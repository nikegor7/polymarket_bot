"""Тесты для backtester."""
from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ.setdefault("GNEWS_API_KEY", "test-key")

from core.backtester import run_backtest, run_grid


def _make_bet(cid, edge, our_prob, market_prob, side="YES"):
    return {
        "condition_id": cid,
        "edge": edge,
        "our_prob": our_prob,
        "market_prob": market_prob,
        "side": side,
        "bet_amount": 2.0,
    }


def _make_outcome(cid, won):
    return {cid: {"won": won}}


def test_backtest_win():
    bets = [_make_bet("0x1", 0.10, 0.70, 0.60)]
    outcomes_map = {"0x1": {"won": True}}
    r = run_backtest(bets, outcomes_map, min_edge=0.05, kelly_fraction=0.25)
    assert r.wins == 1
    assert r.losses == 0
    assert r.total_pnl > 0
    assert r.win_rate == 1.0


def test_backtest_loss():
    bets = [_make_bet("0x1", 0.10, 0.70, 0.60)]
    outcomes_map = {"0x1": {"won": False}}
    r = run_backtest(bets, outcomes_map, min_edge=0.05, kelly_fraction=0.25)
    assert r.wins == 0
    assert r.losses == 1
    assert r.total_pnl < 0


def test_backtest_filters_low_edge():
    bets = [
        _make_bet("0x1", 0.02, 0.52, 0.50),  # edge < min_edge
        _make_bet("0x2", 0.10, 0.70, 0.60),  # edge ok
    ]
    outcomes_map = {"0x1": {"won": True}, "0x2": {"won": True}}
    r = run_backtest(bets, outcomes_map, min_edge=0.05, kelly_fraction=0.25)
    assert r.total_bets == 1  # only 0x2 passes


def test_backtest_pending():
    bets = [_make_bet("0x1", 0.10, 0.70, 0.60)]
    outcomes_map = {}  # no outcome
    r = run_backtest(bets, outcomes_map, min_edge=0.05, kelly_fraction=0.25)
    assert r.pending == 1
    assert r.wins == 0


def test_grid_runs():
    bets = [_make_bet("0x1", 0.10, 0.70, 0.60)]
    outcomes_map = {"0x1": {"won": True}}
    results = run_grid(bets, outcomes_map, edges=[0.05, 0.10], kellys=[0.20, 0.30])
    assert len(results) == 4  # 2x2 grid


def test_backtest_no_side():
    bets = [_make_bet("0x1", 0.10, 0.30, 0.45, side="NO")]
    outcomes_map = {"0x1": {"won": True}}
    r = run_backtest(bets, outcomes_map, min_edge=0.05, kelly_fraction=0.25)
    assert r.wins == 1
    assert r.total_pnl > 0


def test_backtest_empty():
    r = run_backtest([], {}, min_edge=0.05, kelly_fraction=0.25)
    assert r.total_bets == 0
    assert r.total_pnl == 0.0
    assert r.roi_pct == 0.0
