from __future__ import annotations
import sqlite3, threading, json
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs(id INTEGER PRIMARY KEY, slot TEXT UNIQUE NOT NULL, started_at TEXT,
  finished_at TEXT, png_path TEXT, simulation INTEGER DEFAULT 0, caption TEXT);
CREATE TABLE IF NOT EXISTS measurements(run_id INTEGER, bbu_id INTEGER, site TEXT, users_4g INTEGER,
  users_5g INTEGER, traffic_mbps REAL, traffic_metric TEXT, state TEXT, errors TEXT, collected_at TEXT);
CREATE TABLE IF NOT EXISTS deliveries(id INTEGER PRIMARY KEY, run_id INTEGER, destination TEXT,
  attempt INTEGER, ok INTEGER, detail TEXT, ts TEXT);
CREATE TABLE IF NOT EXISTS events(ts TEXT, bbu_id INTEGER, kind TEXT, detail TEXT);
"""

def _now(): return datetime.now(timezone.utc).isoformat(timespec="seconds")

class Database:
    def __init__(self, path: Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.c = sqlite3.connect(path, check_same_thread=False)
        self.c.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.lock: self.c.executescript(SCHEMA); self.c.commit()

    def _x(self, sql, args=()):
        with self.lock:
            cur = self.c.execute(sql, args); self.c.commit(); return cur

    def claim_slot(self, slot: str, simulation=False, stale_min=15):
        """Reserva o slot horário. Retorna run_id, ou None se já feito/em andamento (anti-duplicidade)."""
        cur = self._x("INSERT OR IGNORE INTO runs(slot,started_at,simulation) VALUES(?,?,?)", (slot, _now(), int(simulation)))
        if cur.rowcount:
            return cur.lastrowid
        r = self._x("SELECT id, finished_at, started_at FROM runs WHERE slot=?", (slot,)).fetchone()
        if r["finished_at"] is None:  # execução interrompida (reinício do serviço) => retoma se velha
            age = datetime.now(timezone.utc) - datetime.fromisoformat(r["started_at"])
            if age > timedelta(minutes=stale_min):
                self._x("UPDATE runs SET started_at=? WHERE id=?", (_now(), r["id"]))
                self._x("DELETE FROM measurements WHERE run_id=?", (r["id"],))
                return r["id"]
        return None

    def save_rows(self, run_id, rows, collected_at):
        for r in rows:
            u5 = r["users5g"] if isinstance(r["users5g"], int) else None
            self._x("INSERT INTO measurements VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (run_id, r["bbu_id"], r["site"], r["users4g"], u5, r["traffic"], r.get("traffic_metric"),
                     r["state"], json.dumps(r["errors"], ensure_ascii=False), collected_at))

    def finish_run(self, run_id, png_path, caption):
        self._x("UPDATE runs SET finished_at=?, png_path=?, caption=? WHERE id=?", (_now(), str(png_path), caption, run_id))

    def delivered(self, run_id, dest) -> bool:
        return self._x("SELECT 1 FROM deliveries WHERE run_id=? AND destination=? AND ok=1", (run_id, dest)).fetchone() is not None

    def add_delivery(self, run_id, dest, attempt, ok, detail=""):
        self._x("INSERT INTO deliveries(run_id,destination,attempt,ok,detail,ts) VALUES(?,?,?,?,?,?)",
                (run_id, dest, attempt, int(ok), detail[:300], _now()))

    def pending_runs(self, dest, hours=3):
        since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        return self._x("""SELECT id, png_path, caption FROM runs WHERE finished_at IS NOT NULL AND simulation=0
            AND png_path IS NOT NULL AND finished_at>=? AND id NOT IN
            (SELECT run_id FROM deliveries WHERE destination=? AND ok=1)""", (since, dest)).fetchall()

    def event(self, bbu_id, kind, detail=""):
        self._x("INSERT INTO events VALUES(?,?,?,?)", (_now(), bbu_id, kind, detail[:300]))
