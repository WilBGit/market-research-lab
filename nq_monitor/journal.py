"""SQLite journal for immutable test signals and one-time outcomes."""

from pathlib import Path
import sqlite3
from typing import Mapping


class SignalJournal:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS signals (
                signal_id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                direction TEXT NOT NULL,
                signal_timestamp TEXT NOT NULL,
                sweep_timestamp TEXT NOT NULL,
                cisd_timestamp TEXT NOT NULL,
                entry_price REAL NOT NULL,
                stop_loss_price REAL NOT NULL,
                target_price REAL NOT NULL,
                risk_points REAL NOT NULL,
                liquidity_level REAL NOT NULL,
                pd_kind TEXT NOT NULL,
                pd_low REAL NOT NULL,
                pd_high REAL NOT NULL,
                requirements_json TEXT NOT NULL,
                evidence_json TEXT NOT NULL,
                order_mode TEXT NOT NULL CHECK(order_mode = 'paper_only'),
                inserted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS outcomes (
                signal_id TEXT PRIMARY KEY REFERENCES signals(signal_id),
                outcome TEXT NOT NULL CHECK(outcome IN ('WIN','LOSS','TIMEOUT')),
                exit_timestamp TEXT NOT NULL,
                exit_price REAL NOT NULL,
                r_multiple REAL NOT NULL,
                reason TEXT NOT NULL,
                inserted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS alerts (
                alert_id TEXT PRIMARY KEY,
                signal_id TEXT,
                message TEXT NOT NULL,
                inserted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS discord_alert_deliveries (
                alert_id TEXT PRIMARY KEY REFERENCES alerts(alert_id),
                delivered_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        self.conn.commit()

    def record_signal(self, row: Mapping[str, object]) -> bool:
        keys = [
            "signal_id", "symbol", "direction", "signal_timestamp",
            "sweep_timestamp", "cisd_timestamp", "entry_price",
            "stop_loss_price", "target_price", "risk_points",
            "liquidity_level", "pd_kind", "pd_low", "pd_high",
            "requirements_json", "evidence_json", "order_mode",
        ]
        values = [row[k] for k in keys]
        cur = self.conn.execute(
            f"INSERT OR IGNORE INTO signals ({','.join(keys)}) VALUES ({','.join('?' for _ in keys)})",
            values,
        )
        self.conn.commit()
        return cur.rowcount == 1

    def record_outcome(self, row: Mapping[str, object]) -> bool:
        keys = ["signal_id", "outcome", "exit_timestamp", "exit_price", "r_multiple", "reason"]
        cur = self.conn.execute(
            f"INSERT OR IGNORE INTO outcomes ({','.join(keys)}) VALUES ({','.join('?' for _ in keys)})",
            [row[k] for k in keys],
        )
        self.conn.commit()
        return cur.rowcount == 1

    def record_alert(self, alert_id: str, message: str, signal_id: str | None = None) -> bool:
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO alerts(alert_id, signal_id, message) VALUES(?,?,?)",
            (alert_id, signal_id, message),
        )
        self.conn.commit()
        return cur.rowcount == 1

    def pending_alerts(self):
        return self.conn.execute(
            """
            SELECT a.*
            FROM alerts a
            LEFT JOIN discord_alert_deliveries d ON d.alert_id = a.alert_id
            WHERE d.alert_id IS NULL
            ORDER BY a.inserted_at, a.alert_id
            """
        ).fetchall()

    def mark_alert_delivered(self, alert_id: str) -> bool:
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO discord_alert_deliveries(alert_id) VALUES(?)",
            (alert_id,),
        )
        self.conn.commit()
        return cur.rowcount == 1

    def count_signals(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0])

    def summary(self) -> dict[str, object]:
        row = self.conn.execute(
            """
            SELECT
                SUM(CASE WHEN outcome='WIN' THEN 1 ELSE 0 END) AS wins,
                SUM(CASE WHEN outcome='LOSS' THEN 1 ELSE 0 END) AS losses,
                SUM(CASE WHEN outcome='TIMEOUT' THEN 1 ELSE 0 END) AS timeouts
            FROM outcomes
            """
        ).fetchone()
        wins = int(row[0] or 0)
        losses = int(row[1] or 0)
        timeouts = int(row[2] or 0)
        pending = int(self.conn.execute(
            "SELECT COUNT(*) FROM signals s LEFT JOIN outcomes o ON o.signal_id=s.signal_id WHERE o.signal_id IS NULL"
        ).fetchone()[0])
        denominator = wins + losses
        return {
            "wins": wins,
            "losses": losses,
            "timeouts": timeouts,
            "pending": pending,
            "win_rate": wins / denominator if denominator else None,
        }

    def open_signals(self):
        return self.conn.execute(
            "SELECT s.* FROM signals s LEFT JOIN outcomes o ON o.signal_id=s.signal_id WHERE o.signal_id IS NULL ORDER BY s.signal_timestamp"
        ).fetchall()

    def close(self):
        self.conn.close()
