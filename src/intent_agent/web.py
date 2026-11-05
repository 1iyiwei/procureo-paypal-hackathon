"""FastAPI app: Telegram webhook (or long polling locally) plus the PayPal return/cancel pages."""

import asyncio
import contextlib
import html
import logging
from contextlib import asynccontextmanager

from aiogram import Bot, Dispatcher
from aiogram.types import LinkPreviewOptions, Update
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse

from .agent import Agent, Button
from .bot import AccessGate, keyboard, router
from .brain import OpenAIBrain
from .catalog import Catalog
from .checkout import CaptureResult, Checkout
from .config import Settings
from .live import LiveCatalog
from .paypal import PayPalClient
from .store import Store

log = logging.getLogger(__name__)

WEBHOOK_PATH = "/telegram/webhook"
NO_PREVIEW = LinkPreviewOptions(is_disabled=True)  # store links shouldn't expand into big previews


def create_app(
    settings: Settings | None = None, *, checkout: Checkout | None = None, agent: Agent | None = None
) -> FastAPI:
    """Build the app. Passing `checkout` skips Telegram and PayPal setup (used by tests)."""
    settings = settings or Settings()
    pending_updates: set[asyncio.Task] = set()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if checkout is not None:
            app.state.checkout = checkout
            app.state.agent = agent
            app.state.bot_username = None
            yield
            return

        bot = Bot(settings.telegram_bot_token)
        paypal = PayPalClient(settings.paypal_client_id, settings.paypal_client_secret, settings.paypal_base_url)
        store = Store(settings.database_path)

        async def notify(chat_id: int, text: str) -> None:
            await bot.send_message(chat_id, text)

        async def send(chat_id: int, text: str, buttons: list[Button] | None) -> None:
            await bot.send_message(
                chat_id, text, parse_mode="HTML", reply_markup=keyboard(buttons), link_preview_options=NO_PREVIEW
            )

        live_catalog = LiveCatalog(settings.channel3_api_key, settings.currency) if settings.channel3_api_key else None
        app.state.checkout = Checkout(paypal, store, notify, settings.public_base_url, settings.currency)
        app.state.agent = Agent(
            store=store,
            paypal=paypal,
            brain=OpenAIBrain(settings.openai_api_key, settings.openai_model),
            catalog=Catalog(),
            checkout=app.state.checkout,
            send=send,
            public_base_url=settings.public_base_url,
            currency=settings.currency,
            demo_hints=settings.demo_hints,
            live_catalog=live_catalog,
        )
        app.state.bot_username = (await bot.get_me()).username

        dp = Dispatcher()
        if settings.access_code:
            dp.update.outer_middleware(AccessGate(settings.access_code, store, settings.demo_hints))
        dp.include_router(router)
        dp["checkout"] = app.state.checkout
        dp["agent"] = app.state.agent
        app.state.bot, app.state.dp = bot, dp

        async def watch_live_prices() -> None:
            while True:
                await asyncio.sleep(settings.live_check_minutes * 60)
                try:
                    await app.state.agent.check_live()
                except Exception:
                    log.exception("live price check failed")  # keep the loop alive for the next round

        live_watcher = asyncio.create_task(watch_live_prices()) if live_catalog else None
        polling = None
        if settings.use_webhook:
            await bot.set_webhook(
                settings.public_base_url.rstrip("/") + WEBHOOK_PATH,
                secret_token=settings.telegram_webhook_secret or None,
                drop_pending_updates=True,
            )
            log.info("Telegram webhook mode")
        else:
            await bot.delete_webhook(drop_pending_updates=True)
            polling = asyncio.create_task(dp.start_polling(bot, handle_signals=False))
            log.info("Telegram long-polling mode")
        try:
            yield
        finally:
            if live_watcher:
                live_watcher.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await live_watcher
                await live_catalog.aclose()
            if polling:
                await dp.stop_polling()
                with contextlib.suppress(asyncio.CancelledError):
                    await polling
            await paypal.aclose()
            await bot.session.close()
            store.close()

    app = FastAPI(lifespan=lifespan)

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"ok": True}

    @app.post(WEBHOOK_PATH)
    async def telegram_webhook(
        request: Request,
        x_telegram_bot_api_secret_token: str | None = Header(default=None),
    ) -> dict:
        if settings.telegram_webhook_secret and x_telegram_bot_api_secret_token != settings.telegram_webhook_secret:
            raise HTTPException(status_code=403)
        update = Update.model_validate(await request.json(), context={"bot": request.app.state.bot})
        # Reply right away: model calls can outlast the time Telegram waits before sending an update again.
        task = asyncio.create_task(request.app.state.dp.feed_update(request.app.state.bot, update))
        pending_updates.add(task)
        task.add_done_callback(pending_updates.discard)
        return {"ok": True}

    # PayPal appends ?token=<order id>&PayerID=... when redirecting the buyer back.
    @app.get("/paypal/return", response_class=HTMLResponse)
    async def paypal_return(request: Request, token: str = "") -> HTMLResponse:
        result = await request.app.state.checkout.complete(token)
        messages = {
            CaptureResult.COMPLETED: ("Payment complete", "Your payment went through."),
            CaptureResult.ALREADY_DONE: ("Already processed", "This checkout was already handled."),
            CaptureResult.UNKNOWN_ORDER: ("Unknown checkout", "We couldn't find this checkout."),
            CaptureResult.FAILED: ("Payment failed", "PayPal couldn't complete the payment."),
        }
        title, body = messages[result]
        status = 404 if result is CaptureResult.UNKNOWN_ORDER else 200
        return _page(title, body, request.app.state.bot_username, status)

    @app.get("/paypal/cancel", response_class=HTMLResponse)
    async def paypal_cancel(request: Request, token: str = "") -> HTMLResponse:
        await request.app.state.checkout.cancel(token)
        return _page("Checkout cancelled", "No payment was taken.", request.app.state.bot_username)

    # After saving PayPal, the buyer returns with ?approval_token_id=<setup token id>.
    @app.get("/paypal/vault/return", response_class=HTMLResponse)
    async def vault_return(request: Request, approval_token_id: str = "") -> HTMLResponse:
        saved = await request.app.state.agent.on_paypal_saved(approval_token_id)
        title, body = (
            ("PayPal saved", "Automatic buying is on, within the limit you set.")
            if saved
            else ("PayPal not saved", "This link was already used or has expired. Automatic buying was not changed.")
        )
        return _page(title, body, request.app.state.bot_username)

    @app.get("/paypal/vault/cancel", response_class=HTMLResponse)
    async def vault_cancel(request: Request) -> HTMLResponse:
        return _page("PayPal not saved", "Automatic buying was not turned on.", request.app.state.bot_username)

    return app


def _page(title: str, body: str, bot_username: str | None, status: int = 200) -> HTMLResponse:
    back = (
        f'<p><a href="https://t.me/{html.escape(bot_username)}">Back to Telegram</a></p>'
        if bot_username
        else "<p>You can return to Telegram now.</p>"
    )
    return HTMLResponse(
        f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{html.escape(title)}</title>
<style>body{{font:16px system-ui,sans-serif;max-width:28rem;margin:4rem auto;padding:0 1rem;text-align:center}}</style>
</head><body><h1>{html.escape(title)}</h1><p>{html.escape(body)}</p>{back}</body></html>""",
        status_code=status,
    )
