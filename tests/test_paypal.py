from decimal import Decimal

import httpx
import pytest
import respx

from intent_agent.paypal import PayPalClient, PayPalError

BASE = "https://api-m.sandbox.paypal.com"


@pytest.fixture
def client():
    return PayPalClient("id", "secret", BASE)


def mock_token(router: respx.MockRouter):
    return router.post(f"{BASE}/v1/oauth2/token").respond(200, json={"access_token": "tok", "expires_in": 3600})


@respx.mock
async def test_create_order_returns_approval_link(client):
    token = mock_token(respx)
    create = respx.post(f"{BASE}/v2/checkout/orders").respond(
        200,
        json={
            "id": "ORDER1",
            "status": "PAYER_ACTION_REQUIRED",
            "links": [
                {"rel": "self", "href": f"{BASE}/v2/checkout/orders/ORDER1"},
                {"rel": "payer-action", "href": "https://www.sandbox.paypal.com/checkoutnow?token=ORDER1"},
            ],
        },
    )

    order = await client.create_order(
        amount=Decimal("12.5"),
        currency="USD",
        description="Coffee",
        return_url="http://x/return",
        cancel_url="http://x/cancel",
    )

    assert order.order_id == "ORDER1"
    assert order.approve_url.endswith("token=ORDER1")
    sent = create.calls.last.request
    assert sent.headers["Authorization"] == "Bearer tok"
    assert "PayPal-Request-Id" in sent.headers
    body = httpx.Response(200, content=sent.content).json()
    assert body["purchase_units"][0]["amount"] == {"currency_code": "USD", "value": "12.50"}
    assert body["payment_source"]["paypal"]["experience_context"]["return_url"] == "http://x/return"
    assert token.call_count == 1


@respx.mock
async def test_token_is_reused(client):
    token = mock_token(respx)
    respx.get(f"{BASE}/v2/checkout/orders/A").respond(200, json={"id": "A"})
    await client.get_order("A")
    await client.get_order("A")
    assert token.call_count == 1


@respx.mock
async def test_error_carries_paypal_details(client):
    mock_token(respx)
    respx.post(f"{BASE}/v2/checkout/orders/A/capture").respond(
        422, json={"name": "UNPROCESSABLE_ENTITY", "details": [{"issue": "ORDER_NOT_APPROVED"}]}
    )
    with pytest.raises(PayPalError) as e:
        await client.capture_order("A")
    assert e.value.status_code == 422
    assert e.value.body["details"][0]["issue"] == "ORDER_NOT_APPROVED"


@respx.mock
async def test_auth_failure(client):
    respx.post(f"{BASE}/v1/oauth2/token").respond(401, json={"error": "invalid_client"})
    with pytest.raises(PayPalError, match="authentication"):
        await client.get_order("A")
