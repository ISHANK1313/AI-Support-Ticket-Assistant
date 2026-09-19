"""Offline HTTP boundary tests; no API server or credentials required."""
from decimal import Decimal

import pytest
import requests

from src.ui_client import APIClient, APIError, ticket_payload


class Response:
    def __init__(self, status, body):
        self.status_code, self.body = status, body

    def json(self):
        return self.body


class Transport:
    def __init__(self, *responses):
        self.responses = iter(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        result = next(self.responses)
        if isinstance(result, Exception):
            raise result
        return result


def test_ticket_post_preserves_unknown_zero_false_and_decimal():
    transport = Transport(Response(201, {"id": 1}))
    client = APIClient(token="session-token", transport=transport)
    payload = ticket_payload(message="Help", order_value_inr=Decimal("0.10"),
                             days_since_delivery=0, original_item_available=False)
    assert client.create_ticket(payload) == {"id": 1}
    method, url, options = transport.calls[0]
    assert (method, url) == ("POST", "http://127.0.0.1:8000/tickets")
    assert options["headers"]["Authorization"] == "Bearer session-token"
    assert options["timeout"] == 160
    assert options["allow_redirects"] is False
    assert options["json"]["order_value_inr"] == "0.10"
    assert options["json"]["days_since_delivery"] == 0
    assert options["json"]["days_since_dispatch"] is None
    assert options["json"]["original_item_available"] is False


@pytest.mark.parametrize("body", [{"detail": {"code": "invalid_token", "message": "Expired"}},
                                  {"detail": "Not authenticated"}])
def test_401_clears_token_and_notifies_session(body):
    cleared = []
    client = APIClient(token="old", transport=Transport(Response(401, body)),
                       on_unauthorized=lambda: cleared.append(True))
    with pytest.raises(APIError, match="sign in") as caught:
        client.me()
    assert caught.value.status == 401
    assert client.token is None
    assert cleared == [True]


def test_auth_json_and_owner_scoped_routes():
    transport = Transport(Response(201, {"id": 2}),
                          Response(200, {"access_token": "new", "token_type": "bearer", "expires_in": 3600}),
                          Response(200, {"id": 2}), Response(200, {"items": [], "limit": 20, "offset": 0}),
                          Response(200, {"id": 3}))
    client = APIClient("http://localhost:8000/", transport=transport)
    client.register("a@example.com", "password")
    client.login("a@example.com", "password")
    client.me()
    client.list_tickets()
    client.get_ticket(3)
    assert transport.calls[0][2]["json"] == {"email": "a@example.com", "password": "password"}
    assert "Authorization" not in transport.calls[1][2]["headers"]
    assert transport.calls[2][2]["headers"]["Authorization"] == "Bearer new"
    assert transport.calls[3][2]["params"] == {"limit": 20, "offset": 0}
    assert transport.calls[4][1].endswith("/tickets/3")


def test_timeout_is_not_retried_and_warns_of_uncertain_save():
    transport = Transport(requests.Timeout("secret internal context"))
    client = APIClient(token="a", transport=transport)
    with pytest.raises(APIError, match="History") as caught:
        client.create_ticket(ticket_payload(message="help"))
    assert len(transport.calls) == 1
    assert "secret" not in str(caught.value)


def test_provider_failure_remains_error_and_keeps_session():
    client = APIClient(token="a", transport=Transport(Response(503, {
        "detail": {"code": "index_unavailable", "message": "Run ingestion first."}})))
    with pytest.raises(APIError, match="Run ingestion first") as caught:
        client.create_ticket(ticket_payload(message="help"))
    assert caught.value.code == "index_unavailable"
    assert client.token == "a"


def test_payload_rejects_non_input_keys():
    with pytest.raises(ValueError):
        ticket_payload(message="help", user_id=3)


def test_protected_call_without_login_never_uses_network():
    transport = Transport()
    with pytest.raises(APIError, match="sign in"):
        APIClient(transport=transport).list_tickets()
    assert not transport.calls


def test_streamlit_session_rerender_logout_and_history(monkeypatch):
    from pathlib import Path
    from streamlit.testing.v1 import AppTest
    import src.ui_client as module

    detail = {"id": 1, "message": "Help", "facts": {}, "created_at": "today",
              "decision": {"action": "NEEDS_MORE_INFORMATION", "confidence": 0.5,
                           "reason": "Which item?", "sources": []}}
    transport = Transport(Response(200, {"access_token": "new", "token_type": "bearer", "expires_in": 3600}),
                          Response(200, {"email": "a@example.com"}),
                          Response(201, detail),
                          Response(200, {"items": [detail], "limit": 20, "offset": 0}),
                          Response(200, detail))
    monkeypatch.setenv("API_BASE_URL", "http://localhost:9000")
    monkeypatch.setattr(module.requests, "request", transport.request)
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "streamlit_app.py")).run()
    assert not app.exception
    app.text_input(key="login_email").input("a@example.com")
    app.text_input(key="login_password").input("password")
    app.button(key="login_submit").click().run()
    assert not app.exception
    app.radio(key="page").set_value("New decision").run()
    app.text_area(key="message").input("Help")
    app.button(key="ticket_submit").click().run()
    assert not app.exception
    assert transport.calls[2][2]["json"]["order_value_inr"] is None
    app.run()
    assert len(transport.calls) == 3  # A normal rerender must not repeat POST.
    app.radio(key="page").set_value("History").run()
    app.button(key="history_load").click().run()
    app.button(key="history_detail").click().run()
    assert not app.exception
    app.button(key="logout").click().run()
    assert not app.exception
    assert "decision_result" not in app.session_state
    assert "history" not in app.session_state
    assert "detail" not in app.session_state
    assert "token" not in app.session_state
    assert all(call[1].startswith("http://localhost:9000") for call in transport.calls)
