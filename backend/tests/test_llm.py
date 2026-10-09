"""LLM tasks with the deterministic mock, and the Anthropic client against a fake SDK client."""

from __future__ import annotations

import json
from datetime import date
from types import SimpleNamespace

import pytest

from app.services.llm import AnthropicLLM, ExtractionContext, MockLLM, RfqEmailContext, template_email

CTX = ExtractionContext(nsn="5305011209897", part_numbers=["MS24693-C274"], quantity=100, unit_of_issue="EA",
                        today=date(2026, 10, 9))


@pytest.mark.parametrize(
    "text,price,lead,conf_min",
    [
        ("Unit price: $12.34 each, lead time 4 weeks ARO. P/N MS24693-C274. Quote valid for 30 days.", 12.34, 28, 0.8),
        ("We can do $0.488/ea, 10 business days, FOB origin, MOQ 500", 0.488, 14, 0.6),
        ("Price is $1,250.00 per unit. In stock, ships in 3 days.", 1250.0, 3, 0.6),
    ],
)
def test_mock_extracts_quotes(text, price, lead, conf_min):
    q = MockLLM().extract_quote(text, CTX)
    assert q.is_quote and q.unit_price == price and q.lead_time_days == lead
    assert q.extraction_confidence >= conf_min


def test_mock_details():
    q = MockLLM().extract_quote(
        "P/N MS24693-C274 at $2.10 each. MOQ 250. Quote valid for 45 days. Shipping $35.00. C of C and full traceability.",
        CTX,
    )
    assert q.quoted_part_number == "MS24693-C274" and q.min_order_qty == 250
    assert q.quote_valid_until == date(2026, 11, 23) and q.freight_cost == 35.0
    assert q.certs_offered == ["CoC", "Traceability"]


def test_mock_decline_and_ambiguous_and_quoted_original():
    d = MockLLM().extract_quote("Sorry, we are unable to quote this item at this time.", CTX)
    assert d.declined and not d.is_quote
    a = MockLLM().extract_quote("About $1.10 for small lots or $0.90 for bigger ones.", CTX)
    assert a.extraction_confidence < 0.6
    q = MockLLM().extract_quote("Price $5.00 each.\n\n> Please quote. Our target is $1.00 each", CTX)
    assert q.unit_price == 5.0


def _email_ctx() -> RfqEmailContext:
    return RfqEmailContext(
        broker_name="Acme Federal", sender_name="Acme Federal", supplier_name="Bolt Co", contact_name="Sam",
        solicitation_number="SPE4A627T0486", nsn="5305011209897", nomenclature="SCREW,MACHINE",
        part_numbers=["MS24693-C274"], quantity=1977, unit_of_issue="EA", required_delivery_date="March 1, 2027",
        fob="ORIGIN", inspection_point="DESTINATION", packaging="MIL-STD-2073-1E", certs_required=["CoC"],
    )


def test_template_email_has_all_required_facts():
    e = template_email(_email_ctx())
    for fact in ("MS24693-C274", "5305-01-120-9897", "1977 EA", "March 1, 2027", "Origin", "destination",
                 "MIL-STD-2073-1E", "CoC", "unit price", "lead time", "minimum order", "freight"):
        assert fact in e.body or fact in e.subject, fact


class FakeMessages:
    def __init__(self, payload: dict | str, stop_reason: str = "end_turn") -> None:
        self.payload = payload
        self.stop_reason = stop_reason
        self.calls: list[dict] = []

    def create(self, **kw):
        self.calls.append(kw)
        text = self.payload if isinstance(self.payload, str) else json.dumps(self.payload)
        return SimpleNamespace(stop_reason=self.stop_reason, content=[SimpleNamespace(type="text", text=text)])


def _anthropic(payload, stop="end_turn") -> tuple[AnthropicLLM, FakeMessages]:
    llm = AnthropicLLM.__new__(AnthropicLLM)
    import anthropic

    llm._anthropic = anthropic
    llm.model = "claude-sonnet-5-5"
    llm.use_fallback = False
    fake = FakeMessages(payload, stop)
    llm.client = SimpleNamespace(messages=fake, beta=SimpleNamespace(messages=fake))
    return llm, fake


def test_anthropic_extraction_uses_json_schema_and_validates():
    llm, fake = _anthropic({
        "is_quote": True, "declined": False, "unit_price": 12.5, "currency": "USD", "quoted_part_number": "MS24693-C274",
        "min_order_qty": None, "lead_time_days": 21, "quote_valid_until": "2026-11-08", "freight_terms": None,
        "freight_cost": None, "certs_offered": ["CoC"], "extraction_confidence": 0.93, "notes": None,
    })
    q = llm.extract_quote("Unit price $12.50", CTX)
    assert q.unit_price == 12.5 and q.quote_valid_until == date(2026, 11, 8)
    call = fake.calls[0]
    assert call["model"] == "claude-sonnet-5-5"
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert "<reply>" in call["messages"][0]["content"]


def test_anthropic_email_falls_back_to_template_when_facts_dropped():
    llm, _ = _anthropic({"subject": "Hello", "body": "Please send a quote."})
    e = llm.draft_rfq_email(_email_ctx())
    assert e == template_email(_email_ctx())


def test_anthropic_refusal_raises_and_email_falls_back():
    llm, _ = _anthropic({"subject": "x", "body": "y"}, stop="refusal")
    with pytest.raises(RuntimeError):
        llm.extract_quote("text", CTX)
    assert llm.draft_rfq_email(_email_ctx()).subject.startswith("RFQ SPE4A627T0486")
