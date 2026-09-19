"""FastAPI app factory and the six required REST endpoints."""
import json
import re
import sqlite3
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from . import database
from .auth import create_access_token, hash_password, make_auth_dependency, verify_password
from .config import Settings
from .schemas import TicketInput, UserPublic


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(max_length=254)
    password: str = Field(min_length=8, max_length=128)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value):
        value = value.strip().lower()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
            raise ValueError("Invalid email address")
        return value


def detail(row):
    return {
        "id": row["id"], "message": row["message"],
        "facts": json.loads(row["facts_json"]), "created_at": row["created_at"],
        "decision": {"action": row["action"], "confidence": row["confidence"],
                     "reason": row["reason"], "sources": json.loads(row["sources"])},
    }


def create_app(settings: Settings | None = None, decision_service=None):
    @asynccontextmanager
    async def lifespan(app):
        configured = settings or Settings.load()
        database.init_db(configured.database_path)
        app.state.settings = configured
        app.state.auth = make_auth_dependency(configured)
        app.state.decision_service = decision_service
        yield

    app = FastAPI(title="Support Ticket Decision API", lifespan=lifespan)

    @app.exception_handler(RequestValidationError)
    async def invalid_input(request, exc):
        return JSONResponse(status_code=422, content={"detail": {
            "code": "INVALID_INPUT", "message": "Invalid request fields or values"}})

    @app.exception_handler(sqlite3.Error)
    async def database_error(request, exc):
        return JSONResponse(status_code=503, content={"detail": {
            "code": "DATABASE_UNAVAILABLE", "message": "Database operation failed"}})

    # A static dependency keeps Bearer authentication visible in OpenAPI.
    from fastapi.security import HTTPBearer
    bearer = HTTPBearer(auto_error=False)

    def current_user(credentials=Depends(bearer)):
        return app.state.auth(credentials)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.post("/register", status_code=201, response_model=UserPublic)
    def register(body: Credentials):
        hashed = hash_password(body.password)
        try:
            with database.get_conn(app.state.settings.database_path) as conn:
                user_id = database.insert_user(conn, body.email, hashed)
                return dict(database.get_user_by_id(conn, user_id))
        except sqlite3.IntegrityError:
            raise HTTPException(409, {"code": "EMAIL_EXISTS", "message": "Email already registered"})

    @app.post("/login")
    def login(body: Credentials):
        with database.get_conn(app.state.settings.database_path) as conn:
            user = database.get_user_by_email(conn, body.email)
        if user is None or not verify_password(body.password, user["password_hash"]):
            raise HTTPException(401, {"code": "INVALID_CREDENTIALS", "message": "Invalid credentials"})
        token, expires = create_access_token(user["id"], app.state.settings)
        return {"access_token": token, "token_type": "bearer", "expires_in": expires}

    @app.get("/me", response_model=UserPublic)
    def me(user=Depends(current_user)):
        with database.get_conn(app.state.settings.database_path) as conn:
            return dict(database.get_user_by_id(conn, user.id))

    @app.post("/tickets", status_code=201)
    def submit_ticket(body: TicketInput, user=Depends(current_user)):
        from .decision import DecisionService, PipelineError
        try:
            service = app.state.decision_service or DecisionService(app.state.settings)
            result = service.decide(body)
        except PipelineError as exc:
            raise HTTPException(exc.status_code, {"code": exc.code, "message": str(exc)})
        facts = body.model_dump(mode="json", exclude={"message"})
        with database.get_conn(app.state.settings.database_path) as conn:
            ticket_id = database.insert_ticket_and_decision(
                conn, user.id, body.message, facts, result.model_dump(mode="json"))
            return detail(database.get_ticket_for_user(conn, ticket_id, user.id))

    @app.get("/tickets")
    def list_tickets(limit: int = Query(20, ge=1, le=100),
                     offset: int = Query(0, ge=0), user=Depends(current_user)):
        with database.get_conn(app.state.settings.database_path) as conn:
            rows = database.list_tickets_for_user(conn, user.id, limit, offset)
            return {"items": [detail(row) for row in rows], "limit": limit, "offset": offset}

    @app.get("/tickets/{ticket_id}")
    def ticket_detail(ticket_id: int, user=Depends(current_user)):
        with database.get_conn(app.state.settings.database_path) as conn:
            row = database.get_ticket_for_user(conn, ticket_id, user.id)
        if row is None:
            raise HTTPException(404, {"code": "NOT_FOUND", "message": "Ticket not found"})
        return detail(row)

    return app


app = create_app()
