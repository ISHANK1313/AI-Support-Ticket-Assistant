"""HTTP integration tests use synthetic users and an injected decision provider."""
from contextlib import contextmanager
from pathlib import Path
import sqlite3

from fastapi.testclient import TestClient

from src.api import create_app
from src.config import Settings
from src.schemas import Action, DecisionOutput


class FakeDecisionService:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def decide(self, ticket):
        self.calls.append(ticket)
        if self.error:
            raise self.error
        return DecisionOutput(action=Action.REQUEST_PHOTOS, confidence=0.9,
                              reason="Photos required for this damaged order.",
                              sources=["damaged_goods.md"])


@contextmanager
def client_with(tmp_path, service=None):
    settings = Settings("", "test", "test", "test-secret-" * 5, 60,
                        tmp_path / "api.db", Path(__file__).resolve().parents[1] / "knowledge_base")
    with TestClient(create_app(settings, decision_service=service)) as client:
        yield client, settings


def login(client, email="alice@example.com"):
    credentials = {"email": email, "password": "a-secure-password"}
    assert client.post("/register", json=credentials).status_code == 201
    response = client.post("/login", json=credentials)
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_submit_persists_ticket_and_decision(tmp_path):
    service = FakeDecisionService()
    with client_with(tmp_path, service) as (client, settings):
        headers = login(client)
        payload = {"message": "Arrived damaged", "order_value_inr": "3500.25",
                   "days_since_delivery": 0, "order_status": "delivered"}
        response = client.post("/tickets", json=payload, headers=headers)
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["decision"]["action"] == "REQUEST_PHOTOS"
        assert body["facts"]["order_value_inr"] == "3500.25"
        assert body["facts"]["days_since_delivery"] == 0
        assert body["facts"]["days_since_dispatch"] is None
        assert client.get(f"/tickets/{body['id']}", headers=headers).json() == body
        listing = client.get("/tickets", headers=headers).json()
        assert listing["items"] == [body]
        assert len(service.calls) == 1


def test_cross_user_access_denied(tmp_path):
    with client_with(tmp_path, FakeDecisionService()) as (client, _):
        alice, bob = login(client), login(client, "bob@example.com")
        response = client.post("/tickets", json={"message": "Bob's issue"}, headers=bob)
        assert response.status_code == 201, response.text
        ticket_id = response.json()["id"]
        assert client.get(f"/tickets/{ticket_id}", headers=alice).status_code == 404
        assert client.get("/tickets", headers=alice).json()["items"] == []
        assert client.get(f"/tickets/{ticket_id}", headers=bob).status_code == 200


def test_duplicate_email_and_password_storage(tmp_path):
    with client_with(tmp_path) as (client, settings):
        login(client)
        response = client.post("/register", json={"email": "ALICE@example.com", "password": "another-pass"})
        assert response.status_code == 409
        with sqlite3.connect(settings.database_path) as conn:
            stored = conn.execute("SELECT password_hash FROM users").fetchone()[0]
        assert stored.startswith("$argon2")
        assert stored != "a-secure-password"
        assert client.post("/login", json={"email": "alice@example.com", "password": "incorrect-pass"}).status_code == 401


def test_provider_failure_not_saved(tmp_path):
    from src.decision import PipelineError
    with client_with(tmp_path, FakeDecisionService(PipelineError("Unavailable"))) as (client, _):
        headers = login(client)
        response = client.post("/tickets", json={"message": "Broken thing"}, headers=headers)
        assert response.status_code == 503
        assert client.get("/tickets", headers=headers).json()["items"] == []


def test_authentication_precedes_generation_and_pagination_is_bounded(tmp_path):
    service = FakeDecisionService()
    with client_with(tmp_path, service) as (client, _):
        assert client.post("/tickets", json={"message": "Hello"}).status_code == 401
        assert service.calls == []
        headers = login(client)
        assert client.get("/tickets?limit=101", headers=headers).status_code == 422
        assert client.get("/tickets?offset=-1", headers=headers).status_code == 422
        assert client.post("/tickets", json={"message": "Hello", "user_id": 42}, headers=headers).status_code == 422
