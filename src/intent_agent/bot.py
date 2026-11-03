"""Telegram handlers. The checkout service is injected through the dispatcher as `checkout`."""

import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from .checkout import Checkout, parse_amount
from .paypal import PayPalError

log = logging.getLogger(__name__)
router = Router()

HELP = (
    "I'm a shopping agent that buys on your behalf, within limits you set.\n\n"
    "For now you can test checkout:\n"
    "/buy <amount> <description> (for example /buy 12.50 Coffee beans)\n\n"
    "Coming soon: tell me what you need in your own words, and add photos, videos or links if they help."
)


@router.message(CommandStart())
@router.message(Command("help"))
async def on_start(message: Message) -> None:
    await message.answer(HELP)


@router.message(Command("buy"))
async def on_buy(message: Message, command: CommandObject, checkout: Checkout) -> None:
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


@router.message(F.photo)
async def on_photo(message: Message) -> None:
    await message.answer("Got your photo. I can't use attachments yet, but soon they'll help refine your request.")
