from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram import Bot, Dispatcher, Router
from aiogram.filters import CommandObject
from aiogram.methods import AnswerCallbackQuery, DeleteMessage, SendMessage
from aiogram.types import CallbackQuery, Chat, Message as TgMessage, Update, User

from intent_agent.bot import AccessGate, on_buy
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


class TestAccessGate:
    """Through a real dispatcher, so these also show the gate runs before any handler."""

    def setup_method(self):
        self.store = Store(":memory:")
        self.bot = Bot("42:TEST")
        self.sent = []

        async def session(bot, method, timeout=None):
            self.sent.append(method)
            if isinstance(method, SendMessage):
                return TgMessage(message_id=9, date=0, chat=Chat(id=7, type="private"), text=method.text)
            return True

        self.bot.session = session
        self.handled = []
        router = Router()

        @router.message()
        async def on_message(message: TgMessage):
            self.handled.append(message.text)

        @router.callback_query()
        async def on_button(query: CallbackQuery):
            self.handled.append(query.data)

        self.dp = Dispatcher()
        self.dp.update.outer_middleware(AccessGate("s3cret", self.store, demo_hints=False))
        self.dp.include_router(router)

    async def say(self, text=None, **kwargs):
        chat = Chat(id=7, type="private")
        message = TgMessage(message_id=1, date=0, chat=chat, text=text, **kwargs)
        await self.dp.feed_update(self.bot, Update(update_id=1, message=message))

    async def tap(self, data="refund:abc"):
        user = User(id=7, is_bot=False, first_name="J")
        message = TgMessage(message_id=1, date=0, chat=Chat(id=7, type="private"), text="x")
        query = CallbackQuery(id="1", from_user=user, chat_instance="c", data=data, message=message)
        await self.dp.feed_update(self.bot, Update(update_id=2, callback_query=query))

    async def test_nothing_reaches_the_bot_without_the_code(self):
        for text in ["Buy me headphones", "/ff", "/start", "/start wrong", "S3CRET", None]:
            await self.say(text)
        await self.tap()
        assert self.handled == []
        assert not self.store.chat_allowed(7)
        assert isinstance(self.sent[-1], AnswerCallbackQuery)

    async def test_the_code_opens_the_chat_and_is_deleted(self):
        await self.say("s3cret")
        assert self.store.chat_allowed(7)
        assert self.handled == []  # the code itself never goes to the model
        assert any(isinstance(m, DeleteMessage) for m in self.sent)
        await self.say("Buy me headphones")
        await self.tap()
        assert self.handled == ["Buy me headphones", "refund:abc"]

    async def test_a_start_link_carries_the_code(self):
        await self.say("/start s3cret")
        assert self.store.chat_allowed(7)

    async def test_reset_does_not_lock_the_chat_again(self):
        await self.say("s3cret")
        self.store.reset_chat(7)
        assert self.store.chat_allowed(7)
