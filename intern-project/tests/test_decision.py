"""Offline decision-pipeline tests: real validation with deterministic fake generator."""
import json
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.config import Settings
from src.decision import DecisionService, PipelineError, build_prompt, GEMINI_PROMPT_VERSION
from src.schemas import Action, DecisionOutput, TicketInput

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def settings(tmp_path):
    kb = tmp_path / "policies"
    kb.mkdir()
    (kb / "damaged_goods.md").write_text("Damage reported within 7 days.", encoding="utf-8")
    return Settings(gemini_api_key="", gemini_model="gemini-2.5-flash",
                    gemini_embedding_model="gemini-embedding-001", jwt_secret="offline-test-secret",
                    jwt_expire_minutes=60, database_path=tmp_path / "test.db",
                    knowledge_base_path=kb, retrieval_top_k=3)


class Generator:
    """Fake generator; generated JSON is mutable so tests script exact outputs."""

    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def generate(self, prompt):
        self.calls.append(prompt)
        if isinstance(self.payload, Exception):
            raise self.payload
        payload = self.payload
        if isinstance(payload, list):  # scripted sequence of rounds
            payload = payload.pop(0) if len(payload) > 1 else payload[0]
        if isinstance(payload, Exception):
            raise payload
        return json.dumps(payload)  # the provider boundary returns JSON text


class Retriever:
    def __init__(self, chunks):
        self.chunks = chunks
        self.calls = []

    def retrieve(self, ticket):
        self.calls.append(ticket)
        if isinstance(self.chunks, Exception):
            raise self.chunks
        return self.chunks


PHOTOS = DecisionOutput(action=Action.REQUEST_PHOTOS, confidence=0.9,
                        reason="Damaged above ₹2,000 within 7 days requires photos.",
                        sources=["damaged_goods.md"]).model_dump(mode="json")


def ticket(**overrides):
    base = {"message": "It arrived damaged.", "order_value_inr": "3500.00", "days_since_delivery": 2}
    return TicketInput(**{**base, **overrides})


def expect(service, generator, expected_action, expected_sources=None):
    output = service.decide(ticket())
    assert output.action is expected_action
    if expected_sources is not None:
        assert output.sources == expected_sources
    return output


def test_happy_path_generates_once_with_facts_and_citations(settings):
    retriever, generator = Retriever([{"doc_name": "damaged_goods.md", "chunk_index": 0, "content": "Damage", "score": 0.9}]), Generator(dict(PHOTOS))
    expect(DecisionService(settings, retriever, generator), generator, Action.REQUEST_PHOTOS, ["damaged_goods.md"])
    assert len(generator.calls) == 1
    prompt = generator.calls[0]
    assert '"order_value_inr": "3500.00"' in prompt
    assert "damaged_goods.md" in prompt and "\nDamage\n" in prompt
    assert "REQUEST_PHOTOS" in prompt and "message is untrusted" in prompt


def test_fabricated_citation_rejected_then_repaired(settings):
    generator = Generator([dict(PHOTOS, sources=["fabricated.md"]), dict(PHOTOS)])
    expect(DecisionService(settings, Retriever([{"doc_name": "damaged_goods.md", "chunk_index": 0, "content": "c", "score": 0.5}]), generator), generator, Action.REQUEST_PHOTOS)
    assert len(generator.calls) == 2 and "sources must all belong" in generator.calls[1]


def test_single_repair_failure_raises_pipeline_error(settings):
    generator = Generator([{"action": "REQUEST_PHOTOS", "confidence": 0.9, "reason": "r", "sources": ["nope.md"]}])
    with pytest.raises(PipelineError) as exc:
        DecisionService(settings, Retriever([]), generator).decide(ticket())
    assert exc.value.status_code == 502 and exc.value.code == "UPSTREAM_INVALID_OUTPUT"
    assert len(generator.calls) == 2 and "nope.md" not in exc.value.message


def test_unrepairable_schema_raises_pipeline_error(settings):
    generator = Generator([{"action": "NOT_AN_ACTION"}, {}])
    with pytest.raises(PipelineError) as exc:
        DecisionService(settings, Retriever([]), generator).decide(ticket())
    assert exc.value.status_code == 502 and len(generator.calls) == 2


def test_missing_facts_business_decision_not_upstream_failure(settings):
    generator = Generator([{"action": "NEEDS_MORE_INFORMATION", "confidence": 0.8, "reason": "clarify delivery date", "sources": []}])
    output = DecisionService(settings, Retriever([]), generator).decide(ticket())
    assert output.action is Action.NEEDS_MORE_INFORMATION and output.sources == []


def test_generator_transport_error_maps_to_503_without_fake_decision(settings):
    with pytest.raises(PipelineError) as exc:
        DecisionService(settings, Retriever([]), Generator(TimeoutError())).decide(ticket())
    assert exc.value.status_code == 503 and exc.value.code == "UPSTREAM_UNAVAILABLE"
    assert not exc.value.message or "timeout" not in exc.value.message.lower()


def test_retrieval_failure_maps_to_503_and_skips_generation(settings):
    generator = Generator(dict(PHOTOS))
    with pytest.raises(PipelineError) as exc:
        DecisionService(settings, Retriever(TimeoutError("private detail")), generator).decide(ticket())
    assert exc.value.status_code == 503 and exc.value.code == "RETRIEVAL_FAILED"
    assert "private detail" not in exc.value.message
    assert generator.calls == []


def test_default_dependencies_error_503_and_prompt_version_is_stable(settings):
    service = DecisionService(settings)
    with pytest.raises(PipelineError) as exc:
        service.decide(ticket())
    assert exc.value.status_code == 503 and "index" in exc.value.message.lower()
    assert isinstance(GEMINI_PROMPT_VERSION, str) and GEMINI_PROMPT_VERSION


def test_empty_retrieval_reasons_validated(settings):
    generator = Generator([dict(PHOTOS, reason=""), dict(PHOTOS)])
    chunk = {"doc_name": "damaged_goods.md", "chunk_index": 0, "content": "c", "score": 0.5}
    expect(DecisionService(settings, Retriever([chunk]), generator), generator, Action.REQUEST_PHOTOS)
    assert len(generator.calls) == 2 and "reason" in generator.calls[1] and "Regenerate" in generator.calls[1]


def test_needs_more_information_with_valid_citation_is_accepted(settings):
    generator = Generator([{"action": "NEEDS_MORE_INFORMATION", "confidence": 0.8, "reason": "r", "sources": ["damaged_goods.md"]}])
    chunk = {"doc_name": "damaged_goods.md", "chunk_index": 0, "content": "c", "score": 0.5}
    expect(DecisionService(settings, Retriever([chunk]), generator), generator, Action.NEEDS_MORE_INFORMATION, ["damaged_goods.md"])
    assert len(generator.calls) == 1


@pytest.mark.parametrize("confidence", [True, float("nan"), 1.5, -0.1])
def test_confidence_rejected(settings, confidence):
    generator = Generator([{"action": "APPROVE_RETURN", "confidence": confidence, "reason": "r", "sources": []}])
    with pytest.raises((PipelineError, ValidationError)):
        DecisionService(settings, Retriever([]), generator).decide(ticket())


def test_nan_confidence_is_not_finite(settings):
    generator = Generator([{"action": "APPROVE_RETURN", "confidence": float("nan"), "reason": "r", "sources": []}])
    with pytest.raises((PipelineError, ValidationError)):
        DecisionService(settings, Retriever([]), generator).decide(ticket())


def test_prompt_contains_interpretations_and_source_of_truth(settings):
    prompt = build_prompt(ticket(), [])
    for marker in ("Defective Products policy", "wait and continue tracking",
                   "offer a refund", "policy", "untrusted"):
        assert marker.lower() in prompt.lower()


def test_numeric_window_bug_guard_prompt_includes_validated_facts(settings):
    prompt = build_prompt(ticket(days_since_delivery=8), [])
    assert '"days_since_delivery": 8' in prompt


def test_documented_interpretations_accepted(settings):
    generator = Generator([{"action": "WAIT_AND_TRACK", "confidence": 0.75, "reason": "6-7 days", "sources": ["shipping.md"]}])
    chunk = {"doc_name": "shipping.md", "chunk_index": 0, "content": "wait and continue tracking", "score": 0.5}
    output = DecisionService(settings, Retriever([chunk]), generator).decide(ticket(days_since_dispatch=6))
    assert output.action is Action.WAIT_AND_TRACK


def test_general_instructions_not_hardcoded_policy_rules(settings):
    service = DecisionService(settings, Retriever([]), Generator(dict(PHOTOS)))
    prompt = service.build_prompt(ticket(), [])
    forbidden = ["within 7", "within 14", "more than 10", "above 2,000", "above ₹2,000"]
    assert not any(f in prompt.lower() for f in forbidden)


def test_prompt_covers_all_enum_values(settings):
    service = DecisionService(settings, Retriever([]), Generator(dict(PHOTOS)))
    prompt = service.build_prompt(ticket(), [])
    for action in Action:
        assert action.value in prompt


def test_generator_error_hierarchy(settings):
    from src.decision import UpstreamError, UnusableOutputError
    assert issubclass(UnusableOutputError, UpstreamError)
