from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from intent_agent.checkout import CaptureResult, Checkout, parse_amount
from intent_agent.config import Settings
from intent_agent.paypal import CreatedOrder, PayPalError
from intent_agent.store import Store
from intent_agent.web import create_app


class FakePayPal:
    def __init__(self):
        self.captures = 0
        self.capture_error: PayPalError | None = None
        self.capture_status = "COMPLETED"

    async def create_order(self, **kwargs):
        self.last_create = kwargs
        return CreatedOrder("ORDER1", "https://sandbox.paypal.test/approve?token=ORDER1")

    async def capture_order(self, order_id):
        self.captures += 1
        if self.capture_error:
            raise self.capture_error
        return {"id": order_id, "status": self.capture_status}


@pytest.fixture
def setup():
    paypal = FakePayPal()
    sent: list[tuple[int, str]] = []

    async def notify(chat_id, text):
        sent.append((chat_id, text))

    checkout = Checkout(paypal, Store(":memory:"), notify, "https://agent.example/")
    return checkout, paypal, sent


def test_parse_amount():
    assert parse_amount("$12.5") == Decimal("12.50")
    for bad in ["", "abc", "0", "-3", "1000.01"]:
        with pytest.raises(ValueError):
            parse_amount(bad)


async def test_start_uses_public_return_urls(setup):
    checkout, paypal, _ = setup
    url = await checkout.start(42, Decimal("9.99"), "Socks")
    assert url.endswith("token=ORDER1")
    assert paypal.last_create["return_url"] == "https://agent.example/paypal/return"
    assert paypal.last_create["cancel_url"] == "https://agent.example/paypal/cancel"
    assert checkout.store.get_order("ORDER1").status == "CREATED"


async def test_complete_captures_once_and_notifies(setup):
    checkout, paypal, sent = setup
    await checkout.start(42, Decimal("9.99"), "Socks")

    assert await checkout.complete("ORDER1") is CaptureResult.COMPLETED
    assert await checkout.complete("ORDER1") is CaptureResult.ALREADY_DONE

    assert paypal.captures == 1
    assert checkout.store.get_order("ORDER1").status == "COMPLETED"
    assert len(sent) == 1 and sent[0][0] == 42 and "9.99" in sent[0][1]


async def test_already_captured_counts_as_completed(setup):
    checkout, paypal, _ = setup
    await checkout.start(42, Decimal("1"), "X")
    paypal.capture_error = PayPalError("x", 422, {"details": [{"issue": "ORDER_ALREADY_CAPTURED"}]})
    assert await checkout.complete("ORDER1") is CaptureResult.COMPLETED


async def test_capture_failure(setup):
    checkout, paypal, sent = setup
    await checkout.start(42, Decimal("1"), "X")
    paypal.capture_error = PayPalError("x", 422, {"details": [{"issue": "INSTRUMENT_DECLINED"}]})
    assert await checkout.complete("ORDER1") is CaptureResult.FAILED
    assert checkout.store.get_order("ORDER1").status == "FAILED"
    assert "did not go through" in sent[-1][1]


async def test_unknown_order(setup):
    checkout, _, _ = setup
    assert await checkout.complete("NOPE") is CaptureResult.UNKNOWN_ORDER


async def test_cancel(setup):
    checkout, _, sent = setup
    await checkout.start(42, Decimal("1"), "X")
    await checkout.cancel("ORDER1")
    assert checkout.store.get_order("ORDER1").status == "CANCELLED"
    assert await checkout.complete("ORDER1") is CaptureResult.ALREADY_DONE
    assert "cancelled" in sent[-1][1]


def test_return_page(setup):
    checkout, paypal, sent = setup
    settings = Settings(telegram_bot_token="1:x", paypal_client_id="i", paypal_client_secret="s")
    with TestClient(create_app(settings, checkout=checkout)) as http:
        assert http.get("/healthz").json() == {"ok": True}
        assert http.get("/paypal/return", params={"token": "NOPE"}).status_code == 404

        http.portal.call(checkout.start, 42, Decimal("5"), "Tea")
        resp = http.get("/paypal/return", params={"token": "ORDER1", "PayerID": "P"})
        assert resp.status_code == 200 and "Payment complete" in resp.text
        assert "Already processed" in http.get("/paypal/return", params={"token": "ORDER1"}).text
    assert paypal.captures == 1
