# [PayPal AI Hackathon](https://paypalaihackathon.devpost.com/)

**Standing Order** is a Telegram shopping agent. You describe what you need once, in text, optionally
adding photos, videos or links, and set your limits. The agent finds the item, waits for the right price,
and buys it through PayPal, never going past those limits.
Background is in [docs/ideation.md](docs/ideation.md); the product pitch is in [docs/submission.md](docs/submission.md) and the demo video plan in [docs/storyboard.md](docs/storyboard.md).

## Status

- [x] Telegram bot with `/buy <amount> <description>` that creates a PayPal sandbox order and sends a Pay button
- [x] PayPal return page that captures the payment (once, even if reloaded) and confirms it in the chat
- [ ] Text request, with optional photos, videos or links, to a structured request
- [ ] Product sourcing
- [ ] Mandates with spending limits, price watching and a simulated clock
- [ ] Saved payment method (vault) for purchases the agent makes on its own

## Run locally

1. Create a bot with [@BotFather](https://t.me/BotFather) and copy its token.
2. At [developer.paypal.com](https://developer.paypal.com/dashboard/applications/sandbox), create a sandbox app and
   copy its client ID and secret. Under **Testing tools → Sandbox accounts**, note the personal (buyer) account
   to log in with at checkout.
3. Configure and run:

   ```bash
   cp .env.example .env   # then fill in the values
   uv run intent-agent
   ```

   With `PUBLIC_BASE_URL=http://localhost:8000` the bot uses long polling, so no public URL is needed.
   Open the Pay link on the same machine so PayPal can redirect back to localhost.
4. In Telegram, send `/buy 12.50 Coffee beans` to your bot, tap the button, and pay with the sandbox buyer account.

## Tests

```bash
uv run pytest
```

PayPal and Telegram are mocked, so tests need no credentials.

## Deploy on Render

[render.yaml](render.yaml) defines one web service with a small disk for SQLite. Set `PUBLIC_BASE_URL` to the
service's `https://…onrender.com` URL; when it is https the bot switches to webhook mode automatically.
Use a paid instance: free instances sleep when idle, which would miss price checks, and the demo must
stay up through judging (Dec 1–15).

## Layout

| File | Role |
| --- | --- |
| `src/intent_agent/paypal.py` | PayPal REST client (OAuth token, create, capture and get order) |
| `src/intent_agent/checkout.py` | Checkout flow: start an order for a chat, capture it on return, notify |
| `src/intent_agent/store.py` | SQLite storage for orders |
| `src/intent_agent/bot.py` | Telegram command handlers |
| `src/intent_agent/web.py` | FastAPI app: Telegram webhook or polling, PayPal return and cancel pages |
