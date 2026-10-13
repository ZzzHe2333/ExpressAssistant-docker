"""SQLite package history and owner-provided credentials."""
import json
import os
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

class Store:
    def __init__(self, db_path, cookie_file):
        self.db_path = str(db_path)
        self.cookie_file = Path(cookie_file)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS packages (
                mail_no TEXT PRIMARY KEY, company_name TEXT NOT NULL,
                state_name TEXT NOT NULL, latest_text TEXT NOT NULL,
                latest_time TEXT NOT NULL, source TEXT NOT NULL,
                order_id TEXT NOT NULL, traces TEXT NOT NULL,
                goods TEXT NOT NULL, updated_at TEXT NOT NULL)""")

    def _conn(self):
        conn = sqlite3.connect(self.db_path, timeout=15)
        conn.row_factory = sqlite3.Row
        return conn

    def get_cookie(self):
        try:
            return self.cookie_file.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return ""

    def set_cookie(self, value):
        if not isinstance(value, str) or not value.strip() or len(value) > 32768:
            raise ValueError("Cookie must be 1–32768 characters")
        self.cookie_file.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=".taobao-", dir=self.cookie_file.parent)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(value.strip() + "\n")
            os.replace(name, self.cookie_file)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def upsert(self, items):
        stamp = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            for item in items:
                conn.execute("""INSERT INTO packages(
                  mail_no, company_name, state_name, latest_text, latest_time,
                  source, order_id, traces, goods, updated_at)
                  VALUES(?,?,?,?,?,?,?,?,?,?)
                  ON CONFLICT(mail_no) DO UPDATE SET
                  company_name=CASE WHEN excluded.company_name != '' THEN excluded.company_name ELSE packages.company_name END,
                  state_name=CASE WHEN excluded.state_name != '' THEN excluded.state_name ELSE packages.state_name END,
                  latest_text=excluded.latest_text, latest_time=excluded.latest_time,
                  source=excluded.source,
                  order_id=CASE WHEN excluded.order_id != '' THEN excluded.order_id ELSE packages.order_id END,
                  traces=excluded.traces, goods=excluded.goods, updated_at=excluded.updated_at""",
                  (item["mailNo"], item.get("companyName", ""), item.get("stateName", ""),
                   item.get("latestText", ""), item.get("latestTime", ""),
                   item.get("source", ""), item.get("orderId", ""),
                   json.dumps(item.get("traces", []), ensure_ascii=False),
                   json.dumps(item.get("goods", []), ensure_ascii=False), stamp))
        return len(items)

    @staticmethod
    def _row(row):
        if row is None:
            return None
        obj = dict(row)
        obj["mailNo"] = obj.pop("mail_no")
        for src, dst in (("company_name", "companyName"), ("state_name", "stateName"),
                         ("latest_text", "latestText"), ("latest_time", "latestTime"),
                         ("order_id", "orderId"), ("updated_at", "updatedAt")):
            obj[dst] = obj.pop(src)
        obj["traces"] = json.loads(obj["traces"])
        obj["goods"] = json.loads(obj["goods"])
        return obj

    def list_packages(self):
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM packages ORDER BY updated_at DESC, mail_no").fetchall()
        return [self._row(x) for x in rows]

    def get_package(self, mail_no):
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM packages WHERE mail_no = ?", (mail_no,)).fetchone()
        return self._row(row)
