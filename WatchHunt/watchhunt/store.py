import json
import sqlite3
import threading
from dataclasses import asdict
from datetime import datetime, timezone

from models import Listing

_lock = threading.Lock()


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path="watchhunt.db"):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS seen (
            key TEXT PRIMARY KEY, source TEXT, title TEXT, url TEXT, price REAL,
            score INTEGER, ai_json TEXT, first_seen TEXT);
        CREATE TABLE IF NOT EXISTS tracked (
            key TEXT PRIMARY KEY, data TEXT, status TEXT, updated_at TEXT);
        CREATE TABLE IF NOT EXISTS prices (
            key TEXT, ts TEXT, price REAL, bids INTEGER);
        CREATE INDEX IF NOT EXISTS prices_key ON prices(key);
        CREATE TABLE IF NOT EXISTS leads (
            key TEXT PRIMARY KEY, data TEXT, stage TEXT, lane TEXT, fit INTEGER, reasons TEXT, updated TEXT);
        CREATE TABLE IF NOT EXISTS closes (
            key TEXT PRIMARY KEY, source TEXT, title TEXT, price REAL, bids INTEGER, ended TEXT, seen_at TEXT);
        """)
        self.db.commit()

    # --- seen (dedupe between runs) ---
    def is_seen(self, key):
        return self.db.execute("SELECT 1 FROM seen WHERE key=?", (key,)).fetchone() is not None

    def save(self, lst: Listing):
        with _lock:
            self.db.execute(
                "INSERT OR REPLACE INTO seen VALUES (?,?,?,?,?,?,?,?)",
                (lst.key, lst.source, lst.title, lst.url, lst.price, lst.score,
                 json.dumps(lst.ai) if lst.ai else None, _now()))

    # --- tracked (the live board) ---
    def track(self, lst: Listing):
        with _lock:
            self.db.execute("INSERT OR REPLACE INTO tracked VALUES (?,?,?,?)",
                            (lst.key, json.dumps(asdict(lst)), "active", _now()))
            self._add_price(lst)

    def update_tracked(self, lst: Listing, status="active"):
        with _lock:
            self.db.execute("UPDATE tracked SET data=?, status=?, updated_at=? WHERE key=?",
                            (json.dumps(asdict(lst)), status, _now(), lst.key))
            self._add_price(lst)
            self.db.commit()

    def set_status(self, key, status):
        with _lock:
            self.db.execute("UPDATE tracked SET status=?, updated_at=? WHERE key=?", (status, _now(), key))
            self.db.commit()

    def tracked(self, statuses=("active", "ended")):
        q = f"SELECT data, status, updated_at FROM tracked WHERE status IN ({','.join('?' * len(statuses))})"
        rows = self.db.execute(q, statuses).fetchall()
        out = []
        for data, status, updated in rows:
            d = json.loads(data)
            out.append((Listing(**d), status, updated))
        return out

    def history(self, key, limit=60):
        rows = self.db.execute(
            "SELECT ts, price, bids FROM prices WHERE key=? ORDER BY ts DESC, rowid DESC LIMIT ?", (key, limit)).fetchall()
        return [{"ts": t, "price": p, "bids": b} for t, p, b in reversed(rows)]

    def _add_price(self, lst):
        last = self.db.execute("SELECT price, bids FROM prices WHERE key=? ORDER BY ts DESC, rowid DESC LIMIT 1",
                               (lst.key,)).fetchone()
        if last is None or last[0] != lst.price or last[1] != lst.bids:
            self.db.execute("INSERT INTO prices VALUES (?,?,?,?)", (lst.key, _now(), lst.price, lst.bids))

    # --- closes (finished auctions harvested from the sites' closed-search, used as real price comps) ---
    def save_close(self, key, source, title, price, bids, ended):
        with _lock:
            self.db.execute("INSERT OR REPLACE INTO closes VALUES (?,?,?,?,?,?,?)",
                            (key, source, title, price, bids, ended, _now()))

    def all_closes(self):
        return self.db.execute("SELECT key, source, title, price, bids, ended FROM closes").fetchall()

    # --- leads (every listing a scan returns, with its funnel stage) ---
    def save_lead(self, lst: Listing, verdict):
        with _lock:
            self.db.execute("INSERT OR REPLACE INTO leads VALUES (?,?,?,?,?,?,?)",
                            (lst.key, json.dumps(asdict(lst)), verdict["stage"], verdict.get("lane"),
                             verdict.get("fit", 0), json.dumps(verdict.get("reasons", [])), _now()))

    def leads(self, stages=None):
        q = "SELECT data, stage, lane, fit, reasons, updated FROM leads"
        args = ()
        if stages:
            q += f" WHERE stage IN ({','.join('?' * len(stages))})"
            args = tuple(stages)
        out = []
        for data, stage, lane, fit, reasons, updated in self.db.execute(q, args).fetchall():
            out.append((Listing(**json.loads(data)), {"stage": stage, "lane": lane, "fit": fit,
                                                      "reasons": json.loads(reasons or "[]"), "updated": updated}))
        return out

    def prune_leads(self, days=10):
        with _lock:
            self.db.execute("DELETE FROM leads WHERE updated < datetime('now', ?)", (f"-{days} days",))

    def commit(self):
        with _lock:
            self.db.commit()
