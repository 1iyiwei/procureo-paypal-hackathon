"""Real products and prices from Channel3, for chats in live mode.

Payments stay in the PayPal sandbox, so a purchase here is a test: the money is fake and nothing ships.
Channel3 doesn't report shipping times, so arrival dates are estimates.
"""

from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

import httpx

from .catalog import Product, Quote

CHANNEL3_URL = "https://api.trychannel3.com"
ESTIMATED_SHIP_DAYS = 5
SEARCH_LIMIT = 10
NEAR_BUDGET = Decimal("1.25")  # also consider items a little over the limit, since their price may drop


class Channel3Error(Exception):
    pass


class LiveCatalog:
    def __init__(self, api_key: str, currency: str = "USD", base_url: str = CHANNEL3_URL):
        self._http = httpx.AsyncClient(base_url=base_url, headers={"x-api-key": api_key}, timeout=30.0)
        self.currency = currency

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        try:
            response = await self._http.request(method, path, **kwargs)
        except httpx.HTTPError as e:
            raise Channel3Error(str(e)) from e
        if response.status_code >= 400 and response.status_code != 404:
            raise Channel3Error(f"{response.status_code}: {response.text[:200]}")
        return response

    async def search(self, query: str, max_price: Decimal | None = None) -> list[Product]:
        """Products that may fit a request, with at least one offer we could quote. Each call costs one credit.

        Results lean expensive, so look near the budget first, and more widely only if that finds nothing.
        """
        products = []
        if max_price is not None:
            ceiling = float(max_price * NEAR_BUDGET)
            products = await self._search({"query": query, "limit": SEARCH_LIMIT, "filters": {"price": {"max_price": ceiling}}})
        return products or await self._search({"query": query, "limit": SEARCH_LIMIT})

    async def _search(self, body: dict) -> list[Product]:
        response = await self._request("POST", "/v1/search", json=body)
        seen, products = set(), []
        for data in response.json().get("products", []):
            offers = self._offers(data)
            # The catalog can list one item several times, so show it only once per store.
            key = (" ".join((data.get("title") or data["id"]).lower().split()), offers[0][0]["domain"]) if offers else None
            if key and key not in seen:
                seen.add(key)
                products.append(_product(data))
        return products

    async def quotes(self, product_ids: list[str], now: datetime) -> list[Quote]:
        """What the products cost right now. Product lookups are free. Raises Channel3Error if a check failed."""
        quotes = []
        for product_id in product_ids:
            response = await self._request("GET", f"/v1/products/{product_id}")
            if response.status_code == 404:  # no longer listed
                continue
            data = response.json()
            arrives_on = (now + timedelta(days=ESTIMATED_SHIP_DAYS)).date()
            quotes += [
                Quote(_product(data), offer["domain"], price, arrives_on, url=offer["url"], live=True)
                for offer, price in self._offers(data)
            ]
        return quotes

    def _offers(self, product: dict) -> list[tuple[dict, Decimal]]:
        """New, in-stock offers in our currency, with their prices."""
        offers = []
        for offer in product.get("offers") or []:
            price = _price(offer.get("price") or {}, self.currency)
            if (
                price is not None
                and offer.get("availability") == "InStock"
                and offer.get("condition") in (None, "new")
                and offer.get("url")
                and offer.get("domain")
            ):
                offers.append((offer, price))
        return offers


def describe(products: list[Product]) -> str:
    return "\n".join(f"- {p.id}: {p.name}. {p.description}" for p in products)


def _price(price: dict, currency: str) -> Decimal | None:
    if price.get("currency") != currency:
        return None
    try:
        amount = Decimal(str(price.get("price"))).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None
    return amount if amount > 0 else None


def _product(data: dict) -> Product:
    features = "; ".join((data.get("key_features") or [])[:6])
    return Product(data["id"], data.get("title") or data["id"], features or (data.get("description") or "")[:300])
