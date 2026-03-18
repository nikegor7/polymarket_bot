"""Тесты для outcome tracking: resolution, P&L, calibration, categories."""
from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ.setdefault("GNEWS_API_KEY", "test-key")

from core.outcome_tracker import (
    _calc_hypothetical_pnl,
    _detect_category,
    calibration_score,
    hypothetical_roi,
    win_rate_by_category,
)


# ─── P&L calculation ──────────────────────────────────────

def test_pnl_win_yes():
    """Ставка YES, рынок resolved YES — выигрыш."""
    pnl = _calc_hypothetical_pnl("YES", 0.72, 0.60, 3.0, resolved_yes=True)
    # Выигрыш: 3.0 * (1/0.60 - 1) = 3.0 * 0.6667 = 2.0
    assert pnl > 0
    assert abs(pnl - 2.0) < 0.01


def test_pnl_lose_yes():
    """Ставка YES, рынок resolved NO — проигрыш."""
    pnl = _calc_hypothetical_pnl("YES", 0.72, 0.60, 3.0, resolved_yes=False)
    assert pnl == -3.0


def test_pnl_win_no():
    """Ставка NO, рынок resolved NO — выигрыш."""
    pnl = _calc_hypothetical_pnl("NO", 0.30, 0.45, 2.0, resolved_yes=False)
    # Выигрыш: 2.0 * (1/0.45 - 1) = 2.0 * 1.2222 = 2.4444
    assert pnl > 0
    assert abs(pnl - 2.4444) < 0.01


def test_pnl_lose_no():
    """Ставка NO, рынок resolved YES — проигрыш."""
    pnl = _calc_hypothetical_pnl("NO", 0.30, 0.45, 2.0, resolved_yes=True)
    assert pnl == -2.0


def test_pnl_zero_bet():
    """Нулевая ставка — P&L 0."""
    pnl = _calc_hypothetical_pnl("YES", 0.72, 0.60, 0.0, resolved_yes=True)
    assert pnl == 0.0


# ─── Category detection ───────────────────────────────────

def test_detect_crypto():
    assert _detect_category("Will Bitcoin exceed $100k by March?") == "crypto"


def test_detect_politics():
    assert _detect_category("Will Trump win the 2026 election?") == "politics"


def test_detect_tech():
    assert _detect_category("Will OpenAI release GPT-5 before July?") == "tech"


def test_detect_geopolitics():
    assert _detect_category("Will Ukraine and Russia agree to a ceasefire?") == "geopolitics"


def test_detect_economics():
    assert _detect_category("Will inflation drop below 3% this year?") == "economics"


def test_detect_other():
    assert _detect_category("Will it rain tomorrow in Paris?") == "other"


def test_detect_case_insensitive():
    assert _detect_category("BITCOIN TO THE MOON") == "crypto"


# ─── Calibration score ────────────────────────────────────

def _make_outcome(our_prob, our_side, won):
    return {
        "our_prob": our_prob,
        "our_side": our_side,
        "won": won,
        "question": "Test question",
        "bet_amount": 1.0,
        "hypothetical_pnl": 0.5 if won else -1.0,
    }


def test_calibration_empty():
    assert calibration_score([]) == {}


def test_calibration_perfect():
    """Идеальная калибровка: предсказал 100% и выиграл."""
    outcomes = [_make_outcome(0.99, "YES", True)]
    cal = calibration_score(outcomes)
    assert cal["brier_score"] < 0.01  # почти идеально


def test_calibration_worst():
    """Худшая калибровка: предсказал 99% и проиграл."""
    outcomes = [_make_outcome(0.99, "YES", False)]
    cal = calibration_score(outcomes)
    assert cal["brier_score"] > 0.9  # почти максимальная ошибка


def test_calibration_buckets():
    """Проверяем что buckets создаются корректно."""
    outcomes = [
        _make_outcome(0.65, "YES", True),
        _make_outcome(0.75, "YES", False),
        _make_outcome(0.75, "YES", True),
    ]
    cal = calibration_score(outcomes)
    assert "60-70%" in cal["buckets"]
    assert "70-80%" in cal["buckets"]
    assert cal["buckets"]["60-70%"]["total"] == 1
    assert cal["buckets"]["70-80%"]["total"] == 2


def test_calibration_no_side():
    """Калибровка для ставки на NO — expressed_prob должна быть 1 - our_prob."""
    outcomes = [_make_outcome(0.30, "NO", True)]  # our_prob=0.30 на NO → expressed=0.70
    cal = calibration_score(outcomes)
    assert "70-80%" in cal["buckets"]


# ─── Hypothetical ROI ─────────────────────────────────────

def test_roi_empty():
    assert hypothetical_roi([]) == {}


def test_roi_all_wins():
    outcomes = [
        _make_outcome(0.70, "YES", True),
        _make_outcome(0.80, "YES", True),
    ]
    roi = hypothetical_roi(outcomes)
    assert roi["win_rate"] == 1.0
    assert roi["wins"] == 2
    assert roi["total"] == 2
    assert roi["total_pnl"] > 0


def test_roi_all_losses():
    outcomes = [
        _make_outcome(0.70, "YES", False),
        _make_outcome(0.80, "YES", False),
    ]
    roi = hypothetical_roi(outcomes)
    assert roi["win_rate"] == 0.0
    assert roi["total_pnl"] < 0


def test_roi_mixed():
    outcomes = [
        _make_outcome(0.70, "YES", True),
        _make_outcome(0.80, "YES", False),
    ]
    roi = hypothetical_roi(outcomes)
    assert roi["win_rate"] == 0.5


# ─── Win rate by category ─────────────────────────────────

def test_category_grouping():
    outcomes = [
        {**_make_outcome(0.70, "YES", True), "question": "Will Bitcoin hit 100k?"},
        {**_make_outcome(0.60, "YES", False), "question": "Will Bitcoin drop below 80k?"},
        {**_make_outcome(0.75, "YES", True), "question": "Will Trump sign the bill?"},
    ]
    cats = win_rate_by_category(outcomes)
    assert "crypto" in cats
    assert "politics" in cats
    assert cats["crypto"]["total"] == 2
    assert cats["crypto"]["wins"] == 1
    assert cats["politics"]["total"] == 1
    assert cats["politics"]["wins"] == 1
