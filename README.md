# [PayPal AI Hackathon](https://paypalaihackathon.devpost.com/)

**Procureo** is a Telegram shopping agent. You describe what you need once, in text, optionally
adding photos, videos or links, and set your limits. The agent finds the item, waits for the right price,
and buys it through PayPal, never going past those limits.
Background is in [docs/ideation.md](docs/ideation.md); the product pitch is in [docs/submission.md](docs/submission.md) and the demo video plan in [docs/storyboard.md](docs/storyboard.md).

## Status

Built: describing a need in text with an optional photo, video frame or link; a standing order with a spending limit
and deadline; matching against a mock catalog; price watching on a simulated clock; asking before buying with one-tap
PayPal checkout; opt-in automatic buying with a saved PayPal; "Why this one?"; refunds; an `/orders` timeline.

The stores, products and prices are simulated. Voice notes aren't supported.

**Who can do what.** The model only proposes. It can draft a standing order or suggest automatic buying, but only a
button tap can start watching, raise a limit or turn on automatic buying, and code re-checks the limits right before
every payment. Text inside photos and web pages can't change any of this.

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
4. In Telegram, tell your bot what you need, for example: *Noise-cancelling over-ear headphones for Sam's birthday.
   Needs to arrive by Dec 14. Under $200.* Tap **Start watching**, then send `/ff` to skip to the next price change.
   Pay or save PayPal with the sandbox **personal** account.

Demo commands: `/ff` skips to the next thing the agent has to say (`/ff 3d` for at most 3 days), `/reset` starts this
chat over, and `/buy 12.50 Coffee beans` tests checkout alone. Each chat has its own simulated clock, starting Nov 16, 2026.
Values in `.env` take priority over variables exported in your shell.

To run the whole storyboard without Telegram, against the real model and PayPal sandbox (it needs the saved PayPal
from the check below, and `--scripted` skips the model):

```bash
uv run python scripts/demo_dry_run.py
```

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
| `src/intent_agent/agent.py` | The standing order conversation and purchase flow |
| `src/intent_agent/watcher.py` | Pure rules: when to update, ask, buy automatically or stop |
| `src/intent_agent/brain.py` | OpenAI: understanding messages and attachments, matching products |
| `src/intent_agent/catalog.py` | Mock stores, products and scripted prices |
| `src/intent_agent/paypal.py` | PayPal REST client (orders, saved PayPal, refunds) |
| `src/intent_agent/checkout.py` | Checkout flow: start an order for a chat, capture it on return, notify |
| `src/intent_agent/store.py` | SQLite storage for orders, standing orders and timelines |
| `src/intent_agent/bot.py` | Telegram command handlers |
| `src/intent_agent/web.py` | FastAPI app: Telegram webhook or polling, PayPal return and cancel pages |
