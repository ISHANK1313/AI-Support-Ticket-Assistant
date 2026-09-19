"""Pydantic request/response schemas and the action vocabulary."""
from decimal import Decimal
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Action(str, Enum):
    """Full decision vocabulary: the 15 observed historical actions plus the
    refund-only branch documented in refrence.md (A03)."""

    APPROVE_REFUND_OR_REPLACEMENT = "APPROVE_REFUND_OR_REPLACEMENT"
    APPROVE_REPLACEMENT = "APPROVE_REPLACEMENT"
    APPROVE_RETURN = "APPROVE_RETURN"
    CANCEL_AND_REFUND = "CANCEL_AND_REFUND"
    CANNOT_CANCEL_AFTER_DISPATCH = "CANNOT_CANCEL_AFTER_DISPATCH"
    NEEDS_MORE_INFORMATION = "NEEDS_MORE_INFORMATION"
    OFFER_REPLACEMENT_OR_REFUND = "OFFER_REPLACEMENT_OR_REFUND"
    OFFER_REFUND = "OFFER_REFUND"
    OPEN_SHIPPING_INVESTIGATION = "OPEN_SHIPPING_INVESTIGATION"
    REJECT_FOOD_RETURN = "REJECT_FOOD_RETURN"
    REJECT_OPENED_ITEM = "REJECT_OPENED_ITEM"
    REJECT_OUTSIDE_WINDOW = "REJECT_OUTSIDE_WINDOW"
    REPLACE_CORRECT_ITEM = "REPLACE_CORRECT_ITEM"
    REQUEST_DEFECT_EVIDENCE = "REQUEST_DEFECT_EVIDENCE"
    REQUEST_PHOTOS = "REQUEST_PHOTOS"
    WAIT_AND_TRACK = "WAIT_AND_TRACK"


class TicketInput(BaseModel):
    """Submitted ticket facts. Mirrors sample_test_cases.json fields; historical
    issue_type / resolved_action are deliberately absent."""

    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=5000)
    order_value_inr: Decimal | None = Field(
        default=None, ge=Decimal("0"), decimal_places=2
    )
    days_since_delivery: int | None = Field(default=None, ge=0, strict=True)
    days_since_dispatch: int | None = Field(default=None, ge=0, strict=True)
    product_type: Literal["food", "non_food", "mixed", "unknown"] = "unknown"
    opened_status: Literal["opened", "unopened", "unknown"] = "unknown"
    order_status: Literal["processing", "dispatched", "delivered", "unknown"] = "unknown"
    ordered_item: str | None = Field(default=None, min_length=1, max_length=200)
    received_item: str | None = Field(default=None, min_length=1, max_length=200)
    original_item_available: bool | None = Field(default=None, strict=True)

    @field_validator("message")
    @classmethod
    def message_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message must not be blank")
        return value.strip()

    @field_validator("days_since_delivery", "days_since_dispatch")
    @classmethod
    def days_not_boolean(cls, value):
        if isinstance(value, bool):
            raise ValueError("day fields must be integers, not booleans")
        return value

    @field_validator("ordered_item", "received_item")
    @classmethod
    def optional_text_not_blank(cls, value):
        if value is not None and not value.strip():
            raise ValueError("optional item fields must not be blank when provided")
        return value

    @model_validator(mode="after")
    def dispatch_consistency(self):
        if (self.order_status == "processing" and self.days_since_dispatch is not None
                and self.days_since_dispatch > 0):
            raise ValueError("a processing order cannot have a dispatch age")
        return self


class DecisionOutput(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    action: Action
    confidence: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    reason: str = Field(min_length=1, max_length=4000)
    sources: list[str] = Field(default_factory=list, max_length=20)


class UserPublic(BaseModel):
    id: int
    email: str
    created_at: str


class TicketDetail(BaseModel):
    id: int
    message: str
    facts: dict
    created_at: str
    decision: DecisionOutput | None


class TicketListResponse(BaseModel):
    items: list[TicketDetail]
    limit: int
    offset: int
