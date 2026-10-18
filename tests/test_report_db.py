from velosonet.config import load_settings
from velosonet.simulate import run
from velosonet.db import Database

def test_simulation_png(tmp_path):
    png, cap = run(load_settings(), tmp_path / "s.png")
    assert png.exists() and png.stat().st_size > 10_000 and "SIMULAÇÃO" in cap

def test_slot_dedup(tmp_path):
    db = Database(tmp_path / "t.sqlite")
    rid = db.claim_slot("2026-01-01T08"); assert rid
    assert db.claim_slot("2026-01-01T08") is None
    db.add_delivery(rid, "telegram", 1, True); assert db.delivered(rid, "telegram")
