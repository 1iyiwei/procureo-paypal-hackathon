"""SQLite persistence. Kept deliberately small; mandates and price history will be added here."""

import sqlite3
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path


@dataclass(frozen=True)
class Order:
    order_id: str
    chat_id: int
    description: str
    amount: Decimal
    currency: str
    status: str  # CREATED -> COMPLETED | CANCELLED | FAILED


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
        self._db.commit()

    def add_order(self, order: Order) -> None:
        self._db.execute(
            "INSERT INTO orders (order_id, chat_id, description, amount, currency, status)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (order.order_id, order.chat_id, order.description, str(order.amount), order.currency, order.status),
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
        )

    def transition(self, order_id: str, from_status: str, to_status: str) -> bool:
        """Atomically move an order between states; False if it was not in `from_status`."""
        cur = self._db.execute(
            "UPDATE orders SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE order_id = ? AND status = ?",
            (to_status, order_id, from_status),
        )
        self._db.commit()
        return cur.rowcount == 1

    def close(self) -> None:
        self._db.close()
