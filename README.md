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
- [ ] Saved payment method (vault) for purchases the agent makes on its own (verified in the sandbox with `scripts/vault_check.py`; not yet in the bot)

## PayPal accounts: which login goes where

Testing involves three different PayPal logins. Mixing them up is the most common way to get stuck.

| Account | What it is | Where you use it |
| --- | --- | --- |
| **Your own PayPal account** | Your real account | Only to log in to [developer.paypal.com](https://developer.paypal.com). Never on a sandbox page. |
| **Sandbox business account** (`sb-…@business.example.com`) | A fake merchant. It receives the money. | Chosen when you create the app; you normally don't log in with it. |
| **Sandbox personal account** (`sb-…@personal.example.com`) | A fake buyer with fake money | Log in with it on every **sandbox.paypal.com** page the bot or scripts open, to pay or to save PayPal. |

Setup on developer.paypal.com, with the dashboard switched to **Sandbox**:

1. **Testing tools → Sandbox accounts** lists the sandbox accounts. PayPal usually creates one business and one
   personal account for you; if not, use **Create account**. Open an account's **⋮ → View/edit account** to see
   or change its password.
2. **Apps & Credentials → Create App**, type **Merchant** (not Platform), and select the sandbox
   **business** account. Copy the app's client ID and secret into `.env`.

Common problems:

- **The sandbox page asks for a phone code that never arrives.** You probably entered your real PayPal login on
  a sandbox page. Use the sandbox personal account instead.
- **"This PayPal account is associated with the merchant you're trying to pay."** You're logged in as the business
  account, or the app was linked to the personal account. Log in as the personal account, and check which sandbox
  account the app shows on its page.
- **The sandbox page skips the login and shows that error again.** The browser is still logged in to
  sandbox.paypal.com as the wrong account, even in a private window if any private window from earlier is still open.
  Log out on sandbox.paypal.com, or close all private windows and paste the link into a new one.

## Run locally

1. Create a bot with [@BotFather](https://t.me/BotFather) and copy its token.
2. Set up the PayPal sandbox app and accounts as described in the section above.
3. Configure and run:

   ```bash
   cp .env.example .env   # then fill in the values
   uv run intent-agent
   ```

   With `PUBLIC_BASE_URL=http://localhost:8000` the bot uses long polling, so no public URL is needed.
   Open the Pay link on the same machine so PayPal can redirect back to localhost.
4. In Telegram, send `/buy 12.50 Coffee beans` to your bot, tap the button, and pay with the sandbox **personal** account.

## Check saved PayPal (vault) in the sandbox

Automatic buying depends on PayPal letting the agent pay later with a payment method the buyer saved once.
To check that against the real sandbox:

1. Use a **Merchant** app. PayPal's docs mention a **Vault** setting in the app's features; we found no such
   toggle on a new Merchant sandbox app, and saving PayPal worked anyway. If the first step fails, look for
   that setting.
2. Run the check. It opens a sandbox.paypal.com page; log in with the sandbox **personal** account and approve.

   ```bash
   uv run python scripts/vault_check.py
   ```

   It saves PayPal, then pays $1.00 with no buyer present, and prints PASS or FAIL for each step.
   `--pay-only` pays again with the saved PayPal, and `--delete` removes it.

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
