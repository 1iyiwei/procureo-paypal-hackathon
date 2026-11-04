"""Checkout flow: create a PayPal order for a chat, then capture it when the buyer returns."""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum

from .paypal import PayPalClient, PayPalError, capture_id_of
from .store import Order, Store

log = logging.getLogger(__name__)

# Upper bound for a single demo purchase; the mandate's spending cap will replace this.
MAX_AMOUNT = Decimal("1000.00")

Notify = Callable[[int, str], Awaitable[None]]
OnPaid = Callable[[Order, str | None], Awaitable[None]]  # order, capture id


class CaptureResult(Enum):
    COMPLETED = "completed"
    ALREADY_DONE = "already_done"
    UNKNOWN_ORDER = "unknown_order"
    FAILED = "failed"


def parse_amount(text: str) -> Decimal:
    try:
        amount = Decimal(text.strip().lstrip("$")).quantize(Decimal("0.01"))
    except InvalidOperation:
        raise ValueError(f"{text!r} is not an amount") from None
    if not (Decimal("0") < amount <= MAX_AMOUNT):
        raise ValueError(f"amount must be between 0.01 and {MAX_AMOUNT}")
    return amount


@dataclass
class Checkout:
    paypal: PayPalClient
    store: Store
    notify: Notify
    public_base_url: str
    currency: str = "USD"
    on_paid: OnPaid | None = None  # called instead of `notify` for standing-order purchases

    async def start(
        self, chat_id: int, amount: Decimal, description: str, standing_order_id: str | None = None
    ) -> str:
        """Create an order and return the PayPal approval URL to send to the buyer."""
        base = self.public_base_url.rstrip("/")
        created = await self.paypal.create_order(
            amount=amount,
            currency=self.currency,
            description=description,
            return_url=f"{base}/paypal/return",
            cancel_url=f"{base}/paypal/cancel",
            reference_id=str(chat_id),
        )
        self.store.add_order(
            Order(
                order_id=created.order_id,
                chat_id=chat_id,
                description=description,
                amount=amount,
                currency=self.currency,
                status="CREATED",
                standing_order_id=standing_order_id,
            )
        )
        return created.approve_url

    async def complete(self, order_id: str) -> CaptureResult:
        """Capture an approved order. Safe to call repeatedly (e.g. the buyer reloads the return page)."""
        order = self.store.get_order(order_id)
        if order is None:
            return CaptureResult.UNKNOWN_ORDER
        # Claim the order so two concurrent requests cannot both capture it.
        if not self.store.transition(order_id, "CREATED", "CAPTURING"):
            return CaptureResult.ALREADY_DONE

        capture_id = None
        try:
            result = await self.paypal.capture_order(order_id)
            status = result.get("status")
            capture_id = capture_id_of(result)
        except PayPalError as e:
            if _issue(e) == "ORDER_ALREADY_CAPTURED":
                status = "COMPLETED"
            else:
                log.warning("capture failed for %s: %s %s", order_id, e.status_code, e.body)
                status = None

        if status == "COMPLETED":
            self.store.transition(order_id, "CAPTURING", "COMPLETED")
            if order.standing_order_id and self.on_paid:
                await self.on_paid(order, capture_id)
                return CaptureResult.COMPLETED
            await self.notify(
                order.chat_id,
                f"✅ Paid {order.amount} {order.currency} for “{order.description}”.\n"
                f"PayPal order {order_id}",
            )
            return CaptureResult.COMPLETED

        self.store.transition(order_id, "CAPTURING", "FAILED")
        await self.notify(order.chat_id, f"⚠️ Payment for “{order.description}” did not go through.")
        return CaptureResult.FAILED

    async def cancel(self, order_id: str) -> None:
        order = self.store.get_order(order_id)
        if order and self.store.transition(order_id, "CREATED", "CANCELLED"):
            await self.notify(order.chat_id, f"Checkout for “{order.description}” was cancelled.")


def _issue(e: PayPalError) -> str | None:
    if isinstance(e.body, dict):
        details = e.body.get("details") or []
        if details and isinstance(details[0], dict):
            return details[0].get("issue")
    return None
