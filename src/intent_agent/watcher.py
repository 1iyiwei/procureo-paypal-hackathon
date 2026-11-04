"""Decides what a standing order should do at a moment in time. Pure logic: no I/O, no AI."""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import Enum

from .catalog import Quote

# How long before "arrive by" the agent stops waiting and has to buy.
BUY_MARGIN = timedelta(days=5)


@dataclass
class StandingOrder:
    id: str
    chat_id: int
    title: str
    summary: str
    max_price: Decimal | None = None
    arrive_by: date | None = None
    status: str = "DRAFT"  # DRAFT, WATCHING, PAUSED, BUYING, BOUGHT, REFUNDED, EXPIRED, CANCELLED
    auto_limit: Decimal | None = None  # set only after the user taps to allow automatic buying
    pending_auto_limit: Decimal | None = None  # proposed, not yet confirmed by a tap
    product_ids: list[str] = field(default_factory=list)
    last_low: Decimal | None = None  # lowest price told to the user so far
    last_offer: Decimal | None = None  # lowest price already offered to the user
    last_call_sent: bool = False
    bought_price: Decimal | None = None
    bought_store: str | None = None
    bought_at: datetime | None = None
    arrives_on: date | None = None
    paypal_order_id: str | None = None
    capture_id: str | None = None
    why: str | None = None

    @property
    def buy_by(self) -> date | None:
        return self.arrive_by - BUY_MARGIN if self.arrive_by else None

    @property
    def automatic(self) -> bool:
        return self.auto_limit is not None


class Action(Enum):
    NOTHING = "nothing"
    UPDATE = "update"  # tell the user the lowest price moved
    ASK = "ask"  # offer a price inside the limit and wait for the user
    AUTO_BUY = "auto_buy"  # pay with saved PayPal
    LAST_CALL = "last_call"  # last day to buy in time
    EXPIRE = "expire"  # too late to arrive in time


@dataclass(frozen=True)
class Decision:
    action: Action
    quote: Quote | None = None


def eligible(order: StandingOrder, quotes: list[Quote]) -> list[Quote]:
    """Quotes that would arrive in time, cheapest first."""
    in_time = [q for q in quotes if order.arrive_by is None or q.arrives_on <= order.arrive_by]
    return sorted(in_time, key=lambda q: (q.price, q.arrives_on))


def may_auto_buy(order: StandingOrder, price: Decimal) -> bool:
    """The one rule that lets the agent spend without asking."""
    return (
        order.auto_limit is not None
        and order.max_price is not None
        and order.auto_limit <= order.max_price
        and price <= order.auto_limit
    )


def decide(order: StandingOrder, quotes: list[Quote], now: datetime) -> Decision:
    if order.status != "WATCHING" or order.max_price is None:
        return Decision(Action.NOTHING)
    if order.buy_by and now.date() > order.buy_by:
        return Decision(Action.EXPIRE)

    options = eligible(order, quotes)
    best = options[0] if options else None
    if best and may_auto_buy(order, best.price):
        return Decision(Action.AUTO_BUY, best)
    if order.buy_by and now.date() == order.buy_by and not order.last_call_sent:
        return Decision(Action.LAST_CALL, best)
    if best is None:
        return Decision(Action.NOTHING)
    is_new_offer = best.price <= order.max_price and (order.last_offer is None or best.price < order.last_offer)
    if is_new_offer and not order.automatic:
        return Decision(Action.ASK, best)
    if order.last_low is None or best.price < order.last_low:
        return Decision(Action.UPDATE, best)
    return Decision(Action.NOTHING)
