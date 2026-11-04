"""Minimal async client for the PayPal REST API (Orders v2), sandbox by default."""

import time
import uuid
from dataclasses import dataclass
from decimal import Decimal

import httpx


class PayPalError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None, body: object = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


@dataclass(frozen=True)
class CreatedOrder:
    order_id: str
    approve_url: str


@dataclass(frozen=True)
class CreatedSetupToken:
    setup_token_id: str
    approve_url: str


@dataclass(frozen=True)
class SavedPayPal:
    payment_token_id: str  # pass as vault_id when paying later
    customer_id: str
    email: str


class PayPalClient:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        base_url: str,
        http: httpx.AsyncClient | None = None,
    ):
        self._client_id = client_id
        self._client_secret = client_secret
        self._http = http or httpx.AsyncClient(base_url=base_url, timeout=20)
        self._token: str | None = None
        self._token_expires_at = 0.0

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _access_token(self) -> str:
        # Refresh a minute early so a token never expires mid-request.
        if self._token and time.monotonic() < self._token_expires_at - 60:
            return self._token
        resp = await self._http.post(
            "/v1/oauth2/token",
            auth=(self._client_id, self._client_secret),
            data={"grant_type": "client_credentials"},
        )
        body = _json(resp)
        if resp.status_code != 200:
            raise PayPalError("PayPal authentication failed", resp.status_code, body)
        self._token = body["access_token"]
        self._token_expires_at = time.monotonic() + float(body.get("expires_in", 0))
        return self._token

    async def _request(self, method: str, path: str, json: dict | None = None, request_id: str | None = None) -> dict:
        token = await self._access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            # Makes retries safe: PayPal returns the original result for a repeated request id.
            "PayPal-Request-Id": request_id or str(uuid.uuid4()),
        }
        resp = await self._http.request(method, path, json=json, headers=headers)
        body = _json(resp)
        if resp.status_code >= 400:
            raise PayPalError(f"PayPal {method} {path} failed", resp.status_code, body)
        return body

    async def create_order(
        self,
        *,
        amount: Decimal,
        currency: str,
        description: str,
        return_url: str,
        cancel_url: str,
        reference_id: str | None = None,
    ) -> CreatedOrder:
        payload = {
            "intent": "CAPTURE",
            "purchase_units": [
                {
                    "reference_id": reference_id or "default",
                    "description": description[:127],
                    "amount": {"currency_code": currency, "value": f"{amount:.2f}"},
                }
            ],
            "payment_source": {
                "paypal": {
                    "experience_context": {
                        "return_url": return_url,
                        "cancel_url": cancel_url,
                        "user_action": "PAY_NOW",
                        "shipping_preference": "NO_SHIPPING",
                    }
                }
            },
        }
        body = await self._request("POST", "/v2/checkout/orders", json=payload)
        approve_url = _link(body, "payer-action", "approve")
        if not approve_url:
            raise PayPalError("PayPal order has no approval link", body=body)
        return CreatedOrder(order_id=body["id"], approve_url=approve_url)

    async def capture_order(self, order_id: str) -> dict:
        return await self._request("POST", f"/v2/checkout/orders/{order_id}/capture", json={})

    async def get_order(self, order_id: str) -> dict:
        return await self._request("GET", f"/v2/checkout/orders/{order_id}")

    async def refund_capture(self, capture_id: str) -> dict:
        return await self._request("POST", f"/v2/payments/captures/{capture_id}/refund", json={})

    # --- Saved PayPal (vault): buyer approves once, the agent can pay later without them ---

    async def create_setup_token(self, *, return_url: str, cancel_url: str, description: str) -> CreatedSetupToken:
        # After approval PayPal redirects to return_url with ?approval_token_id=<setup token id>
        # (observed in sandbox, 2026-11-03), which identifies the setup token to turn into a payment token.
        payload = {
            "payment_source": {
                "paypal": {
                    "description": description[:127],
                    "permit_multiple_payment_tokens": False,
                    "usage_type": "MERCHANT",
                    "customer_type": "CONSUMER",
                    "usage_pattern": "IMMEDIATE",
                    "experience_context": {
                        "shipping_preference": "NO_SHIPPING",
                        "payment_method_preference": "IMMEDIATE_PAYMENT_REQUIRED",
                        "return_url": return_url,
                        "cancel_url": cancel_url,
                    },
                }
            }
        }
        body = await self._request("POST", "/v3/vault/setup-tokens", json=payload)
        approve_url = _link(body, "approve")
        if not approve_url:
            raise PayPalError("PayPal setup token has no approval link", body=body)
        return CreatedSetupToken(setup_token_id=body["id"], approve_url=approve_url)

    async def get_setup_token(self, setup_token_id: str) -> dict:
        return await self._request("GET", f"/v3/vault/setup-tokens/{setup_token_id}")

    async def create_payment_token(self, setup_token_id: str) -> SavedPayPal:
        """Turn an approved setup token into a saved payment method."""
        body = await self._request(
            "POST",
            "/v3/vault/payment-tokens",
            json={"payment_source": {"token": {"id": setup_token_id, "type": "SETUP_TOKEN"}}},
        )
        return SavedPayPal(
            payment_token_id=body["id"],
            customer_id=(body.get("customer") or {}).get("id", ""),
            email=((body.get("payment_source") or {}).get("paypal") or {}).get("email_address", ""),
        )

    async def delete_payment_token(self, payment_token_id: str) -> None:
        await self._request("DELETE", f"/v3/vault/payment-tokens/{payment_token_id}")

    async def charge_saved(
        self,
        *,
        payment_token_id: str,
        amount: Decimal,
        currency: str,
        description: str,
        reference_id: str | None = None,
        request_id: str | None = None,
    ) -> dict:
        """Pay with a saved PayPal method, with no buyer present. Returns the final order.

        Pass a `request_id` unique to the purchase: PayPal then treats a retry as the same payment.
        """
        payload = {
            "intent": "CAPTURE",
            "purchase_units": [
                {
                    "reference_id": reference_id or "default",
                    "description": description[:127],
                    "amount": {"currency_code": currency, "value": f"{amount:.2f}"},
                }
            ],
            "payment_source": {"paypal": {"vault_id": payment_token_id}},
        }
        order = await self._request("POST", "/v2/checkout/orders", json=payload, request_id=request_id)
        # PayPal's docs show vaulted orders coming back COMPLETED; capture explicitly if not.
        if order.get("status") == "APPROVED":
            order = await self.capture_order(order["id"])
        return order


def capture_id_of(order: dict) -> str | None:
    """The capture id in a completed order, needed for refunds."""
    for unit in order.get("purchase_units", []):
        for capture in (unit.get("payments") or {}).get("captures", []):
            return capture.get("id")
    return None


def _json(resp: httpx.Response) -> dict:
    if not resp.content:  # e.g. 204 from DELETE
        return {}
    try:
        return resp.json()
    except ValueError:
        return {"raw": resp.text}


def _link(body: dict, *rels: str) -> str | None:
    return next((link["href"] for link in body.get("links", []) if link.get("rel") in rels), None)
