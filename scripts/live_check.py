"""Check Channel3 and the real model together: search, match, and read current prices. Nothing is bought.

    uv run python scripts/live_check.py "Noise-cancelling over-ear headphones" 200

The last argument is an optional spending limit. Uses one or two Channel3 credits and a small amount of model credit.
"""

import asyncio
import sys
from datetime import datetime
from decimal import Decimal

from intent_agent.brain import OpenAIBrain
from intent_agent.config import Settings
from intent_agent.live import LiveCatalog, describe


async def main() -> None:
    settings = Settings(telegram_bot_token="not-used-here")
    if not settings.channel3_api_key:
        sys.exit("Set CHANNEL3_API_KEY in .env first.")
    args = sys.argv[1:]
    limit = Decimal(args.pop()) if args and args[-1].replace(".", "").isdigit() else None
    query = " ".join(args) or "Noise-cancelling over-ear headphones"
    catalog = LiveCatalog(settings.channel3_api_key, settings.currency)
    brain = OpenAIBrain(settings.openai_api_key, settings.openai_model)
    try:
        found = await catalog.search(query, limit)
        print(f"Search found {len(found)} products with usable offers:")
        for p in found:
            print(f"  {p.id}: {p.name[:90]}")
        ids = await brain.source(query, describe(found))
        print(f"\nThe model kept: {ids}\n")
        for q in await catalog.quotes(ids[:3], datetime.now()):
            print(f"  ${q.price} at {q.store}: {q.product.name[:70]}")
    finally:
        await catalog.aclose()


if __name__ == "__main__":
    asyncio.run(main())
