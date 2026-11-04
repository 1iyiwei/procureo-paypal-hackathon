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


@respx.mock
async def test_save_paypal_flow(client):
    mock_token(respx)
    setup = respx.post(f"{BASE}/v3/vault/setup-tokens").respond(
        200,
        json={
            "id": "SETUP1",
            "status": "PAYER_ACTION_REQUIRED",
            "links": [{"rel": "approve", "href": "https://www.sandbox.paypal.com/agreements/approve?approval_session_id=SETUP1"}],
        },
    )
    respx.post(f"{BASE}/v3/vault/payment-tokens").respond(
        200,
        json={
            "id": "PT1",
            "customer": {"id": "CUST1"},
            "payment_source": {"paypal": {"email_address": "buyer@example.com"}},
        },
    )

    created = await client.create_setup_token(return_url="http://x/r", cancel_url="http://x/c", description="d")
    assert created.setup_token_id == "SETUP1" and "approve" in created.approve_url
    sent = httpx.Response(200, content=setup.calls.last.request.content).json()["payment_source"]["paypal"]
    assert sent["usage_type"] == "MERCHANT" and sent["experience_context"]["return_url"] == "http://x/r"

    saved = await client.create_payment_token("SETUP1")
    assert (saved.payment_token_id, saved.customer_id, saved.email) == ("PT1", "CUST1", "buyer@example.com")
    body = httpx.Response(200, content=respx.calls.last.request.content).json()
    assert body == {"payment_source": {"token": {"id": "SETUP1", "type": "SETUP_TOKEN"}}}


@respx.mock
async def test_charge_saved_completes_in_one_call(client):
    mock_token(respx)
    create = respx.post(f"{BASE}/v2/checkout/orders").respond(201, json={"id": "O1", "status": "COMPLETED"})
    capture = respx.post(f"{BASE}/v2/checkout/orders/O1/capture")

    order = await client.charge_saved(payment_token_id="PT1", amount=Decimal("164"), currency="USD", description="x")

    assert order["status"] == "COMPLETED"
    body = httpx.Response(200, content=create.calls.last.request.content).json()
    assert body["payment_source"] == {"paypal": {"vault_id": "PT1"}}
    assert body["purchase_units"][0]["amount"]["value"] == "164.00"
    assert not capture.called


@respx.mock
async def test_charge_saved_captures_if_only_approved(client):
    mock_token(respx)
    respx.post(f"{BASE}/v2/checkout/orders").respond(201, json={"id": "O1", "status": "APPROVED"})
    respx.post(f"{BASE}/v2/checkout/orders/O1/capture").respond(201, json={"id": "O1", "status": "COMPLETED"})
    order = await client.charge_saved(payment_token_id="PT1", amount=Decimal("1"), currency="USD", description="x")
    assert order["status"] == "COMPLETED"


@respx.mock
async def test_delete_payment_token_handles_empty_response(client):
    mock_token(respx)
    route = respx.delete(f"{BASE}/v3/vault/payment-tokens/PT1").respond(204)
    await client.delete_payment_token("PT1")
    assert route.called
