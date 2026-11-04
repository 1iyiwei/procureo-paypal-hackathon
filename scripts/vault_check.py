"""Check, against the real PayPal sandbox, that the agent can save PayPal once and pay later on its own.

    uv run python scripts/vault_check.py               # save PayPal (browser approval), then pay $1.00
    uv run python scripts/vault_check.py --pay-only    # pay again with the PayPal saved last time
    uv run python scripts/vault_check.py --delete      # remove the saved PayPal

Needs PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET (sandbox) in .env, and "Vault" enabled for the sandbox app.
"""

import argparse
import asyncio
import json
import sys
import threading
import webbrowser
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from intent_agent.config import PayPalSettings
from intent_agent.paypal import PayPalClient, PayPalError

PORT = 8765
STATE_FILE = Path("data/vault_check.json")


def wait_for_redirect(timeout: float) -> str | None:
    """Serve localhost until PayPal sends the browser back; return 'return' or 'cancel'."""
    result: dict[str, str] = {}
    done = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            kind = "return" if self.path.startswith("/vault/return") else "cancel" if self.path.startswith("/vault/cancel") else None
            self.send_response(200 if kind else 404)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(f"<h1>{kind or 'not found'}</h1><p>You can close this tab.</p>".encode())
            if kind:
                result["kind"] = kind
                result["path"] = self.path
                done.set()

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", PORT), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        done.wait(timeout)
    finally:
        server.shutdown()
    if "path" in result:
        print(f"  redirect: {result['path']}")
    return result.get("kind")


def ok(msg: str) -> None:
    print(f"PASS  {msg}")


def fail(msg: str, e: PayPalError | None = None) -> None:
    print(f"FAIL  {msg}")
    if e is not None:
        print(f"      HTTP {e.status_code}: {json.dumps(e.body, indent=2)}")
    sys.exit(1)


async def save_paypal(paypal: PayPalClient) -> str:
    base = f"http://localhost:{PORT}"
    try:
        setup = await paypal.create_setup_token(
            return_url=f"{base}/vault/return",
            cancel_url=f"{base}/vault/cancel",
            description="Standing Order: let the agent buy within your limits",
        )
    except PayPalError as e:
        fail("create setup token (is Vault enabled for this sandbox app?)", e)
    ok(f"create setup token {setup.setup_token_id}")

    print("\nApprove in the browser with your sandbox *personal* (buyer) account:")
    print(f"  {setup.approve_url}\n")
    webbrowser.open(setup.approve_url)
    kind = await asyncio.to_thread(wait_for_redirect, 600)
    if kind != "return":
        fail("buyer approval (cancelled or timed out)")
    ok("buyer approved")

    status = (await paypal.get_setup_token(setup.setup_token_id)).get("status")
    print(f"  setup token status: {status}")

    try:
        saved = await paypal.create_payment_token(setup.setup_token_id)
    except PayPalError as e:
        fail("create payment token", e)
    ok(f"saved PayPal: payment token {saved.payment_token_id}, customer {saved.customer_id}, {saved.email}")

    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(saved.__dict__, indent=2))
    return saved.payment_token_id


async def pay(paypal: PayPalClient, payment_token_id: str, amount: Decimal, currency: str) -> None:
    try:
        order = await paypal.charge_saved(
            payment_token_id=payment_token_id,
            amount=amount,
            currency=currency,
            description="Standing Order vault check",
        )
    except PayPalError as e:
        fail("pay with saved PayPal", e)
    captures = [c for pu in order.get("purchase_units", []) for c in (pu.get("payments") or {}).get("captures", [])]
    if order.get("status") != "COMPLETED" or not captures:
        print(json.dumps(order, indent=2))
        fail(f"pay with saved PayPal: order status {order.get('status')}")
    c = captures[0]
    ok(f"paid {c['amount']['value']} {c['amount']['currency_code']} with no buyer present: "
       f"order {order['id']}, capture {c['id']} ({c['status']})")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pay-only", action="store_true", help="pay with the PayPal saved by a previous run")
    parser.add_argument("--delete", action="store_true", help="remove the PayPal saved by a previous run")
    parser.add_argument("--amount", default="1.00")
    args = parser.parse_args()

    settings = PayPalSettings()
    if "sandbox" not in settings.paypal_base_url:
        fail(f"refusing to run against {settings.paypal_base_url}; this check is for the sandbox only")
    paypal = PayPalClient(settings.paypal_client_id, settings.paypal_client_secret, settings.paypal_base_url)
    try:
        if args.pay_only or args.delete:
            if not STATE_FILE.exists():
                fail(f"no saved PayPal in {STATE_FILE}; run without flags first")
            token_id = json.loads(STATE_FILE.read_text())["payment_token_id"]
            if args.delete:
                await paypal.delete_payment_token(token_id)
                STATE_FILE.unlink()
                ok(f"removed saved PayPal {token_id}")
                return
        else:
            token_id = await save_paypal(paypal)
        await pay(paypal, token_id, Decimal(args.amount), settings.currency)
    finally:
        await paypal.aclose()


if __name__ == "__main__":
    asyncio.run(main())
