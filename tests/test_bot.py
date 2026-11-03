from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.filters import CommandObject

from intent_agent.bot import on_buy
from intent_agent.checkout import Checkout
from intent_agent.store import Store
from tests.test_checkout import FakePayPal


def make_message():
    return SimpleNamespace(chat=SimpleNamespace(id=7), answer=AsyncMock())


async def test_buy_sends_pay_button():
    checkout = Checkout(FakePayPal(), Store(":memory:"), AsyncMock(), "https://agent.example")
    msg = make_message()
    await on_buy(msg, CommandObject(command="buy", args="12.5 Coffee beans"), checkout)

    text = msg.answer.call_args.args[0]
    button = msg.answer.call_args.kwargs["reply_markup"].inline_keyboard[0][0]
    assert "Coffee beans" in text and "12.50" in text
    assert button.url.endswith("token=ORDER1")
    assert checkout.store.get_order("ORDER1").amount == Decimal("12.50")


async def test_buy_rejects_bad_amount():
    checkout = Checkout(FakePayPal(), Store(":memory:"), AsyncMock(), "https://agent.example")
    msg = make_message()
    await on_buy(msg, CommandObject(command="buy", args="lots"), checkout)
    assert msg.answer.call_args.args[0].startswith("Usage:")
    assert checkout.store.get_order("ORDER1") is None
