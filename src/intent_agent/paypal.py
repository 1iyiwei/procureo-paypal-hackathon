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

    async def _request(self, method: str, path: str, json: dict | None = None) -> dict:
        token = await self._access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            # Makes retries safe: PayPal returns the original result for a repeated request id.
            "PayPal-Request-Id": str(uuid.uuid4()),
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
        approve_url = next(
            (link["href"] for link in body.get("links", []) if link["rel"] in ("payer-action", "approve")),
            None,
        )
        if not approve_url:
            raise PayPalError("PayPal order has no approval link", body=body)
        return CreatedOrder(order_id=body["id"], approve_url=approve_url)

    async def capture_order(self, order_id: str) -> dict:
        return await self._request("POST", f"/v2/checkout/orders/{order_id}/capture", json={})

    async def get_order(self, order_id: str) -> dict:
        return await self._request("GET", f"/v2/checkout/orders/{order_id}")


def _json(resp: httpx.Response) -> dict:
    try:
        return resp.json()
    except ValueError:
        return {"raw": resp.text}
