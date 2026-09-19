import pytest
from pydantic import ValidationError
from src.schemas import TicketInput, DecisionOutput


@pytest.mark.parametrize('value', [True, 1.0, 1.5, '3'])
def test_days_require_actual_integers(value):
    with pytest.raises(ValidationError):
        TicketInput(message='Hello', days_since_delivery=value)


def test_missing_and_zero_remain_distinct():
    assert TicketInput(message='Hello').days_since_delivery is None
    assert TicketInput(message='Hello', days_since_delivery=0).days_since_delivery == 0


@pytest.mark.parametrize('field,value', [('ordered_item',' '), ('received_item',' '), ('original_item_available','false')])
def test_optional_fields_validated(field, value):
    with pytest.raises(ValidationError):
        TicketInput(message='Hello', **{field:value})


def test_impossible_processing_dispatch_is_rejected():
    with pytest.raises(ValidationError):
        TicketInput(message='Hello', order_status='processing', days_since_dispatch=2)


def test_extra_decision_fields_rejected():
    with pytest.raises(ValidationError):
        DecisionOutput.model_validate_json('{"action":"NEEDS_MORE_INFORMATION","confidence":0.5,"reason":"Clarify","sources":[],"extra":true}')
