"""
database.py — WiFi3D Mapper
SQLite persistence layer for storing and querying WiFi scan data.
"""

import sqlite3
import csv
import os
from datetime import datetime
from typing import List, Dict, Optional, Tuple


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_DDL = """
CREATE TABLE IF NOT EXISTS scans (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp        TEXT    NOT NULL,
    ssid             TEXT    NOT NULL DEFAULT '',
    bssid            TEXT    NOT NULL DEFAULT '',
    signal_strength  INTEGER NOT NULL DEFAULT -100,
    channel          INTEGER NOT NULL DEFAULT 0,
    security         TEXT    NOT NULL DEFAULT '',
    x_pos            REAL    NOT NULL DEFAULT 0.0,
    y_pos            REAL    NOT NULL DEFAULT 0.0
);

CREATE INDEX IF NOT EXISTS idx_scans_timestamp ON scans (timestamp);
CREATE INDEX IF NOT EXISTS idx_scans_bssid      ON scans (bssid);
"""


# ---------------------------------------------------------------------------
# Database manager
# ---------------------------------------------------------------------------

class DatabaseManager:
    """
    Thread-safe (via check_same_thread=False) SQLite manager.
    Each public method opens/closes a connection so callers on any thread
    are safe when combined with a mutex or when PyQt signals are used.
    """

    def __init__(self, db_path: str = "wifi_scans.db") -> None:
        self.db_path = db_path
        self._init_db()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        """Create tables and indexes if they don't exist yet."""
        with self._connect() as conn:
            conn.executescript(_DDL)

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def insert_scan(
        self,
        ssid: str,
        bssid: str,
        signal_strength: int,
        channel: int,
        security: str = "",
        x_pos: float = 0.0,
        y_pos: float = 0.0,
    ) -> None:
        """Persist a single WiFi measurement."""
        ts = datetime.utcnow().isoformat(sep=" ", timespec="milliseconds")
        sql = """
            INSERT INTO scans
                (timestamp, ssid, bssid, signal_strength, channel, security, x_pos, y_pos)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """
        with self._connect() as conn:
            conn.execute(sql, (ts, ssid, bssid, signal_strength, channel, security, x_pos, y_pos))

    def insert_batch(self, rows: List[Dict]) -> None:
        """
        Insert multiple scan records in a single transaction.

        Each dict should contain the keys:
            ssid, bssid, signal_strength, channel, security, x_pos, y_pos
        """
        if not rows:
            return
        ts = datetime.utcnow().isoformat(sep=" ", timespec="milliseconds")
        sql = """
            INSERT INTO scans
                (timestamp, ssid, bssid, signal_strength, channel, security, x_pos, y_pos)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """
        data = [
            (
                ts,
                r.get("ssid", ""),
                r.get("bssid", ""),
                r.get("signal_strength", -100),
                r.get("channel", 0),
                r.get("security", ""),
                r.get("x_pos", 0.0),
                r.get("y_pos", 0.0),
            )
            for r in rows
        ]
        with self._connect() as conn:
            conn.executemany(sql, data)

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get_recent_scans(self, limit: int = 500) -> List[Dict]:
        """Return the *limit* most-recent scan rows as plain dicts."""
        sql = "SELECT * FROM scans ORDER BY id DESC LIMIT ?"
        with self._connect() as conn:
            rows = conn.execute(sql, (limit,)).fetchall()
        return [dict(r) for r in reversed(rows)]

    def get_all_scans(self) -> List[Dict]:
        """Return every row ordered by timestamp ascending."""
        sql = "SELECT * FROM scans ORDER BY id ASC"
        with self._connect() as conn:
            rows = conn.execute(sql).fetchall()
        return [dict(r) for r in rows]

    def get_rssi_history(self, bssid: str, limit: int = 200) -> List[Tuple[str, int]]:
        """
        Return (timestamp, signal_strength) pairs for a specific BSSID,
        ordered oldest-first.  Used by the live RSSI graph.
        """
        sql = """
            SELECT timestamp, signal_strength
            FROM   scans
            WHERE  bssid = ?
            ORDER  BY id DESC
            LIMIT  ?
        """
        with self._connect() as conn:
            rows = conn.execute(sql, (bssid, limit)).fetchall()
        return [(r["timestamp"], r["signal_strength"]) for r in reversed(rows)]

    def get_latest_per_bssid(self) -> List[Dict]:
        """
        Return the most-recent measurement per unique BSSID.
        Used by the 3-D heatmap to avoid plotting stale duplicates.
        """
        sql = """
            SELECT s.*
            FROM   scans s
            INNER JOIN (
                SELECT bssid, MAX(id) AS max_id
                FROM   scans
                GROUP  BY bssid
            ) latest ON s.id = latest.max_id
        """
        with self._connect() as conn:
            rows = conn.execute(sql).fetchall()
        return [dict(r) for r in rows]

    def count_scans(self) -> int:
        """Total number of scan records."""
        with self._connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM scans").fetchone()[0]

    # ------------------------------------------------------------------
    # Export / maintenance
    # ------------------------------------------------------------------

    def export_csv(self, filepath: str) -> int:
        """
        Write all scan data to *filepath* in CSV format.
        Returns the number of rows written.
        """
        rows = self.get_all_scans()
        if not rows:
            return 0

        fieldnames = ["id", "timestamp", "ssid", "bssid",
                      "signal_strength", "channel", "security", "x_pos", "y_pos"]
        with open(filepath, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

        return len(rows)

    def clear_all(self) -> None:
        """Delete every row from the scans table."""
        with self._connect() as conn:
            conn.execute("DELETE FROM scans")

    def vacuum(self) -> None:
        """Reclaim disk space after a bulk delete."""
        with self._connect() as conn:
            conn.execute("VACUUM")