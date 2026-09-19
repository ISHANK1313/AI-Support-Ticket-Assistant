"""HTTP-only frontend boundary. No backend configuration, database, or AI access."""
from decimal import Decimal
from typing import Callable

import requests


class APIError(Exception):
    def __init__(self, message: str, *, status: int | None = None, code: str = "request_failed"):
        super().__init__(message)
        self.status, self.code = status, code


TICKET_DEFAULTS = {
    "message": "", "order_value_inr": None, "days_since_delivery": None,
    "days_since_dispatch": None, "product_type": "unknown", "opened_status": "unknown",
    "order_status": "unknown", "ordered_item": None, "received_item": None,
    "original_item_available": None,
}


def ticket_payload(**values) -> dict:
    """Keep explicit unknowns and serialize money without binary float conversion."""
    if values.keys() - TICKET_DEFAULTS.keys():
        raise ValueError("Unexpected ticket input field")
    payload = {**TICKET_DEFAULTS, **values}
    if isinstance(payload["order_value_inr"], Decimal):
        payload["order_value_inr"] = str(payload["order_value_inr"])
    return payload


class APIClient:
    """One session's bearer token and an injectable requests-compatible transport.

    API_BASE_URL is trusted operator configuration, never a customer form field.
    Redirects and retries are disabled to avoid credential forwarding/duplicate POSTs.
    """
    def __init__(self, base_url: str = "http://127.0.0.1:8000", *, token: str | None = None,
                 transport=None, on_unauthorized: Callable[[], None] | None = None):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.transport = transport if transport is not None else requests
        self.on_unauthorized = on_unauthorized

    def _unauthorized(self):
        self.token = None
        if self.on_unauthorized:
            self.on_unauthorized()

    def _request(self, method: str, path: str, *, protected=True, **kwargs):
        headers = {"Accept": "application/json"}
        if protected:
            if not self.token:
                self._unauthorized()
                raise APIError("Please sign in to continue.", status=401, code="unauthorized")
            headers["Authorization"] = f"Bearer {self.token}"
        try:
            response = self.transport.request(method, self.base_url + path, headers=headers,
                                              timeout=160, allow_redirects=False, **kwargs)
        except requests.RequestException:
            message = "Cannot reach the API or the request timed out."
            if method == "POST":
                message += " The operation may have completed; check History (or try signing in after registration) before submitting again."
            raise APIError(message, code="connection_error") from None
        if response.status_code == 401:
            self._unauthorized()
            raise APIError("Authentication failed or your session expired; please sign in again.",
                           status=401, code="unauthorized")
        try:
            body = response.json()
        except ValueError:
            raise APIError("The API returned an unreadable response.", status=response.status_code) from None
        if not 200 <= response.status_code < 300:
            detail = body.get("detail") if isinstance(body, dict) else None
            code, message = "request_failed", f"API request failed (HTTP {response.status_code})."
            if isinstance(detail, dict):
                code = detail.get("code", code)
                message = detail.get("message", message)
            elif isinstance(detail, str):
                message = detail
            elif isinstance(detail, list):
                message = "Invalid input. Check required fields, numeric values, and status combinations."
            raise APIError(str(message), status=response.status_code, code=str(code))
        if not isinstance(body, dict):
            raise APIError("The API returned an unexpected response shape.", status=response.status_code)
        return body

    def register(self, email: str, password: str):
        return self._request("POST", "/register", protected=False, json={"email": email, "password": password})

    def login(self, email: str, password: str):
        self.token = None
        result = self._request("POST", "/login", protected=False, json={"email": email, "password": password})
        if not isinstance(result.get("access_token"), str) or not result["access_token"]:
            raise APIError("The API did not return a login token.")
        self.token = result["access_token"]
        return result

    def me(self):
        return self._request("GET", "/me")

    def create_ticket(self, payload: dict):
        return self._request("POST", "/tickets", json=ticket_payload(**payload))

    def list_tickets(self, limit: int = 20, offset: int = 0):
        return self._request("GET", "/tickets", params={"limit": limit, "offset": offset})

    def get_ticket(self, ticket_id: int):
        return self._request("GET", f"/tickets/{int(ticket_id)}")
