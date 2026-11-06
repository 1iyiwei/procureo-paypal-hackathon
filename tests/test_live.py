"""Live mode: real products from Channel3 (faked at the HTTP level), with PayPal and the model faked."""

from datetime import datetime
from decimal import Decimal

import httpx
import pytest
import respx

from intent_agent.agent import Agent
from intent_agent.catalog import Catalog
from intent_agent.checkout import Checkout
from intent_agent.live import CHANNEL3_URL, LiveCatalog
from intent_agent.store import Store
from intent_agent.watcher import Action, StandingOrder, decide
from tests.test_agent import CHAT, HEADPHONES, FakeBrain, FakePayPal


def offer(price, domain="bestbuy.com", **kwargs):
    return {
        "url": f"https://www.buy.trychannel3.com/{domain}",
        "domain": domain,
        "price": {"price": price, "currency": "USD"},
        "availability": "InStock",
        **kwargs,
    }


def product(id="p1", title="Sony WH-1000XM5", offers=None):
    return {"id": id, "title": title, "key_features": ["Over-ear", "Noise cancelling"], "offers": offers or [offer(248.0)]}


class Live:
    def __init__(self, router):
        self.router = router
        self.store = Store(":memory:")
        self.paypal = FakePayPal()
        self.brain = FakeBrain()
        self.brain.product_ids = ["p1"]
        self.sent = []

        async def send(chat_id, text, buttons):
            self.sent.append((text, buttons or []))

        async def notify(chat_id, text):
            self.sent.append((text, []))

        self.agent = Agent(
            store=self.store,
            paypal=self.paypal,
            brain=self.brain,
            catalog=Catalog(),
            checkout=Checkout(self.paypal, self.store, notify, "https://agent.example"),
            send=send,
            public_base_url="https://agent.example",
            live_catalog=LiveCatalog("key"),
        )
        self.store.set_chat_live(CHAT, True)

    @property
    def last(self):
        return self.sent[-1]

    def order(self):
        return self.store.standing_orders(CHAT)[-1]

    async def tap(self, label):
        button = next(b for b in self.last[1] if label in b.text)
        await self.agent.on_button(CHAT, button.data)

    async def draft(self, understood=HEADPHONES):
        self.brain.next = understood
        await self.agent.on_message(CHAT, "(message)")

    async def watching(self, price=248.0):
        self.router.post(f"{CHANNEL3_URL}/v1/search").respond(json={"products": [product(offers=[offer(price)])]})
        self.price(price)
        await self.draft()
        await self.tap("Start watching")
        await self.tap("Watch these")

    def price(self, price, **kwargs):
        self.router.get(f"{CHANNEL3_URL}/v1/products/p1").respond(json=product(offers=[offer(price)]), **kwargs)

    def allow_auto_buy(self, limit):
        order = self.order()
        order.auto_limit = Decimal(limit)
        self.store.save_standing_order(order)
        self.store.set_saved_paypal(CHAT, "TOKEN1")


@pytest.fixture
def live():
    with respx.mock(assert_all_called=False) as router:
        yield Live(router)


async def test_real_products_need_confirming_before_any_watching(live):
    live.router.post(f"{CHANNEL3_URL}/v1/search").respond(json={"products": [product(), product("acc", "Ear pads")]})
    live.price(248.0)
    await live.draft()
    await live.tap("Start watching")

    text, buttons = live.last
    assert "Sony WH-1000XM5" in text and "$248" in text and "bestbuy.com" in text
    assert "fake money" in text and "Ear pads" not in text
    assert live.order().status == "DRAFT" and live.order().product_ids == []

    await live.tap("Watch these")
    assert live.order().status == "WATCHING" and live.order().product_ids == ["p1"]
    assert live.order().reference_price == Decimal("248.00")


async def test_products_inside_the_limit_come_first_and_duplicates_go(live):
    search = live.router.post(f"{CHANNEL3_URL}/v1/search").respond(json={"products": [
        product("over", "Too dear", [offer(240.0)]),
        product("a", "Fits A", [offer(150.0)]),
        product("a2", "fits  a", [offer(150.0)]),
        product("b", "Fits B", [offer(199.0)]),
        product("c", "Fits C", [offer(120.0)]),
    ]})
    for p in [product("over", "Too dear", [offer(240.0)]), product("a", "Fits A", [offer(150.0)]),
              product("b", "Fits B", [offer(199.0)]), product("c", "Fits C", [offer(120.0)])]:
        live.router.get(f"{CHANNEL3_URL}/v1/products/{p['id']}").respond(json=p)
    live.brain.product_ids = ["over", "a", "a2", "b", "c"]
    await live.draft()
    await live.tap("Start watching")
    assert live.order().candidate_ids == ["a", "b", "c"]
    assert '"max_price":250.0' in search.calls.last.request.content.decode().replace(" ", "")


async def test_a_model_cant_add_a_product_the_search_didnt_return(live):
    live.router.post(f"{CHANNEL3_URL}/v1/search").respond(json={"products": [product()]})
    live.price(248.0)
    live.brain.product_ids = ["p1", "invented"]
    await live.draft()
    await live.tap("Start watching")
    assert live.order().candidate_ids == ["p1"]


async def test_changing_the_request_makes_the_old_list_stale(live):
    live.router.post(f"{CHANNEL3_URL}/v1/search").respond(json={"products": [product()]})
    live.price(248.0)
    await live.draft()
    await live.tap("Start watching")
    button = next(b for b in live.last[1] if "Watch these" in b.text)
    await live.draft(HEADPHONES.model_copy(update={"action": "update_order", "summary": "Earbuds"}))
    await live.agent.on_button(CHAT, button.data)
    assert live.order().status == "DRAFT" and "out of date" in live.last[0]


async def test_a_price_inside_the_limit_is_offered_with_the_store_link(live):
    await live.watching()
    live.price(189.0)
    assert await live.agent.check_live()
    text, buttons = live.last
    assert "$189" in text and 'href="https://www.buy.trychannel3.com/bestbuy.com"' in text
    assert "Estimated arrival" in text and "fake money" in text
    assert any("Buy now" in b.text for b in buttons)


async def test_automatic_buying_says_it_is_a_test_and_charges_once(live):
    await live.watching(price=199.0)
    live.allow_auto_buy("170")
    live.price(164.0)
    assert await live.agent.check_live()
    assert live.order().status == "BOUGHT"
    assert "Test purchase" in live.last[0] and "fake money" in live.last[0]
    assert len(live.paypal.charges) == 1
    await live.agent.check_live()
    assert len(live.paypal.charges) == 1


async def test_a_suspiciously_low_price_asks_instead_of_buying(live):
    await live.watching(price=199.0)
    live.allow_auto_buy("170")
    live.price(12.0)
    await live.agent.check_live()
    assert live.paypal.charges == []
    assert live.order().status == "WATCHING"
    assert "$12" in live.last[0] and any("Buy now" in b.text for b in live.last[1])


async def test_a_failed_price_check_does_nothing(live):
    await live.watching()
    live.allow_auto_buy("170")
    count = len(live.sent)
    live.router.get(f"{CHANNEL3_URL}/v1/products/p1").respond(status_code=500)
    assert not await live.agent.check_live()
    assert len(live.sent) == count and live.paypal.charges == []
    live.router.get(f"{CHANNEL3_URL}/v1/products/p1").mock(side_effect=httpx.ConnectError("down"))
    assert not await live.agent.check_live()
    assert live.order().status == "WATCHING"


async def test_pausing_during_a_price_check_stops_the_purchase(live):
    await live.watching(price=199.0)
    live.allow_auto_buy("170")

    def pause_then_answer(request):
        order = live.order()
        order.status = "PAUSED"
        live.store.save_standing_order(order)
        return httpx.Response(200, json=product(offers=[offer(164.0)]))

    live.router.get(f"{CHANNEL3_URL}/v1/products/p1").mock(side_effect=pause_then_answer)
    await live.agent.check_live()
    assert live.paypal.charges == []


async def test_used_foreign_and_out_of_stock_offers_are_ignored():
    catalog = LiveCatalog("key")
    data = product(offers=[
        offer(10.0, condition="used"),
        offer(11.0, availability="OutOfStock"),
        {**offer(12.0), "price": {"price": 12.0, "currency": "EUR"}},
        offer(13.0, condition="new", domain="good.example"),
    ])
    with respx.mock:
        respx.get(f"{CHANNEL3_URL}/v1/products/p1").respond(json=data)
        quotes = await catalog.quotes(["p1"], datetime(2026, 11, 4))
    assert [(q.price, q.store) for q in quotes] == [(Decimal("13.00"), "good.example")]


async def test_a_product_that_disappears_is_not_an_error():
    catalog = LiveCatalog("key")
    with respx.mock:
        respx.get(f"{CHANNEL3_URL}/v1/products/p1").respond(status_code=404)
        assert await catalog.quotes(["p1"], datetime(2026, 11, 4)) == []


async def test_demo_chats_are_not_touched_by_live_checks(live):
    other = 77
    live.store.sim_now(other)
    assert await live.agent.check_live() is False
    assert not live.store.chat_live(other)


async def test_mode_cant_change_while_watching(live):
    await live.watching()
    await live.agent.set_mode(CHAT, False)
    assert live.store.chat_live(CHAT) and "first" in live.last[0]


async def test_a_failed_demo_search_points_to_live_and_the_draft_carries_over(live):
    live.store.set_chat_live(CHAT, False)
    live.brain.product_ids = []
    await live.draft()
    await live.tap("Start watching")
    assert "couldn't find" in live.last[0] and "/live" in live.last[0]

    await live.agent.set_mode(CHAT, True)
    assert live.store.chat_live(CHAT) and live.order().status == "DRAFT"
    assert "Standing order: headphones for Sam" in live.last[0]

    live.brain.product_ids = ["p1"]
    live.router.post(f"{CHANNEL3_URL}/v1/search").respond(json={"products": [product()]})
    live.price(248.0)
    await live.tap("Start watching")
    assert "Sony WH-1000XM5" in live.last[0]


async def test_real_products_dont_follow_a_draft_back_to_the_demo(live):
    live.router.post(f"{CHANNEL3_URL}/v1/search").respond(json={"products": [product()]})
    live.price(248.0)
    await live.draft()
    await live.tap("Start watching")
    watch = next(b for b in live.last[1] if "Watch these" in b.text)
    await live.agent.set_mode(CHAT, False)
    assert live.order().candidate_ids == []
    await live.agent.on_button(CHAT, watch.data)
    assert live.order().status == "DRAFT" and "out of date" in live.last[0]


async def test_live_mode_needs_a_key():
    store = Store(":memory:")
    sent = []

    async def send(chat_id, text, buttons):
        sent.append(text)

    paypal = FakePayPal()
    agent = Agent(
        store=store, paypal=paypal, brain=FakeBrain(), catalog=Catalog(), send=send, public_base_url="https://a.example",
        checkout=Checkout(paypal, store, send, "https://a.example"),
    )
    await agent.set_mode(CHAT, True)
    assert not store.chat_live(CHAT) and "isn't set up" in sent[-1]


def test_suspicious_price_rule_only_applies_with_a_reference():
    order = StandingOrder(id="x", chat_id=1, title="t", summary="s", max_price=Decimal(200), auto_limit=Decimal(170), status="WATCHING")
    from intent_agent.catalog import Product, Quote

    quote = Quote(Product("p", "n", "d"), "s", Decimal(12), datetime(2026, 11, 9).date())
    assert decide(order, [quote], datetime(2026, 11, 4)).action is Action.AUTO_BUY
    order.reference_price = Decimal(199)
    assert decide(order, [quote], datetime(2026, 11, 4)).action is Action.ASK
