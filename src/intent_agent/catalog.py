"""Mock product catalog: fictional products and stores, with prices scripted over simulated time.

Prices change at fixed moments so the demo is repeatable. The scenario in docs/storyboard.md depends on
the `aria-700` schedule below. A real catalog would replace this module behind the same methods.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

SIM_START = datetime(2026, 11, 16, 9, 0)


@dataclass(frozen=True)
class Product:
    id: str
    name: str
    description: str


@dataclass(frozen=True)
class Listing:
    product_id: str
    store: str
    ship_days: int
    prices: tuple[tuple[datetime, Decimal], ...]  # (from when, price), oldest first

    def price_at(self, now: datetime) -> Decimal:
        current = self.prices[0][1]
        for start, price in self.prices:
            if now >= start:
                current = price
        return current


@dataclass(frozen=True)
class Quote:
    """What a listing offers right now."""

    product: Product
    store: str
    price: Decimal
    arrives_on: date
    url: str | None = None  # the store's page, for a real product
    live: bool = False  # a real product: the arrival date is an estimate, and buying it is only a sandbox test


def _listing(product_id: str, store: str, ship_days: int, start_price: int, *changes: tuple[str, int]) -> Listing:
    prices = [(datetime(2026, 1, 1), Decimal(start_price))]
    prices += [(datetime.fromisoformat(when), Decimal(price)) for when, price in changes]
    return Listing(product_id, store, ship_days, tuple(prices))


PRODUCTS = [
    Product("aria-700", "Aria Quiet 700", "Over-ear wireless headphones with active noise cancelling, 40-hour battery, black or silver"),
    Product("lumen-pro", "Lumen Hush Pro", "Premium over-ear wireless headphones with active noise cancelling and a leather headband"),
    Product("pebble-buds", "Pebble Buds 2", "In-ear wireless earbuds with active noise cancelling and a pocket charging case"),
    Product("tala-reader", "Tala Page 7", "7-inch e-reader with a glare-free screen, waterproof, 6-week battery"),
    Product("moka-one", "Moka One", "Compact home espresso machine with a milk frother"),
    Product("ridge-pack", "Ridge 28 Backpack", "28-litre hiking backpack, water resistant, with a laptop sleeve"),
    Product("hearth-throw", "Hearth Wool Throw", "Soft merino wool throw blanket, 130 x 180 cm, in five colors"),
    Product("trek-watch", "Trek GPS Watch", "Outdoor GPS sports watch with heart rate tracking and a 2-week battery"),
]

LISTINGS = [
    # Storyboard scenario: $219 on Nov 18, $209 on Nov 24, $189 on Black Friday, $164 at 2 a.m. on Dec 1.
    _listing("aria-700", "Harbor Audio", 5, 229, ("2026-11-24T08:00", 209), ("2026-11-27T00:00", 189), ("2026-12-02T00:00", 209)),
    _listing("aria-700", "Northline", 4, 225, ("2026-11-18T08:00", 219), ("2026-11-28T00:00", 205)),
    _listing("aria-700", "Cedar Sound", 3, 239, ("2026-11-27T00:00", 199), ("2026-12-01T02:00", 164), ("2026-12-03T00:00", 199)),
    _listing("lumen-pro", "Cedar Sound", 3, 279, ("2026-11-27T00:00", 249)),
    _listing("pebble-buds", "Harbor Audio", 5, 129, ("2026-11-27T00:00", 99)),
    _listing("pebble-buds", "Northline", 4, 124, ("2026-11-30T00:00", 94)),
    _listing("tala-reader", "Pageturner", 4, 139, ("2026-11-27T00:00", 109)),
    _listing("tala-reader", "Northline", 4, 135, ("2026-11-30T06:00", 104)),
    _listing("moka-one", "Kitchen Row", 6, 349, ("2026-11-27T00:00", 299), ("2026-12-05T00:00", 279)),
    _listing("ridge-pack", "Summit Supply", 3, 119, ("2026-11-27T00:00", 89)),
    _listing("ridge-pack", "Northline", 4, 115, ("2026-12-01T00:00", 94)),
    _listing("hearth-throw", "Fold & Loom", 5, 98, ("2026-11-27T00:00", 79), ("2026-12-04T00:00", 69)),
    _listing("trek-watch", "Summit Supply", 3, 249, ("2026-11-27T00:00", 209)),
    _listing("trek-watch", "Harbor Audio", 5, 239, ("2026-11-30T00:00", 199)),
]


class Catalog:
    def __init__(self, products: list[Product] = PRODUCTS, listings: list[Listing] = LISTINGS):
        self.products = {p.id: p for p in products}
        self.listings = listings

    def describe(self) -> str:
        """Catalog text for the model to match requests against."""
        return "\n".join(f"- {p.id}: {p.name}. {p.description}" for p in self.products.values())

    def quotes(self, product_ids: list[str], now: datetime) -> list[Quote]:
        return [
            Quote(
                product=self.products[listing.product_id],
                store=listing.store,
                price=listing.price_at(now),
                arrives_on=(now + timedelta(days=listing.ship_days)).date(),
            )
            for listing in self.listings
            if listing.product_id in product_ids
        ]
