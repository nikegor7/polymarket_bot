"""Тесты для SQLite хранилища."""
from __future__ import annotations

import sys
import os
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ.setdefault("GNEWS_API_KEY", "test-key")

import core.database as db


def _setup_temp_db():
    """Переключает БД на временный файл, отключает миграцию из JSON."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    from pathlib import Path
    db.DB_PATH = Path(tmp.name)
    # Отключаем legacy JSON пути чтобы миграция не подхватила реальные данные
    db._LEGACY_BETS = Path("/nonexistent/bets.json")
    db._LEGACY_OUTCOMES = Path("/nonexistent/outcomes.json")
    db.init_db()
    return tmp.name


def _sample_bet():
    return {
        "timestamp": "2026-03-15 10:00:00",
        "question": "Will Bitcoin exceed $100k?",
        "condition_id": "0xabc123",
        "end_date": "2026-03-16T00:00:00Z",
        "our_prob": 0.72,
        "market_prob": 0.61,
        "edge": 0.11,
        "confidence": "medium",
        "side": "YES",
        "bet_amount": 3.20,
        "dry_run": True,
        "reasoning": "Strong bullish signal",
    }


def _sample_outcome():
    return {
        "condition_id": "0xabc123",
        "question": "Will Bitcoin exceed $100k?",
        "our_side": "YES",
        "our_prob": 0.72,
        "market_prob": 0.61,
        "bet_amount": 3.20,
        "resolved_yes": True,
        "won": True,
        "hypothetical_pnl": 2.05,
        "resolved_at": "2026-03-16T00:00:00Z",
    }


# ─── Tests ─────────────────────────────────────────────────

def test_init_creates_tables():
    _setup_temp_db()
    conn = db.get_connection()
    tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    table_names = {t["name"] for t in tables}
    conn.close()
    assert "bets" in table_names
    assert "outcomes" in table_names


def test_insert_and_load_bet():
    _setup_temp_db()
    db.insert_bet(_sample_bet())
    bets = db.load_bets()
    assert len(bets) == 1
    assert bets[0]["question"] == "Will Bitcoin exceed $100k?"
    assert bets[0]["our_prob"] == 0.72
    assert bets[0]["side"] == "YES"


def test_insert_and_load_outcome():
    _setup_temp_db()
    db.insert_outcome(_sample_outcome())
    outcomes = db.load_outcomes()
    assert len(outcomes) == 1
    assert outcomes[0]["won"] is True
    assert outcomes[0]["resolved_yes"] is True
    assert outcomes[0]["hypothetical_pnl"] == 2.05


def test_duplicate_outcome_ignored():
    _setup_temp_db()
    db.insert_outcome(_sample_outcome())
    db.insert_outcome(_sample_outcome())  # same condition_id
    outcomes = db.load_outcomes()
    assert len(outcomes) == 1


def test_tracked_condition_ids():
    _setup_temp_db()
    db.insert_outcome(_sample_outcome())
    ids = db.get_tracked_condition_ids()
    assert "0xabc123" in ids


def test_empty_db():
    _setup_temp_db()
    assert db.load_bets() == []
    assert db.load_outcomes() == []
    assert db.get_tracked_condition_ids() == set()


def test_multiple_bets():
    _setup_temp_db()
    for i in range(5):
        bet = _sample_bet()
        bet["condition_id"] = f"0x{i}"
        bet["bet_amount"] = float(i + 1)
        db.insert_bet(bet)
    bets = db.load_bets()
    assert len(bets) == 5
    assert bets[0]["bet_amount"] == 1.0
    assert bets[4]["bet_amount"] == 5.0
