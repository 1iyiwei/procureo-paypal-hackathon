"""The standing order conversation and purchase flow, independent of Telegram.

Who can do what:
- The model (`Brain`) proposes. Its output can create a draft, tighten a limit, or suggest automatic buying.
- Only a button tap can start watching, raise a limit, or turn on automatic buying.
- `_auto_buy` re-checks the limits in code right before every payment made without the user.
"""

import html
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

from .brain import Brain, Image, Understanding, context_date
from .catalog import Catalog, Quote
from .checkout import Checkout
from .live import Channel3Error, LiveCatalog, describe
from .paypal import PayPalClient, PayPalError, capture_id_of
from .store import Order, Store
from .watcher import Action, StandingOrder, decide, eligible, may_auto_buy

log = logging.getLogger(__name__)

ACTIVE = ("DRAFT", "WATCHING", "PAUSED")
MAX_FAST_FORWARD = timedelta(days=60)
MAX_LIVE_PRODUCTS = 3


@dataclass(frozen=True)
class Button:
    text: str
    data: str | None = None  # callback, handled by `Agent.on_button`
    url: str | None = None


Send = Callable[[int, str, list[Button] | None], Awaitable[None]]


def money(amount: Decimal) -> str:
    return f"${amount:,.0f}" if amount == amount.to_integral() else f"${amount:,.2f}"


def day(d: date) -> str:
    return f"{d:%b} {d.day}"


def moment(t: datetime) -> str:
    hour = t.hour % 12 or 12
    return f"{day(t)}, {hour}:{t:%M} {'AM' if t.hour < 12 else 'PM'}"


def esc(text: str | None) -> str:
    return html.escape(text or "", quote=False)


def arrival(q: Quote) -> str:
    return f"Estimated arrival {day(q.arrives_on)}, as the store's shipping time isn't known." if q.live else f"Arrives {day(q.arrives_on)}."


def store_link(q: Quote) -> str:
    return f'<a href="{html.escape(q.url, quote=True)}">{esc(q.store)}</a>' if q.url else esc(q.store)


TEST_PURCHASE = "PayPal is in sandbox mode here, so this is a test payment with fake money, and nothing will ship."


def _within_budget_first(ids: list[str], quotes: list[Quote], limit: Decimal) -> list[str]:
    """Keep the given order among products inside the limit, then the closest ones above it."""
    lowest = {}
    for q in quotes:
        lowest[q.product.id] = min(q.price, lowest.get(q.product.id, q.price))
    available = [i for i in ids if i in lowest]
    return sorted(available, key=lambda i: (lowest[i] > limit, lowest[i] if lowest[i] > limit else 0))


def _quote_lines(quotes: list[Quote]) -> list[str]:
    return [f"• {esc(q.product.name)} at {store_link(q)}: {money(q.price)}" for q in sorted(quotes, key=lambda q: q.price)]


class Agent:
    def __init__(
        self,
        *,
        store: Store,
        paypal: PayPalClient,
        brain: Brain,
        catalog: Catalog,
        checkout: Checkout,
        send: Send,
        public_base_url: str,
        currency: str = "USD",
        demo_hints: bool = True,
        live_catalog: LiveCatalog | None = None,
    ):
        self.store = store
        self.paypal = paypal
        self.brain = brain
        self.catalog = catalog
        self.checkout = checkout
        self.send = send
        self.base_url = public_base_url.rstrip("/")
        self.currency = currency
        self.demo_hints = demo_hints
        self.live_catalog = live_catalog
        checkout.on_paid = self.on_checkout_paid

    # --- Clock and mode: a chat runs either the scripted demo on a simulated clock, or real products in real time ---

    def now(self, chat_id: int) -> datetime:
        return datetime.now().replace(microsecond=0) if self.store.chat_live(chat_id) else self.store.sim_now(chat_id)

    async def set_mode(self, chat_id: int, live: bool) -> None:
        if live and self.live_catalog is None:
            return await self.send(chat_id, "Live mode isn't set up on this server.", None)
        if self.store.chat_live(chat_id) != live and any(o.status in ACTIVE for o in self.store.standing_orders(chat_id)):
            return await self.send(chat_id, "Finish or cancel your open standing orders first, or send /reset to start over.", None)
        self.store.set_chat_live(chat_id, live)
        if live:
            return await self.send(
                chat_id,
                "<b>Live mode.</b> I'll search real stores and check real prices every few hours, on the real calendar, so "
                f"this can take days or weeks. {TEST_PURCHASE} Send /demo to go back.",
                None,
            )
        await self.send(chat_id, "Back to the demo, with simulated stores and a simulated clock.", None)

    async def check_live(self, chat_id: int | None = None) -> bool:
        """Evaluate live orders against current prices. One failing order doesn't stop the others."""
        spoke = False
        for chat in [chat_id] if chat_id is not None else self.store.live_chats():
            for order in self.store.standing_orders(chat):
                try:
                    spoke = await self.evaluate(order, self.now(chat)) or spoke
                except Exception:
                    log.exception("live check failed for order %s", order.id)
        return spoke

    # --- Incoming messages ---

    async def on_message(self, chat_id: int, text: str, images: list[Image] | None = None) -> None:
        now = self.now(chat_id)
        understood = await self.brain.understand(text, images or [], self._context(chat_id, now))
        log.info("understood: %s", understood.model_dump(exclude_none=True))
        handler = {
            "new_request": self._new_request,
            "update_order": self._update_order,
            "auto_buy_under": self._auto_buy_under,
            "pause": self._pause,
            "resume": self._resume,
            "cancel": self._cancel,
        }.get(understood.action)
        if handler:
            await handler(chat_id, understood, now)
        elif understood.action == "list_orders":
            await self.show_orders(chat_id)
        elif understood.action == "remove_saved_paypal":
            await self.remove_saved_paypal(chat_id)
        else:
            await self.send(chat_id, esc(understood.reply) or "Tell me what you'd like me to find, and the most you'll pay.", None)

    def _context(self, chat_id: int, now: datetime) -> str:
        lines = [
            f"Today: {context_date(now)}",
            f"Saved PayPal: {'yes' if self.store.saved_paypal(chat_id) else 'no'}",
            "Their standing orders:",
        ]
        orders = [o for o in self.store.standing_orders(chat_id) if o.status in ACTIVE]
        for o in orders:
            recent = "; ".join(text for _, text in self.store.events(o.id)[-3:])
            lines.append(
                f"- id={o.id} status={o.status} title={o.title!r} summary={o.summary!r} limit={o.max_price} "
                f"arrive_by={o.arrive_by} automatic_under={o.auto_limit} recent: {recent}"
            )
        if not orders:
            lines.append("(none)")
        return "\n".join(lines)

    def _find_order(self, chat_id: int, understood: Understanding) -> StandingOrder | None:
        orders = [o for o in self.store.standing_orders(chat_id) if o.status in ACTIVE]
        for o in orders:
            if o.id == understood.order_id:
                return o
        return orders[-1] if orders else None

    async def _no_order(self, chat_id: int) -> None:
        await self.send(chat_id, "You don't have a standing order yet. Tell me what you'd like to buy and the most you'll pay.", None)

    # --- Creating and changing an order ---

    async def _new_request(self, chat_id: int, u: Understanding, now: datetime) -> None:
        for old in self.store.standing_orders(chat_id):
            if old.status == "DRAFT":
                old.status = "CANCELLED"
                self.store.save_standing_order(old)
        order = StandingOrder(
            id=uuid.uuid4().hex[:8],
            chat_id=chat_id,
            title=u.title or "your request",
            summary=u.summary or u.title or "",
            max_price=_price(u.max_price),
            arrive_by=_iso_date(u.arrive_by),
        )
        self.store.save_standing_order(order)
        self.store.add_event(order.id, now, "Created")
        await self._send_card(order)

    async def _update_order(self, chat_id: int, u: Understanding, now: datetime) -> None:
        order = self._find_order(chat_id, u)
        if order is None:
            return await self._no_order(chat_id)
        new_limit, new_date = _price(u.max_price), _iso_date(u.arrive_by)
        spec_changed = (u.summary and u.summary != order.summary) or (u.title and u.title != order.title)
        widens = (new_limit is not None and order.max_price is not None and new_limit > order.max_price) or (
            new_date is not None and order.arrive_by is not None and new_date > order.arrive_by
        )

        order.title, order.summary = u.title or order.title, u.summary or order.summary
        order.max_price, order.arrive_by = new_limit or order.max_price, new_date or order.arrive_by

        if order.status != "DRAFT" and (spec_changed or widens):
            # A different item or more room to spend needs a fresh confirmation, and automatic buying starts over.
            order.status, order.auto_limit, order.pending_auto_limit = "DRAFT", None, None
            order.product_ids, order.last_low, order.last_offer = [], None, None
            order.candidate_ids, order.reference_price = [], None
            self.store.save_standing_order(order)
            self.store.add_event(order.id, now, "Changed; needs confirming again")
            return await self._send_card(order, "Updated. Please confirm before I start watching again.")

        if spec_changed:
            order.candidate_ids = []  # chosen for the old description
        if order.auto_limit is not None and order.max_price is not None and order.auto_limit > order.max_price:
            order.auto_limit = order.max_price
        self.store.save_standing_order(order)
        if order.status == "DRAFT":
            return await self._send_card(order)
        self.store.add_event(order.id, now, f"Limit tightened to {money(order.max_price)}")
        await self.send(chat_id, f"Updated. {self._limits_line(order)}", None)
        await self.evaluate(order, now)

    def _limits_line(self, order: StandingOrder) -> str:
        parts = [f"Spend up to <b>{money(order.max_price)}</b>." if order.max_price else ""]
        if order.arrive_by:
            parts.append(f"Arrive by <b>{day(order.arrive_by)}</b>.")
        return " ".join(p for p in parts if p)

    async def _send_card(self, order: StandingOrder, intro: str = "") -> None:
        lines = [intro + "\n"] if intro else []
        lines += [f"<b>Standing order: {esc(order.title)}</b>", esc(order.summary)]
        lines.append(f"Spend up to: <b>{money(order.max_price)}</b>" if order.max_price else "Spend up to: <i>not set</i>")
        if order.arrive_by:
            lines.append(f"Arrive by: <b>{day(order.arrive_by)}</b> (so buy by {day(order.buy_by)}, allowing for shipping)")
        lines.append("Mode: <b>ask me before buying</b>")
        if order.max_price is None:
            lines.append("\nWhat's the most you want to spend?")
            return await self.send(order.chat_id, "\n".join(lines), None)
        lines.append("\nTo change anything, just tell me.")
        await self.send(order.chat_id, "\n".join(lines), [Button("Start watching", data=f"start:{order.id}")])

    # --- Buttons ---

    async def on_button(self, chat_id: int, data: str) -> None:
        action, _, order_id = data.partition(":")
        order = self.store.standing_order(order_id)
        if order is None or order.chat_id != chat_id:
            return await self.send(chat_id, "That standing order no longer exists.", None)
        handler = {
            "start": self._start_watching,
            "watch": self._watch_candidates,
            "notyet": self._not_yet,
            "auto": self._confirm_auto,
            "why": self._why,
            "receipt": self._receipt,
            "refund": self._refund,
            "cancel": self._cancel_tapped,
        }.get(action)
        if handler:
            await handler(order)

    async def _start_watching(self, order: StandingOrder) -> None:
        if order.status != "DRAFT" or order.max_price is None:
            return
        if self.store.chat_live(order.chat_id):
            return await self._propose_live_products(order)
        now = self.now(order.chat_id)
        ids = [i for i in await self.brain.source(order.summary, self.catalog.describe()) if i in self.catalog.products]
        if not ids:
            return await self.send(
                order.chat_id,
                f"I couldn't find a match in the demo stores for: {esc(order.summary)}\n\nThey carry headphones, earbuds, "
                "e-readers, espresso machines, backpacks, wool throws and GPS watches. Tell me what to change.",
                None,
            )
        await self._begin_watching(order, ids, self.catalog.quotes(ids, now), now)

    async def _propose_live_products(self, order: StandingOrder) -> None:
        """Real listings need a human check, so the user confirms the exact products before any are watched."""
        now = self.now(order.chat_id)
        try:
            found = await self.live_catalog.search(order.summary, order.max_price)
            allowed = {p.id for p in found}
            matched = [i for i in await self.brain.source(order.summary, describe(found)) if i in allowed]
            ids = _within_budget_first(matched, await self.live_catalog.quotes(matched, now), order.max_price)[:MAX_LIVE_PRODUCTS]
            quotes = await self.live_catalog.quotes(ids, now)
        except Channel3Error as e:
            log.warning("live search failed: %s", e)
            return await self.send(order.chat_id, "I couldn't reach the product search just now. Please try again in a moment.", [Button("Start watching", data=f"start:{order.id}")])
        if not quotes:
            return await self.send(order.chat_id, f"I couldn't find a real product in stock for: {esc(order.summary)}\n\nTell me what to change.", None)
        order.candidate_ids = ids
        self.store.save_standing_order(order)
        lines = [f"I found these for {esc(order.title)}. Are they what you want?"]
        lines += _quote_lines(quotes)
        lines.append(f"\nIf not, tell me what to change. {TEST_PURCHASE}")
        await self.send(order.chat_id, "\n".join(lines), [Button("Watch these", data=f"watch:{order.id}")])

    async def _watch_candidates(self, order: StandingOrder) -> None:
        if order.status != "DRAFT" or order.max_price is None or not order.candidate_ids:
            return await self.send(order.chat_id, "That list is out of date. Tap Start watching on the latest card.", None)
        now = self.now(order.chat_id)
        try:
            quotes = await self.live_catalog.quotes(order.candidate_ids, now)
        except Channel3Error as e:
            log.warning("live price check failed: %s", e)
            return await self.send(order.chat_id, "I couldn't check prices just now. Please try again in a moment.", [Button("Watch these", data=f"watch:{order.id}")])
        if quotes:
            order.reference_price = min(q.price for q in quotes)
        await self._begin_watching(order, order.candidate_ids, quotes, now)

    async def _begin_watching(self, order: StandingOrder, ids: list[str], quotes: list[Quote], now: datetime) -> None:
        order.product_ids, order.status = ids, "WATCHING"
        quotes = sorted(quotes, key=lambda q: q.price)
        lines = [f"I'm watching these for {esc(order.title)}:"]
        lines += _quote_lines(quotes)
        options = eligible(order, quotes)
        order.last_low = options[0].price if options and options[0].price > order.max_price else None
        self.store.save_standing_order(order)
        self.store.add_event(order.id, now, f"Started watching {len(quotes)} listings")
        lines.append("\nI'll message you when something changes, and I'll ask before buying.")
        if self.demo_hints and not self.store.chat_live(order.chat_id):
            lines.append(f"\n<i>Demo: stores and prices are simulated, and today is {day(now)}. Send /ff to skip to the next change.</i>")
        await self.send(order.chat_id, "\n".join(lines), None)
        await self.evaluate(order, now)

    async def _not_yet(self, order: StandingOrder) -> None:
        if order.status != "WATCHING":
            return
        now = self.now(order.chat_id)
        self.store.add_event(order.id, now, f"You said not yet at {money(order.last_offer)}")
        await self.send(
            order.chat_id,
            "OK, I'll keep watching and only ask again if it gets cheaper. What would change your mind? "
            "For example, tell me a price below which I should just buy it.",
            None,
        )

    async def _cancel_tapped(self, order: StandingOrder) -> None:
        if order.status in ACTIVE:
            order.status = "CANCELLED"
            self.store.save_standing_order(order)
            self.store.add_event(order.id, self.now(order.chat_id), "Cancelled")
            await self.send(order.chat_id, f"Cancelled the standing order for {esc(order.title)}.", None)

    # --- Pause, resume, cancel by message ---

    async def _pause(self, chat_id: int, u: Understanding, now: datetime) -> None:
        order = self._find_order(chat_id, u)
        if order is None:
            return await self._no_order(chat_id)
        if order.status == "WATCHING":
            order.status = "PAUSED"
            self.store.save_standing_order(order)
            self.store.add_event(order.id, now, "Paused")
        await self.send(chat_id, f"Paused {esc(order.title)}. I won't buy anything or message you until you resume.", None)

    async def _resume(self, chat_id: int, u: Understanding, now: datetime) -> None:
        order = self._find_order(chat_id, u)
        if order is None:
            return await self._no_order(chat_id)
        if order.status == "PAUSED":
            order.status = "WATCHING"
            self.store.save_standing_order(order)
            self.store.add_event(order.id, now, "Resumed")
            await self.send(chat_id, f"Watching {esc(order.title)} again.", None)
            await self.evaluate(order, now)

    async def _cancel(self, chat_id: int, u: Understanding, now: datetime) -> None:
        order = self._find_order(chat_id, u)
        if order is None:
            return await self._no_order(chat_id)
        await self._cancel_tapped(order)

    # --- Permission for automatic buying ---

    async def _auto_buy_under(self, chat_id: int, u: Understanding, now: datetime) -> None:
        order = self._find_order(chat_id, u)
        limit = _price(u.auto_limit)
        if order is None or order.status != "WATCHING":
            return await self.send(chat_id, "Start watching something first, then you can let me buy it automatically.", None)
        if limit is None:
            return await self.send(chat_id, "What's the price below which I should just buy it?", None)
        if order.max_price is not None and limit > order.max_price:
            return await self.send(
                chat_id, f"That's above your {money(order.max_price)} limit. Tell me a new limit first if you'd like to raise it.", None
            )
        order.pending_auto_limit = limit
        self.store.save_standing_order(order)
        until = f", until {day(order.buy_by)}" if order.buy_by else ""
        if self.store.saved_paypal(chat_id):
            text = f"I'll buy automatically under <b>{money(limit)}</b>{until}. Is that right?"
            return await self.send(chat_id, text, [Button(f"Yes, buy under {money(limit)}", data=f"auto:{order.id}")])
        try:
            setup = await self.paypal.create_setup_token(
                return_url=f"{self.base_url}/paypal/vault/return",
                cancel_url=f"{self.base_url}/paypal/vault/cancel",
                description="Procureo: buy automatically within limits you set",
            )
        except PayPalError as e:
            log.warning("setup token failed: %s %s", e.status_code, e.body)
            return await self.send(chat_id, "PayPal isn't responding right now. Please try again in a moment.", None)
        self.store.add_setup_token(setup.setup_token_id, chat_id, order.id, limit)
        await self.send(
            chat_id,
            f"Got it. I'll buy automatically under <b>{money(limit)}</b>{until}.\n"
            "To do that, save PayPal as your payment method once. You can remove it any time.",
            [Button("Save PayPal", url=setup.approve_url)],
        )

    async def _confirm_auto(self, order: StandingOrder) -> None:
        if order.status == "WATCHING" and order.pending_auto_limit is not None:
            await self._turn_on_auto(order, order.pending_auto_limit)

    async def _turn_on_auto(self, order: StandingOrder, limit: Decimal) -> None:
        now = self.now(order.chat_id)
        order.auto_limit, order.pending_auto_limit = limit, None
        self.store.save_standing_order(order)
        self.store.add_event(order.id, now, f"You allowed automatic buying under {money(limit)}")
        await self.send(order.chat_id, f"✅ Automatic buying is on under <b>{money(limit)}</b>, for this standing order only.", None)
        await self.evaluate(order, now)

    async def on_paypal_saved(self, setup_token_id: str) -> bool:
        """The buyer approved saving PayPal. Called from the return page."""
        found = self.store.setup_token(setup_token_id)
        if found is None:
            return False
        chat_id, order_id, limit = found
        try:
            saved = await self.paypal.create_payment_token(setup_token_id)
        except PayPalError as e:
            log.warning("payment token failed: %s %s", e.status_code, e.body)
            await self.send(chat_id, "PayPal couldn't save your payment method, so automatic buying is still off.", None)
            return False
        self.store.delete_setup_token(setup_token_id)
        self.store.set_saved_paypal(chat_id, saved.payment_token_id, saved.email)
        order = self.store.standing_order(order_id)
        if order and order.status == "WATCHING":
            await self._turn_on_auto(order, limit)
        else:
            await self.send(chat_id, "✅ PayPal saved.", None)
        return True

    async def remove_saved_paypal(self, chat_id: int) -> None:
        token = self.store.saved_paypal(chat_id)
        if token is None:
            return await self.send(chat_id, "You don't have a saved PayPal.", None)
        try:
            await self.paypal.delete_payment_token(token)
        except PayPalError as e:
            log.warning("delete token failed: %s %s", e.status_code, e.body)
        self.store.set_saved_paypal(chat_id, None)
        now = self.now(chat_id)
        for order in self.store.standing_orders(chat_id):
            if order.automatic and order.status in ACTIVE:
                order.auto_limit = None
                self.store.save_standing_order(order)
                self.store.add_event(order.id, now, "Automatic buying turned off: PayPal removed")
        await self.send(chat_id, "Removed your saved PayPal. I'll ask before buying from now on.", None)

    # --- Watching ---

    async def fast_forward(self, chat_id: int, limit: timedelta = MAX_FAST_FORWARD) -> bool:
        """Advance this chat's clock hour by hour until the agent has something to say. True if it did.

        A live chat's clock can't be moved, so this just checks current prices right away.
        """
        if self.store.chat_live(chat_id):
            return await self.check_live(chat_id)
        now = self.now(chat_id)
        watching = [o for o in self.store.standing_orders(chat_id) if o.status == "WATCHING"]
        if not watching:
            return False
        end = now + limit
        while now < end:
            now += timedelta(hours=1)
            self.store.set_sim_now(chat_id, now)
            spoke = False
            for order in self.store.standing_orders(chat_id):
                spoke = await self.evaluate(order, now) or spoke
            if spoke:
                return True
        return False

    async def evaluate(self, order: StandingOrder, now: datetime) -> bool:
        """Act on a standing order at this moment. True if the user was told something."""
        if self.store.chat_live(order.chat_id):
            if order.status != "WATCHING":
                return False
            try:
                quotes = await self.live_catalog.quotes(order.product_ids, now)
            except Channel3Error as e:
                log.warning("live price check failed for %s: %s", order.id, e)
                return False  # unknown isn't the same as unavailable, so do nothing and try again later
            # The user may have paused, changed or cancelled the order while prices were being fetched.
            order = self.store.standing_order(order.id)
        else:
            quotes = self.catalog.quotes(order.product_ids, now)
        decision = decide(order, quotes, now)
        q = decision.quote
        match decision.action:
            case Action.UPDATE:
                return await self._update(order, q, now)
            case Action.ASK:
                return await self._ask(order, q, now)
            case Action.AUTO_BUY:
                return await self._auto_buy(order, q, now, len({quote.store for quote in eligible(order, quotes)}))
            case Action.LAST_CALL:
                return await self._last_call(order, q, now)
            case Action.EXPIRE:
                return await self._expire(order, now)
        return False

    async def _update(self, order: StandingOrder, q: Quote, now: datetime) -> bool:
        order.last_low = q.price
        self.store.save_standing_order(order)
        limit = order.auto_limit if order.automatic else order.max_price
        self.store.add_event(order.id, now, f"Lowest price {money(q.price)} at {q.store}")
        which = "your automatic limit" if order.automatic else "your limit"
        await self.send(
            order.chat_id,
            f"<b>{day(now)}</b>: lowest is {money(q.price)} at {esc(q.store)}. Still above {which} of {money(limit)}.",
            None,
        )
        return True

    async def _offer_button(self, order: StandingOrder, q: Quote) -> Button | None:
        try:
            url = await self.checkout.start(order.chat_id, q.price, f"{q.product.name} from {q.store}", standing_order_id=order.id)
        except PayPalError as e:
            log.warning("create order failed: %s %s", e.status_code, e.body)
            return None
        return Button("Buy now with PayPal", url=url)

    async def _ask(self, order: StandingOrder, q: Quote, now: datetime) -> bool:
        button = await self._offer_button(order, q)
        if button is None:
            return False  # try again on the next check
        lowest = " (the lowest so far)" if order.last_low is None or q.price < order.last_low else ""
        order.last_offer, order.last_low = q.price, q.price
        self.store.save_standing_order(order)
        self.store.add_event(order.id, now, f"Asked you about {money(q.price)} at {q.store}")
        await self.send(
            order.chat_id,
            f"<b>{money(q.price)} at {store_link(q)}</b>{lowest}, inside your {money(order.max_price)} limit.\n"
            f"{esc(q.product.name)}. {arrival(q)}" + (f"\n{TEST_PURCHASE}" if q.live else ""),
            [button, Button("Not yet", data=f"notyet:{order.id}")],
        )
        return True

    async def _last_call(self, order: StandingOrder, q: Quote | None, now: datetime) -> bool:
        order.last_call_sent = True
        self.store.save_standing_order(order)
        self.store.add_event(order.id, now, "Last day to buy in time")
        if q is None:
            await self.send(order.chat_id, f"Today is the last day to buy {esc(order.title)} in time, but nothing can arrive by {day(order.arrive_by)}.", None)
            return True
        if q.price > order.max_price:
            await self.send(
                order.chat_id,
                f"Today is the last day to buy {esc(order.title)} in time. The lowest price is {money(q.price)} at "
                f"{esc(q.store)}, above your {money(order.max_price)} limit, so I won't buy it. "
                "Tell me a new limit if you'd like to raise it.",
                None,
            )
            return True
        button = await self._offer_button(order, q)
        await self.send(
            order.chat_id,
            f"Today is the last day to buy {esc(order.title)} in time. The lowest price is <b>{money(q.price)}</b> at "
            f"{store_link(q)}. {arrival(q)}",
            [button] if button else None,
        )
        return True

    async def _expire(self, order: StandingOrder, now: datetime) -> bool:
        order.status = "EXPIRED"
        self.store.save_standing_order(order)
        self.store.add_event(order.id, now, "Stopped: too late to arrive in time")
        await self.send(order.chat_id, f"I've stopped watching {esc(order.title)}: it's too late for it to arrive by {day(order.arrive_by)}. Nothing was bought.", None)
        return True

    # --- Paying ---

    async def _auto_buy(self, order: StandingOrder, q: Quote, now: datetime, compared: int) -> bool:
        token = self.store.saved_paypal(order.chat_id)
        # Last line of defense: re-check everything right before spending, whatever led here.
        if order.status != "WATCHING" or token is None or not may_auto_buy(order, q.price):
            log.warning("refused automatic purchase for %s at %s", order.id, q.price)
            order.auto_limit = None
            self.store.save_standing_order(order)
            return False
        order.status = "BUYING"  # if anything crashes from here on, this order can't be charged again
        self.store.save_standing_order(order)
        try:
            result = await self.paypal.charge_saved(
                payment_token_id=token,
                amount=q.price,
                currency=self.currency,
                description=f"{q.product.name} from {q.store}"[:127],
                request_id=f"standing-order-{order.id}",
            )
            paid = result.get("status") == "COMPLETED"
        except PayPalError as e:
            log.warning("automatic payment failed: %s %s", e.status_code, e.body)
            result, paid = {}, False
        if not paid:
            order.status, order.auto_limit = "WATCHING", None
            self.store.save_standing_order(order)
            self.store.add_event(order.id, now, f"Automatic payment of {money(q.price)} failed; back to asking")
            await self.send(order.chat_id, f"I tried to buy at {money(q.price)} but PayPal declined it, so nothing was charged. I've turned automatic buying off and will ask first.", None)
            return True

        days_early = (order.arrive_by - q.arrives_on).days if order.arrive_by else None
        lowest = "the lowest price I'd seen" if order.last_low is None or q.price <= order.last_low else "the price I found"
        why = f"I compared {compared} {'stores' if compared != 1 else 'store'}. {money(q.price)} was {lowest}, under your {money(order.auto_limit)} limit"
        arrives = "should arrive about" if q.live else "it arrives"
        why += f", and {arrives} {days_early} days before {day(order.arrive_by)}." if days_early is not None else "."
        self._record_purchase(order, q.price, q.store, q.arrives_on, now, result.get("id"), capture_id_of(result), why)
        self.store.add_event(order.id, now, f"Bought automatically at {money(q.price)} from {q.store}")
        await self.send(
            order.chat_id,
            f"<b>{'Test purchase' if q.live else 'Bought'}: {esc(order.title)}, {money(q.price)}</b> at {store_link(q)}\n"
            f"{moment(now)}. You set: buy automatically under {money(order.auto_limit)}"
            + (f", by {day(order.buy_by)}" if order.buy_by else "")
            + f".\nPaid with your saved PayPal. {arrival(q)}" + (f"\n{TEST_PURCHASE}" if q.live else ""),
            [
                Button("Receipt", data=f"receipt:{order.id}"),
                Button("Why this one?", data=f"why:{order.id}"),
                Button("Cancel order", data=f"refund:{order.id}"),
            ],
        )
        return True

    def _record_purchase(self, order, price, store, arrives_on, now, paypal_order_id, capture_id, why) -> None:
        order.status = "BOUGHT"
        order.bought_price, order.bought_store, order.bought_at = price, store, now
        order.arrives_on, order.paypal_order_id, order.capture_id, order.why = arrives_on, paypal_order_id, capture_id, why
        self.store.save_standing_order(order)

    async def _quotes_or_none(self, order: StandingOrder, now: datetime) -> list[Quote]:
        if not self.store.chat_live(order.chat_id):
            return self.catalog.quotes(order.product_ids, now)
        try:
            return await self.live_catalog.quotes(order.product_ids, now)
        except Channel3Error as e:
            log.warning("live price check failed: %s", e)
            return []

    async def on_checkout_paid(self, paid: Order, capture_id: str | None) -> None:
        """The buyer completed a PayPal checkout from an offer."""
        order = self.store.standing_order(paid.standing_order_id)
        if order is None:
            return
        now = self.now(order.chat_id)
        if order.status != "WATCHING":
            # Already bought or cancelled (say, an old Pay link was used): don't keep the money.
            if capture_id:
                await self.paypal.refund_capture(capture_id)
            await self.send(order.chat_id, f"You paid {money(paid.amount)} but {esc(order.title)} is already {order.status.lower()}, so I refunded it.", None)
            return
        q = next((q for q in await self._quotes_or_none(order, now) if paid.description == f"{q.product.name} from {q.store}"), None)
        store, arrives_on = (q.store, q.arrives_on) if q else (paid.description, None)
        why = f"You approved this offer at {money(paid.amount)}, inside your {money(order.max_price)} limit."
        self._record_purchase(order, paid.amount, store, arrives_on, now, paid.order_id, capture_id, why)
        self.store.add_event(order.id, now, f"You approved and paid {money(paid.amount)} at {store}")
        await self.send(
            order.chat_id,
            f"✅ <b>Paid {money(paid.amount)}</b> for {esc(order.title)} at {esc(store)}."
            + (f" Arrives {day(arrives_on)}." if arrives_on else ""),
            [Button("Receipt", data=f"receipt:{order.id}"), Button("Cancel order", data=f"refund:{order.id}")],
        )

    async def _why(self, order: StandingOrder) -> None:
        await self.send(order.chat_id, esc(order.why) or "I haven't bought anything for this order.", None)

    async def _receipt(self, order: StandingOrder) -> None:
        if order.bought_price is None:
            return
        await self.send(
            order.chat_id,
            f"<b>Receipt</b>\n{esc(order.title)}\n{money(order.bought_price)} at {esc(order.bought_store)}\n"
            f"PayPal order: <code>{esc(order.paypal_order_id)}</code>\nStatus: {order.status.lower()}",
            None,
        )

    async def _refund(self, order: StandingOrder) -> None:
        if order.status != "BOUGHT" or not order.capture_id:
            return
        try:
            await self.paypal.refund_capture(order.capture_id)
        except PayPalError as e:
            log.warning("refund failed: %s %s", e.status_code, e.body)
            return await self.send(order.chat_id, "PayPal couldn't process the refund. Please try again in a moment.", None)
        order.status = "REFUNDED"
        self.store.save_standing_order(order)
        now = self.now(order.chat_id)
        self.store.add_event(order.id, now, f"Order cancelled; {money(order.bought_price)} refunded")
        await self.send(order.chat_id, f"Cancelled, and refunded {money(order.bought_price)} to your PayPal.", None)

    # --- Reporting ---

    async def show_orders(self, chat_id: int) -> None:
        orders = self.store.standing_orders(chat_id)[-5:]
        if not orders:
            return await self._no_order(chat_id)
        for order in orders:
            lines = [f"<b>{esc(order.title)}</b>: {order.status.lower()}"]
            lines += [f"• {day(at)}: {esc(text)}" for at, text in self.store.events(order.id)]
            buttons = None
            if order.status in ("WATCHING", "PAUSED"):
                buttons = [Button("Cancel", data=f"cancel:{order.id}")]
            elif order.status == "BOUGHT":
                buttons = [Button("Receipt", data=f"receipt:{order.id}"), Button("Why this one?", data=f"why:{order.id}")]
            await self.send(chat_id, "\n".join(lines), buttons)

    async def reset(self, chat_id: int) -> None:
        """Start the demo over for this chat."""
        token = self.store.saved_paypal(chat_id)
        if token:
            try:
                await self.paypal.delete_payment_token(token)
            except PayPalError as e:
                log.warning("delete token failed: %s %s", e.status_code, e.body)
        self.store.reset_chat(chat_id)


def _price(value: float | None) -> Decimal | None:
    if value is None or value <= 0:
        return None
    return Decimal(str(value)).quantize(Decimal("0.01"))


def _iso_date(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None
