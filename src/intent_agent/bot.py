"""Telegram handlers. `agent` and `checkout` are injected through the dispatcher."""

import logging
import re
from datetime import timedelta

import openai
from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from .agent import MAX_FAST_FORWARD, Agent, Button, day
from .brain import Image
from .checkout import Checkout, parse_amount
from .paypal import PayPalError

log = logging.getLogger(__name__)
router = Router()

HELP = (
    "I'm a shopping agent. Tell me what you need and the most you'll pay. I'll find it, wait for a good price, "
    "and buy it with PayPal, always within your limits.\n\n"
    "Just describe it in your own words. For example:\n"
    "“Noise-cancelling over-ear headphones for Sam's birthday. Needs to arrive by Dec 14. Under $200.”\n\n"
    "A photo, video or link is optional and helps me be precise. I'll ask before buying unless you tell me otherwise.\n\n"
    "/orders shows what I'm doing and what I've done."
)
DEMO_HELP = (
    "\n\n<i>Demo: stores and prices are simulated, and payments use the PayPal sandbox. "
    "/ff skips to the next change, and /reset starts over.</i>"
)

# Buttons that should stop working once tapped.
ONE_TIME_ACTIONS = {"start", "notyet", "auto", "refund", "cancel"}


def keyboard(buttons: list[Button] | None) -> InlineKeyboardMarkup | None:
    if not buttons:
        return None
    rows = [[InlineKeyboardButton(text=b.text, callback_data=b.data, url=b.url)] for b in buttons]
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(CommandStart())
@router.message(Command("help"))
async def on_start(message: Message, agent: Agent) -> None:
    await message.answer(HELP + (DEMO_HELP if agent.demo_hints else ""), parse_mode="HTML")


@router.message(Command("buy"))
async def on_buy(message: Message, command: CommandObject, checkout: Checkout) -> None:
    """Test command for the checkout flow on its own."""
    amount_text, _, description = (command.args or "").partition(" ")
    try:
        amount = parse_amount(amount_text)
    except ValueError as e:
        await message.answer(f"Usage: /buy <amount> <description>\n({e})")
        return
    description = description.strip() or "Test purchase"

    try:
        approve_url = await checkout.start(message.chat.id, amount, description)
    except PayPalError as e:
        log.exception("create order failed: %s", e.body)
        await message.answer("Sorry, PayPal didn't accept that order. Please try again.")
        return

    button = InlineKeyboardButton(text=f"Pay {amount} {checkout.currency} with PayPal", url=approve_url)
    await message.answer(
        f"Ready to check out: “{description}” for {amount} {checkout.currency}.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[button]]),
    )


@router.message(Command("orders"))
async def on_orders(message: Message, agent: Agent) -> None:
    await agent.show_orders(message.chat.id)


def parse_span(text: str | None) -> timedelta:
    """`/ff` argument: 3d or 12h. With no argument, run until something happens."""
    match = re.fullmatch(r"(\d+)\s*([dh])", (text or "").strip().lower())
    if not match:
        return MAX_FAST_FORWARD
    amount = int(match[1])
    return min(timedelta(days=amount) if match[2] == "d" else timedelta(hours=amount), MAX_FAST_FORWARD)


@router.message(Command("ff"))
async def on_fast_forward(message: Message, command: CommandObject, agent: Agent) -> None:
    """Demo control: advance this chat's simulated clock. The command is deleted so it stays out of the video."""
    chat_id = message.chat.id
    try:
        await message.delete()
    except TelegramBadRequest:
        pass
    if not await agent.fast_forward(chat_id, parse_span(command.args)) and agent.demo_hints:
        await message.answer(f"<i>Demo: nothing to report. It's now {day(agent.store.sim_now(chat_id))}.</i>", parse_mode="HTML")


@router.message(Command("reset"))
async def on_reset(message: Message, agent: Agent) -> None:
    """Demo control: forget this chat's orders, clock and saved PayPal."""
    chat_id = message.chat.id
    try:
        await message.delete()
    except TelegramBadRequest:
        pass
    await agent.reset(chat_id)
    if agent.demo_hints:
        await message.answer("<i>Demo: started over.</i>", parse_mode="HTML")


@router.callback_query(F.data)
async def on_button(callback: CallbackQuery, agent: Agent) -> None:
    await callback.answer()
    message = callback.message
    if message is None:
        return
    if callback.data.partition(":")[0] in ONE_TIME_ACTIONS:
        try:
            await message.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest:
            pass
    await agent.on_button(message.chat.id, callback.data)


@router.message(F.voice | F.audio)
async def on_audio(message: Message) -> None:
    await message.answer("I can't listen to audio yet. Please type it, and add a photo, video or link if that helps.")


async def _attachments(message: Message, bot: Bot) -> tuple[list[Image], str]:
    """Images to show the model, and a note about what they are."""
    source, note = None, ""
    if message.photo:
        source = message.photo[-1]
    elif message.document and (message.document.mime_type or "").startswith("image/"):
        source = message.document
    else:
        # For video, the model sees one preview frame.
        video = message.video or message.animation or message.video_note
        if video and video.thumbnail:
            source, note = video.thumbnail, "\n[The attached image is a preview frame from their video.]"
    if source is None:
        return [], ""
    data = await bot.download(source)
    media_type = "image/jpeg"
    if message.document and message.document.mime_type in ("image/png", "image/gif", "image/webp"):
        media_type = message.document.mime_type
    return [Image(data.read(), media_type)], note


@router.message(F.text | F.photo | F.video | F.animation | F.video_note | F.document)
async def on_message(message: Message, bot: Bot, agent: Agent) -> None:
    chat_id = message.chat.id
    await bot.send_chat_action(chat_id, "typing")
    text = message.text or message.caption or ""
    try:
        images, note = await _attachments(message, bot)
        await agent.on_message(chat_id, text + note, images)
    except openai.APIError as e:
        log.exception("model call failed: %s", e)
        await message.answer("Sorry, I couldn't process that just now. Please try again in a moment.")

