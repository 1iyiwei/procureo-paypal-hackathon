"""The standing order flow, following docs/storyboard.md, with the model and PayPal faked."""

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from intent_agent.agent import Agent
from intent_agent.brain import Understanding
from intent_agent.catalog import Catalog
from intent_agent.checkout import Checkout
from intent_agent.paypal import CreatedOrder, CreatedSetupToken, PayPalError, SavedPayPal
from intent_agent.store import Store
from intent_agent.watcher import Action, StandingOrder, decide

CHAT = 42
HEADPHONES = Understanding(
    action="new_request",
    title="headphones for Sam",
    summary="Noise-cancelling over-ear headphones",
    max_price=200,
    arrive_by="2026-12-14",
)


class FakeBrain:
    def __init__(self):
        self.next: Understanding | None = None
        self.product_ids = ["aria-700"]

    async def understand(self, text, images, context):
        self.context = context
        return self.next

    async def source(self, summary, catalog):
        return self.product_ids


class FakePayPal:
    def __init__(self):
        self.charges = []
        self.refunds = []
        self.deleted = []
        self.orders = 0
        self.charge_error = None

    async def create_order(self, **kwargs):
        self.orders += 1
        return CreatedOrder(f"ORDER{self.orders}", f"https://sandbox.paypal.test/pay/ORDER{self.orders}")

    async def capture_order(self, order_id):
        return {
            "id": order_id,
            "status": "COMPLETED",
            "purchase_units": [{"payments": {"captures": [{"id": f"CAP-{order_id}"}]}}],
        }

    async def create_setup_token(self, **kwargs):
        return CreatedSetupToken("SETUP1", "https://sandbox.paypal.test/approve/SETUP1")

    async def create_payment_token(self, setup_token_id):
        return SavedPayPal("TOKEN1", "CUST1", "buyer@example.com")

    async def delete_payment_token(self, token):
        self.deleted.append(token)

    async def charge_saved(self, **kwargs):
        if self.charge_error:
            raise self.charge_error
        self.charges.append(kwargs)
        return {
            "id": "AUTO1",
            "status": "COMPLETED",
            "purchase_units": [{"payments": {"captures": [{"id": "CAP-AUTO1"}]}}],
        }

    async def refund_capture(self, capture_id):
        self.refunds.append(capture_id)
        return {"status": "COMPLETED"}


class Harness:
    def __init__(self):
        self.store = Store(":memory:")
        self.paypal = FakePayPal()
        self.brain = FakeBrain()
        self.sent = []

        async def send(chat_id, text, buttons):
            self.sent.append((text, buttons or []))

        async def notify(chat_id, text):
            self.sent.append((text, []))

        self.checkout = Checkout(self.paypal, self.store, notify, "https://agent.example")
        self.agent = Agent(
            store=self.store,
            paypal=self.paypal,
            brain=self.brain,
            catalog=Catalog(),
            checkout=self.checkout,
            send=send,
            public_base_url="https://agent.example",
        )

    async def say(self, understood: Understanding, text="(message)"):
        self.brain.next = understood
        await self.agent.on_message(CHAT, text)
        return self.last

    @property
    def last(self):
        return self.sent[-1]

    def order(self) -> StandingOrder:
        return self.store.standing_orders(CHAT)[-1]

    def button(self, label: str, message=None):
        buttons = (message or self.last)[1]
        return next(b for b in buttons if label in b.text)

    async def tap(self, label: str):
        await self.agent.on_button(CHAT, self.button(label).data)

    async def start_watching(self):
        await self.say(HEADPHONES)
        await self.tap("Start watching")

    async def ff(self, **kwargs):
        return await self.agent.fast_forward(CHAT, **kwargs)

    def now(self) -> datetime:
        return self.store.sim_now(CHAT)


@pytest.fixture
def h():
    return Harness()


async def test_full_storyboard(h):
    h.brain.product_ids = ["aria-700", "lumen-pro"]  # Cedar Sound lists both, and counts as one store
    # Shot 2: describing the need creates a draft that spends nothing until it's confirmed.
    text, buttons = await h.say(HEADPHONES)
    assert "headphones for Sam" in text and "$200" in text and "Dec 14" in text and "buy by Dec 9" in text
    assert "ask me before buying" in text
    assert h.order().status == "DRAFT" and buttons[0].text == "Start watching"

    # Shot 3: three stores, then quiet updates when the lowest price moves.
    await h.tap("Start watching")
    assert h.order().status == "WATCHING"
    listing = h.sent[-1][0]
    assert all(store in listing for store in ("Harbor Audio", "Northline", "Cedar Sound"))

    assert await h.ff()
    assert "Nov 18" in h.last[0] and "$219" in h.last[0] and "Northline" in h.last[0]
    assert await h.ff()
    assert "Nov 24" in h.last[0] and "$209" in h.last[0]

    # Shot 4: Black Friday offer. The user declines and offers a tighter automatic limit.
    assert await h.ff()
    assert h.now() == datetime(2026, 11, 27, 0, 0)
    assert "$189 at Harbor Audio" in h.last[0] and "Arrives Dec 2" in h.last[0]
    assert h.button("Buy now").url
    await h.tap("Not yet")

    await h.say(Understanding(action="auto_buy_under", auto_limit=170))
    assert "$170" in h.last[0] and "Dec 9" in h.last[0]
    assert h.button("Save PayPal").url.endswith("SETUP1")
    assert not h.order().automatic  # asking isn't permission

    assert await h.agent.on_paypal_saved("SETUP1")
    assert "$170" in h.last[0] and "this standing order only" in h.last[0]
    assert h.order().auto_limit == Decimal("170.00")
    assert h.paypal.charges == []

    # Shot 5: the price drops at 2 a.m. and the agent buys on its own.
    assert await h.ff()
    assert h.now() == datetime(2026, 12, 1, 2, 0)
    text, _ = h.last
    assert "Bought: headphones for Sam, $164" in text and "Cedar Sound" in text
    assert "2:00 AM" in text and "under $170" in text and "Arrives Dec 4" in text
    assert h.order().status == "BOUGHT"
    assert len(h.paypal.charges) == 1
    charge = h.paypal.charges[0]
    assert charge["amount"] == Decimal("164") and charge["payment_token_id"] == "TOKEN1"

    await h.tap("Why this one?")
    assert "compared 3 stores" in h.last[0] and "$164" in h.last[0] and "10 days before Dec 14" in h.last[0]

    # Shot 6: everything is on record, and nothing more happens.
    await h.agent.show_orders(CHAT)
    timeline = h.last[0]
    for expected in ("$189", "not yet", "automatic buying under $170", "Bought automatically at $164"):
        assert expected in timeline
    assert not await h.ff(limit=timedelta(days=30))
    assert len(h.paypal.charges) == 1


async def test_cancelling_a_purchase_refunds_it(h):
    await h.start_watching()
    h.store.set_saved_paypal(CHAT, "TOKEN1")
    order = h.order()
    order.auto_limit = Decimal("170")
    h.store.save_standing_order(order)
    while await h.ff():
        pass
    await h.agent.on_button(CHAT, f"refund:{order.id}")
    assert h.paypal.refunds == ["CAP-AUTO1"]
    assert h.order().status == "REFUNDED"
    await h.agent.on_button(CHAT, f"refund:{order.id}")
    assert h.paypal.refunds == ["CAP-AUTO1"]


async def test_approving_an_offer_pays_through_checkout(h):
    await h.start_watching()
    while "Buy now" not in " ".join(b.text for b in h.last[1]):
        assert await h.ff()
    assert await h.checkout.complete("ORDER1")
    order = h.order()
    assert order.status == "BOUGHT" and order.bought_price == Decimal("189")
    assert order.bought_store == "Harbor Audio" and order.capture_id == "CAP-ORDER1"
    assert "Paid $189" in h.last[0]
    assert h.paypal.charges == []


async def test_paying_after_the_order_is_finished_is_refunded(h):
    await h.start_watching()
    while "Buy now" not in " ".join(b.text for b in h.last[1]):
        assert await h.ff()
    await h.tap("Not yet")
    await h.agent.on_button(CHAT, f"cancel:{h.order().id}")
    await h.checkout.complete("ORDER1")
    assert h.paypal.refunds == ["CAP-ORDER1"]
    assert h.order().status == "CANCELLED" and "refunded" in h.last[0]


async def test_missing_limit_is_asked_for_and_blocks_starting(h):
    text, buttons = await h.say(Understanding(action="new_request", title="headphones", summary="headphones"))
    assert "most you want to spend" in text and buttons == []
    await h.agent.on_button(CHAT, f"start:{h.order().id}")
    assert h.order().status == "DRAFT"
    _, buttons = await h.say(Understanding(action="update_order", max_price=150))
    assert h.order().max_price == Decimal("150.00") and buttons[0].text == "Start watching"


async def test_automatic_limit_above_spending_limit_is_refused(h):
    await h.start_watching()
    text, buttons = await h.say(Understanding(action="auto_buy_under", auto_limit=250))
    assert "above your $200 limit" in text and buttons == []
    assert h.order().pending_auto_limit is None


async def test_with_saved_paypal_automatic_buying_still_needs_a_tap(h):
    await h.start_watching()
    h.store.set_saved_paypal(CHAT, "TOKEN1")
    await h.say(Understanding(action="auto_buy_under", auto_limit=170))
    assert h.order().auto_limit is None and h.order().pending_auto_limit == Decimal("170.00")
    await h.tap("Yes, buy under $170")
    assert h.order().auto_limit == Decimal("170.00")


async def test_raising_the_limit_needs_confirming_and_ends_automatic_buying(h):
    await h.start_watching()
    order = h.order()
    order.auto_limit = Decimal("170")
    h.store.save_standing_order(order)
    text, buttons = await h.say(Understanding(action="update_order", max_price=300))
    order = h.order()
    assert order.status == "DRAFT" and order.auto_limit is None and order.max_price == Decimal("300.00")
    assert buttons[0].text == "Start watching" and "confirm" in text


async def test_lowering_the_limit_applies_and_clamps_automatic_limit(h):
    await h.start_watching()
    order = h.order()
    order.auto_limit = Decimal("170")
    h.store.save_standing_order(order)
    await h.say(Understanding(action="update_order", max_price=150))
    order = h.order()
    assert order.status == "WATCHING" and order.max_price == Decimal("150.00") and order.auto_limit == Decimal("150.00")


async def test_failed_automatic_payment_falls_back_to_asking(h):
    await h.start_watching()
    h.store.set_saved_paypal(CHAT, "TOKEN1")
    order = h.order()
    order.auto_limit = Decimal("170")
    h.store.save_standing_order(order)
    h.paypal.charge_error = PayPalError("declined", 422, {})
    while "declined" not in h.last[0]:
        assert await h.ff()
    order = h.order()
    assert order.status == "WATCHING" and order.auto_limit is None


async def test_automatic_buying_refuses_without_saved_paypal(h):
    await h.start_watching()
    order = h.order()
    order.auto_limit = Decimal("170")  # as if the token had disappeared
    h.store.save_standing_order(order)
    while await h.ff(limit=timedelta(days=20)):
        pass
    assert h.paypal.charges == []
    assert h.order().status != "BOUGHT"


async def test_price_above_limit_at_the_deadline_expires_without_buying(h):
    await h.say(Understanding(action="new_request", title="watch", summary="GPS watch", max_price=100, arrive_by="2026-12-14"))
    h.brain.product_ids = ["trek-watch"]
    await h.tap("Start watching")
    seen = []
    while await h.ff():
        seen.append(h.last[0])
    assert any("last day" in s and "won't buy" in s for s in seen)
    assert h.order().status == "EXPIRED"
    assert h.paypal.charges == [] and h.paypal.orders == 0


async def test_no_catalog_match_stays_a_draft(h):
    await h.say(HEADPHONES)
    h.brain.product_ids = ["not-a-real-id"]
    await h.tap("Start watching")
    assert h.order().status == "DRAFT" and "couldn't find" in h.last[0]


async def test_buttons_only_work_in_their_own_chat(h):
    await h.say(HEADPHONES)
    order = h.order()
    await h.agent.on_button(999, f"start:{order.id}")
    assert h.store.standing_order(order.id).status == "DRAFT"


async def test_pause_stops_everything_and_resume_continues(h):
    await h.start_watching()
    await h.say(Understanding(action="pause"))
    assert h.order().status == "PAUSED"
    assert not await h.ff()
    await h.say(Understanding(action="resume"))
    assert h.order().status == "WATCHING"


async def test_remove_saved_paypal_ends_automatic_buying(h):
    await h.start_watching()
    h.store.set_saved_paypal(CHAT, "TOKEN1")
    order = h.order()
    order.auto_limit = Decimal("170")
    h.store.save_standing_order(order)
    await h.say(Understanding(action="remove_saved_paypal"))
    assert h.paypal.deleted == ["TOKEN1"]
    assert h.store.saved_paypal(CHAT) is None and h.order().auto_limit is None


async def test_user_text_is_escaped(h):
    text, _ = await h.say(Understanding(action="new_request", title="<b>x</b>", summary="a & b", max_price=10))
    assert "&lt;b&gt;x&lt;/b&gt;" in text and "a &amp; b" in text


async def test_reset_starts_over(h):
    await h.start_watching()
    await h.ff()
    h.store.set_saved_paypal(CHAT, "TOKEN1")
    await h.agent.reset(CHAT)
    assert h.store.standing_orders(CHAT) == []
    assert h.now() == datetime(2026, 11, 16, 9, 0)
    assert h.paypal.deleted == ["TOKEN1"]


def test_decide_does_not_buy_late_arrivals():
    catalog = Catalog()
    order = StandingOrder(
        id="x",
        chat_id=1,
        title="t",
        summary="s",
        status="WATCHING",
        max_price=Decimal("200"),
        auto_limit=Decimal("170"),
        product_ids=["aria-700"],
        arrive_by=datetime(2026, 12, 3).date(),  # $164 arrives Dec 4
    )
    now = datetime(2026, 12, 1, 2, 0)
    decision = decide(order, catalog.quotes(order.product_ids, now), now)
    assert decision.action is not Action.AUTO_BUY
