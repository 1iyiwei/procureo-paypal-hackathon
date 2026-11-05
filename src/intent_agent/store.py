"""SQLite persistence for chats, checkout orders, standing orders and their timelines."""

import json
import sqlite3
from dataclasses import dataclass, fields
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from .catalog import SIM_START
from .watcher import StandingOrder


@dataclass(frozen=True)
class Order:
    order_id: str
    chat_id: int
    description: str
    amount: Decimal
    currency: str
    status: str  # CREATED -> CAPTURING -> COMPLETED | FAILED, or CREATED -> CANCELLED
    standing_order_id: str | None = None


class Store:
    def __init__(self, path: str):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS orders (
                order_id    TEXT PRIMARY KEY,
                chat_id     INTEGER NOT NULL,
                description TEXT NOT NULL,
                amount      TEXT NOT NULL,
                currency    TEXT NOT NULL,
                status      TEXT NOT NULL,
                created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        if "standing_order_id" not in {r["name"] for r in self._db.execute("PRAGMA table_info(orders)")}:
            self._db.execute("ALTER TABLE orders ADD COLUMN standing_order_id TEXT")
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS chats (
                chat_id          INTEGER PRIMARY KEY,
                sim_now          TEXT NOT NULL,
                payment_token_id TEXT,
                paypal_email     TEXT
            );
            CREATE TABLE IF NOT EXISTS allowed_chats (
                chat_id INTEGER PRIMARY KEY
            );
            CREATE TABLE IF NOT EXISTS standing_orders (
                id      TEXT PRIMARY KEY,
                chat_id INTEGER NOT NULL,
                seq     INTEGER NOT NULL,
                data    TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS events (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id TEXT NOT NULL,
                at       TEXT NOT NULL,
                text     TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS setup_tokens (
                setup_token_id TEXT PRIMARY KEY,
                chat_id        INTEGER NOT NULL,
                order_id       TEXT NOT NULL,
                auto_limit     TEXT NOT NULL
            );
            """
        )
        if "live" not in {r["name"] for r in self._db.execute("PRAGMA table_info(chats)")}:
            self._db.execute("ALTER TABLE chats ADD COLUMN live INTEGER NOT NULL DEFAULT 0")
        self._db.commit()

    def add_order(self, order: Order) -> None:
        self._db.execute(
            "INSERT INTO orders (order_id, chat_id, description, amount, currency, status, standing_order_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                order.order_id,
                order.chat_id,
                order.description,
                str(order.amount),
                order.currency,
                order.status,
                order.standing_order_id,
            ),
        )
        self._db.commit()

    def get_order(self, order_id: str) -> Order | None:
        row = self._db.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,)).fetchone()
        if row is None:
            return None
        return Order(
            order_id=row["order_id"],
            chat_id=row["chat_id"],
            description=row["description"],
            amount=Decimal(row["amount"]),
            currency=row["currency"],
            status=row["status"],
            standing_order_id=row["standing_order_id"],
        )

    def transition(self, order_id: str, from_status: str, to_status: str) -> bool:
        """Atomically move an order between states; False if it was not in `from_status`."""
        cur = self._db.execute(
            "UPDATE orders SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE order_id = ? AND status = ?",
            (to_status, order_id, from_status),
        )
        self._db.commit()
        return cur.rowcount == 1

    # --- Chats: each has its own simulated clock and, optionally, a saved PayPal ---

    def chat_allowed(self, chat_id: int) -> bool:
        return self._db.execute("SELECT 1 FROM allowed_chats WHERE chat_id = ?", (chat_id,)).fetchone() is not None

    def allow_chat(self, chat_id: int) -> None:
        self._db.execute("INSERT OR IGNORE INTO allowed_chats (chat_id) VALUES (?)", (chat_id,))
        self._db.commit()

    def chat_live(self, chat_id: int) -> bool:
        row = self._db.execute("SELECT live FROM chats WHERE chat_id = ?", (chat_id,)).fetchone()
        return bool(row and row["live"])

    def set_chat_live(self, chat_id: int, live: bool) -> None:
        self.sim_now(chat_id)  # makes sure the chat exists
        self._db.execute("UPDATE chats SET live = ? WHERE chat_id = ?", (int(live), chat_id))
        self._db.commit()

    def live_chats(self) -> list[int]:
        return [r["chat_id"] for r in self._db.execute("SELECT chat_id FROM chats WHERE live = 1")]

    def sim_now(self, chat_id: int) -> datetime:
        self._db.execute(
            "INSERT OR IGNORE INTO chats (chat_id, sim_now) VALUES (?, ?)", (chat_id, SIM_START.isoformat())
        )
        self._db.commit()
        row = self._db.execute("SELECT sim_now FROM chats WHERE chat_id = ?", (chat_id,)).fetchone()
        return datetime.fromisoformat(row["sim_now"])

    def set_sim_now(self, chat_id: int, now: datetime) -> None:
        self._db.execute("UPDATE chats SET sim_now = ? WHERE chat_id = ?", (now.isoformat(), chat_id))
        self._db.commit()

    def saved_paypal(self, chat_id: int) -> str | None:
        self.sim_now(chat_id)
        row = self._db.execute("SELECT payment_token_id FROM chats WHERE chat_id = ?", (chat_id,)).fetchone()
        return row["payment_token_id"]

    def set_saved_paypal(self, chat_id: int, payment_token_id: str | None, email: str | None = None) -> None:
        self.sim_now(chat_id)
        self._db.execute(
            "UPDATE chats SET payment_token_id = ?, paypal_email = ? WHERE chat_id = ?",
            (payment_token_id, email, chat_id),
        )
        self._db.commit()

    def reset_chat(self, chat_id: int) -> None:
        """Forget a chat's standing orders, clock and saved PayPal (demo retakes)."""
        ids = [o.id for o in self.standing_orders(chat_id)]
        self._db.executemany("DELETE FROM events WHERE order_id = ?", [(i,) for i in ids])
        self._db.execute("DELETE FROM standing_orders WHERE chat_id = ?", (chat_id,))
        self._db.execute("DELETE FROM setup_tokens WHERE chat_id = ?", (chat_id,))
        self._db.execute("DELETE FROM chats WHERE chat_id = ?", (chat_id,))
        self._db.commit()

    # --- Standing orders ---

    def save_standing_order(self, order: StandingOrder) -> None:
        seq = self._db.execute(
            "SELECT COALESCE((SELECT seq FROM standing_orders WHERE id = ?),"
            " (SELECT COALESCE(MAX(seq), 0) + 1 FROM standing_orders))",
            (order.id,),
        ).fetchone()[0]
        self._db.execute(
            "INSERT OR REPLACE INTO standing_orders (id, chat_id, seq, data) VALUES (?, ?, ?, ?)",
            (order.id, order.chat_id, seq, _dump(order)),
        )
        self._db.commit()

    def standing_order(self, order_id: str) -> StandingOrder | None:
        row = self._db.execute("SELECT data FROM standing_orders WHERE id = ?", (order_id,)).fetchone()
        return _load(row["data"]) if row else None

    def standing_orders(self, chat_id: int) -> list[StandingOrder]:
        """A chat's standing orders, oldest first."""
        rows = self._db.execute("SELECT data FROM standing_orders WHERE chat_id = ? ORDER BY seq", (chat_id,))
        return [_load(r["data"]) for r in rows]

    def add_event(self, order_id: str, at: datetime, text: str) -> None:
        self._db.execute("INSERT INTO events (order_id, at, text) VALUES (?, ?, ?)", (order_id, at.isoformat(), text))
        self._db.commit()

    def events(self, order_id: str) -> list[tuple[datetime, str]]:
        rows = self._db.execute("SELECT at, text FROM events WHERE order_id = ? ORDER BY id", (order_id,))
        return [(datetime.fromisoformat(r["at"]), r["text"]) for r in rows]

    def add_setup_token(self, setup_token_id: str, chat_id: int, order_id: str, auto_limit: Decimal) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO setup_tokens VALUES (?, ?, ?, ?)", (setup_token_id, chat_id, order_id, str(auto_limit))
        )
        self._db.commit()

    def setup_token(self, setup_token_id: str) -> tuple[int, str, Decimal] | None:
        row = self._db.execute("SELECT * FROM setup_tokens WHERE setup_token_id = ?", (setup_token_id,)).fetchone()
        return (row["chat_id"], row["order_id"], Decimal(row["auto_limit"])) if row else None

    def delete_setup_token(self, setup_token_id: str) -> None:
        self._db.execute("DELETE FROM setup_tokens WHERE setup_token_id = ?", (setup_token_id,))
        self._db.commit()

    def close(self) -> None:
        self._db.close()


_DECIMALS = {"max_price", "auto_limit", "pending_auto_limit", "last_low", "last_offer", "bought_price", "reference_price"}
_DATES = {"arrive_by", "arrives_on"}


def _dump(order: StandingOrder) -> str:
    def encode(value):
        return value.isoformat() if isinstance(value, (date, datetime)) else str(value) if isinstance(value, Decimal) else value

    return json.dumps({f.name: encode(getattr(order, f.name)) for f in fields(order)})


def _load(data: str) -> StandingOrder:
    raw = json.loads(data)
    for name, value in raw.items():
        if value is None:
            continue
        if name in _DECIMALS:
            raw[name] = Decimal(value)
        elif name in _DATES:
            raw[name] = date.fromisoformat(value)
        elif name == "bought_at":
            raw[name] = datetime.fromisoformat(value)
    return StandingOrder(**raw)
