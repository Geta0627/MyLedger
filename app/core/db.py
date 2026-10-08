"""SQLite 数据持久化层。

直接用标准库 sqlite3 —— 在安卓上零额外依赖，体积小，最稳。
所有写操作走同一个连接，Kivy 主线程调用，个人记账量级完全够用。
"""
from __future__ import annotations

import os
import sqlite3
import threading
from typing import List, Optional, Tuple

from .models import (
    Transaction,
    TYPE_EXPENSE,
    TYPE_INCOME,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS transactions (
    id              TEXT PRIMARY KEY,
    amount          INTEGER NOT NULL,
    type            TEXT    NOT NULL,
    category        TEXT    NOT NULL DEFAULT '其他',
    merchant        TEXT    NOT NULL DEFAULT '',
    note            TEXT    NOT NULL DEFAULT '',
    occurred_at     TEXT    NOT NULL,
    source          TEXT    NOT NULL DEFAULT 'manual',
    image_path      TEXT    NOT NULL DEFAULT '',
    ocr_confidence  REAL    NOT NULL DEFAULT 1.0,
    created_at      TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tx_occurred ON transactions(occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_tx_category ON transactions(category);
CREATE INDEX IF NOT EXISTS idx_tx_type     ON transactions(type);
"""


class Database:
    """账目数据库。线程安全（加锁）。"""

    def __init__(self, db_path: str):
        self.db_path = db_path
        parent = os.path.dirname(os.path.abspath(db_path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._init_schema()

    def _init_schema(self):
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    # ---------- 写入 ----------

    def add(self, tx: Transaction) -> Transaction:
        """新增一条账目。"""
        with self._lock:
            self._conn.execute(
                """INSERT INTO transactions
                   (id, amount, type, category, merchant, note,
                    occurred_at, source, image_path, ocr_confidence, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    tx.id, tx.amount, tx.type, tx.category, tx.merchant,
                    tx.note, tx.occurred_at, tx.source, tx.image_path,
                    tx.ocr_confidence, tx.created_at,
                ),
            )
            self._conn.commit()
        return tx

    def update(self, tx: Transaction) -> bool:
        with self._lock:
            cur = self._conn.execute(
                """UPDATE transactions SET
                     amount=?, type=?, category=?, merchant=?, note=?,
                     occurred_at=?, source=?, image_path=?, ocr_confidence=?
                   WHERE id=?""",
                (
                    tx.amount, tx.type, tx.category, tx.merchant, tx.note,
                    tx.occurred_at, tx.source, tx.image_path,
                    tx.ocr_confidence, tx.id,
                ),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def delete(self, tx_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM transactions WHERE id=?", (tx_id,))
            self._conn.commit()
            return cur.rowcount > 0

    def get(self, tx_id: str) -> Optional[Transaction]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM transactions WHERE id=?", (tx_id,)
            ).fetchone()
        return Transaction.from_dict(dict(row)) if row else None

    # ---------- 查询 ----------

    def list(
        self,
        start: Optional[str] = None,
        end: Optional[str] = None,
        tx_type: Optional[str] = None,
        category: Optional[str] = None,
        keyword: Optional[str] = None,
        limit: int = 500,
        offset: int = 0,
    ) -> List[Transaction]:
        """条件查询。start/end 形如 '2026-10-01' 或 '2026-10-01 08:00:00'。"""
        sql = "SELECT * FROM transactions WHERE 1=1"
        args: list = []
        if start:
            sql += " AND occurred_at >= ?"
            args.append(start if len(start) > 10 else start + " 00:00:00")
        if end:
            sql += " AND occurred_at <= ?"
            args.append(end if len(end) > 10 else end + " 23:59:59")
        if tx_type:
            sql += " AND type = ?"
            args.append(tx_type)
        if category:
            sql += " AND category = ?"
            args.append(category)
        if keyword:
            sql += " AND (merchant LIKE ? OR note LIKE ? OR category LIKE ?)"
            kw = f"%{keyword}%"
            args += [kw, kw, kw]
        sql += " ORDER BY occurred_at DESC, created_at DESC LIMIT ? OFFSET ?"
        args += [limit, offset]
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        return [Transaction.from_dict(dict(r)) for r in rows]

    def summary(self, start: str, end: str) -> Tuple[int, int]:
        """返回 (总收入分, 总支出分)。"""
        with self._lock:
            row = self._conn.execute(
                """SELECT
                     COALESCE(SUM(CASE WHEN type=? THEN amount ELSE 0 END),0) AS inc,
                     COALESCE(SUM(CASE WHEN type=? THEN amount ELSE 0 END),0) AS exp
                   FROM transactions
                   WHERE occurred_at >= ? AND occurred_at <= ?""",
                (
                    TYPE_INCOME, TYPE_EXPENSE,
                    start if len(start) > 10 else start + " 00:00:00",
                    end if len(end) > 10 else end + " 23:59:59",
                ),
            ).fetchone()
        return int(row["inc"]), int(row["exp"])

    def summary_by_category(self, start: str, end: str, tx_type: str = TYPE_EXPENSE):
        """按分类汇总，返回 [(category, total_cents, count), ...]，按金额降序。"""
        with self._lock:
            rows = self._conn.execute(
                """SELECT category, SUM(amount) AS total, COUNT(*) AS cnt
                   FROM transactions
                   WHERE type=? AND occurred_at >= ? AND occurred_at <= ?
                   GROUP BY category
                   ORDER BY total DESC""",
                (
                    tx_type,
                    start if len(start) > 10 else start + " 00:00:00",
                    end if len(end) > 10 else end + " 23:59:59",
                ),
            ).fetchall()
        return [(r["category"], int(r["total"]), int(r["cnt"])) for r in rows]

    def distinct_merchants(self, limit: int = 20) -> List[str]:
        """用于商户名自动补全。"""
        with self._lock:
            rows = self._conn.execute(
                """SELECT merchant, COUNT(*) c FROM transactions
                   WHERE merchant != '' GROUP BY merchant
                   ORDER BY c DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        return [r["merchant"] for r in rows]

    def count(self) -> int:
        with self._lock:
            return int(self._conn.execute("SELECT COUNT(*) c FROM transactions").fetchone()["c"])

    def close(self):
        with self._lock:
            self._conn.close()
