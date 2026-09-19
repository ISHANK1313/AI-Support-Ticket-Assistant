"""Offline evaluator tests. Fakes verify plumbing, not Gemini quality."""
import json
from decimal import Decimal
from io import StringIO

import pytest

from eval.run_eval import evaluate, load_rows, main, select_input
from src.schemas import Action, DecisionOutput, TicketInput


def decision(action=Action.WAIT_AND_TRACK):
    return DecisionOutput(action=action, confidence=0.7, reason="Policy explanation", sources=["shipping.md"])


def test_exact_allowlist_excludes_all_metadata_and_preserves_null_zero_false():
    row = dict(message="Help", order_value_inr="0.10", days_since_delivery=0,
               days_since_dispatch=None, original_item_available=False, case_id="S01",
               ticket_id=3, customer_id="private", customer_name="private", user_id=9,
               email="private", expected_action="WAIT_AND_TRACK", resolved_action="WAIT_AND_TRACK",
               issue_type="shipping", unexpected="also excluded")
    payload = select_input(row)
    assert set(payload) == {"message", "order_value_inr", "days_since_delivery", "days_since_dispatch", "original_item_available"}
    ticket = TicketInput(**payload)
    assert ticket.order_value_inr == Decimal("0.10")
    assert ticket.days_since_delivery == 0
    assert ticket.days_since_dispatch is None
    assert ticket.original_item_available is False


def test_errors_in_denominator_and_continue_after_failure():
    seen = []
    def decide(ticket):
        assert isinstance(ticket, TicketInput)
        seen.append(ticket)
        if ticket.message == "error":
            raise RuntimeError("secret provider diagnostic")
        return decision()
    rows = [{"message": "match", "expected_action": "WAIT_AND_TRACK"},
            {"message": "mismatch", "expected_action": "APPROVE_RETURN"},
            {"message": "error", "expected_action": "WAIT_AND_TRACK"}]
    out = StringIO()
    summary = evaluate(rows, decide, output=out)
    assert summary == {"total": 3, "correct": 1, "incorrect": 1, "errors": 1, "accuracy": 1 / 3}
    assert len(seen) == 3
    assert "secret" not in out.getvalue()
    assert "33.33%" in out.getvalue()


def test_invalid_input_and_missing_label_are_errors_without_decision_call():
    def forbidden(_):
        pytest.fail("Invalid rows must not reach the provider")
    result = evaluate([{"message": "", "expected_action": "WAIT_AND_TRACK"}, {"message": "no label"}], forbidden, output=StringIO())
    assert result["errors"] == result["total"] == 2
    assert result["accuracy"] == 0


def test_empty_cases_has_defined_zero_accuracy():
    assert evaluate([], lambda _: decision(), output=StringIO())["accuracy"] == 0


def test_csv_loads_nulls_and_uses_only_allowed_input(tmp_path):
    path = tmp_path / "tickets.csv"
    path.write_text("ticket_id,customer_name,message,order_value_inr,days_since_delivery,days_since_dispatch,original_item_available,issue_type,resolved_action\n1,Private,Help,0.10,,0,false,shipping,WAIT_AND_TRACK\n", encoding="utf-8")
    rows = load_rows(path, csv_mode=True)
    ticket = TicketInput(**select_input(rows[0]))
    assert ticket.days_since_delivery is None
    assert ticket.days_since_dispatch == 0
    assert ticket.original_item_available is False
    out = StringIO()
    result = evaluate(rows, lambda _: decision(), label_key="resolved_action", output=out)
    assert result["correct"] == 1


def test_cli_csv_is_diagnostic_not_held_out(tmp_path, monkeypatch, capsys):
    from eval import run_eval
    path = tmp_path / "tickets.csv"
    path.write_text("message,resolved_action\nHelp,WAIT_AND_TRACK\n", encoding="utf-8")
    monkeypatch.setattr(run_eval, "live_decider", lambda: lambda _: decision())
    assert main(["--csv", str(path)]) == 0
    output = capsys.readouterr().out
    assert "diagnostic label agreement" in output.lower()
    assert "not held-out" in output.lower()


def test_initialization_failure_counts_every_case(tmp_path, monkeypatch, capsys):
    from eval import run_eval
    path = tmp_path / "cases.json"
    path.write_text(json.dumps([{"message": "Help", "expected_action": "WAIT_AND_TRACK"}]))
    def fail():
        raise RuntimeError("secret")
    monkeypatch.setattr(run_eval, "live_decider", fail)
    assert main(["--cases", str(path)]) == 1
    output = capsys.readouterr().out
    assert "errors=1" in output and "total=1" in output
    assert "secret" not in output


def test_cli_requires_exclusive_source():
    with pytest.raises(SystemExit):
        main(["--cases", "a.json", "--csv", "b.csv"])
