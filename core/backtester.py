"""Backtester — перебор параметров стратегии на исторических данных.

Использует bets + outcomes из SQLite для симуляции разных MIN_EDGE и KELLY_FRACTION.
Не вызывает Claude — работает только с уже записанными решениями.

Запуск:
    python -m core.backtester
    python -m core.backtester --min-edge 0.03 0.05 0.08 0.10 --kelly 0.15 0.25 0.35
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass

from core.database import init_db, load_bets, load_outcomes


@dataclass
class BacktestResult:
    min_edge: float
    kelly_fraction: float
    total_bets: int
    wins: int
    losses: int
    pending: int
    total_wagered: float
    total_pnl: float
    roi_pct: float
    win_rate: float
    avg_edge: float


def run_backtest(
    bets: list[dict],
    outcomes_map: dict[str, dict],
    min_edge: float,
    kelly_fraction: float,
    budget: float = 100.0,
    max_bet: float = 5.0,
) -> BacktestResult:
    """Симулирует стратегию с заданными параметрами на исторических данных."""

    wins = 0
    losses = 0
    pending = 0
    total_wagered = 0.0
    total_pnl = 0.0
    edges = []

    for bet in bets:
        edge = abs(bet["edge"])
        if edge < min_edge:
            continue

        # Пересчитываем Kelly с новым fraction
        our_prob = bet["our_prob"]
        market_prob = bet["market_prob"]
        side = bet["side"]

        if side == "YES":
            prob_for_kelly = our_prob
        else:
            prob_for_kelly = 1 - our_prob

        b = (1 / market_prob) - 1
        q = 1 - prob_for_kelly
        kelly = (prob_for_kelly * b - q) / b
        bet_amount = kelly * kelly_fraction * budget
        bet_amount = min(bet_amount, max_bet)
        bet_amount = max(bet_amount, 0.0)

        if bet_amount <= 0:
            continue

        edges.append(edge)
        cid = bet.get("condition_id", "")
        outcome = outcomes_map.get(cid)

        if outcome is None:
            pending += 1
            continue

        total_wagered += bet_amount

        won = outcome["won"]
        if won:
            wins += 1
            pnl = bet_amount * (1 / market_prob - 1)
        else:
            losses += 1
            pnl = -bet_amount

        total_pnl += pnl

    resolved = wins + losses
    return BacktestResult(
        min_edge=min_edge,
        kelly_fraction=kelly_fraction,
        total_bets=resolved + pending,
        wins=wins,
        losses=losses,
        pending=pending,
        total_wagered=round(total_wagered, 2),
        total_pnl=round(total_pnl, 2),
        roi_pct=round(total_pnl / total_wagered * 100, 1) if total_wagered > 0 else 0.0,
        win_rate=round(wins / resolved, 3) if resolved > 0 else 0.0,
        avg_edge=round(sum(edges) / len(edges), 4) if edges else 0.0,
    )


def run_grid(
    bets: list[dict],
    outcomes_map: dict[str, dict],
    edges: list[float],
    kellys: list[float],
    budget: float = 100.0,
    max_bet: float = 5.0,
) -> list[BacktestResult]:
    """Перебирает все комбинации параметров."""
    results = []
    for me in edges:
        for kf in kellys:
            r = run_backtest(bets, outcomes_map, me, kf, budget, max_bet)
            results.append(r)
    return results


def print_grid(results: list[BacktestResult]) -> None:
    """Печатает таблицу результатов."""
    print(f"\n{'='*80}")
    print(f"{'MIN_EDGE':>10} {'KELLY':>8} {'BETS':>6} {'W/L':>8} {'WAGERED':>10} {'P&L':>10} {'ROI':>8} {'WIN%':>7}")
    print(f"{'='*80}")

    for r in sorted(results, key=lambda x: -x.roi_pct):
        wl = f"{r.wins}/{r.losses}"
        pending = f" +{r.pending}p" if r.pending else ""
        print(
            f"{r.min_edge:>10.2%} {r.kelly_fraction:>8.2f} "
            f"{r.total_bets:>6}{pending} {wl:>8} "
            f"${r.total_wagered:>9.2f} ${r.total_pnl:>+9.2f} "
            f"{r.roi_pct:>+7.1f}% {r.win_rate:>6.0%}"
        )

    print(f"{'='*80}")


def main():
    parser = argparse.ArgumentParser(description="Backtest стратегии на исторических данных")
    parser.add_argument("--min-edge", nargs="+", type=float,
                        default=[0.03, 0.05, 0.08, 0.10, 0.15],
                        help="Значения MIN_EDGE для перебора")
    parser.add_argument("--kelly", nargs="+", type=float,
                        default=[0.10, 0.15, 0.20, 0.25, 0.30, 0.40],
                        help="Значения KELLY_FRACTION для перебора")
    parser.add_argument("--budget", type=float, default=100.0)
    parser.add_argument("--max-bet", type=float, default=5.0)
    args = parser.parse_args()

    init_db()
    bets = load_bets()
    outcomes = load_outcomes()

    if not bets:
        print("Нет данных для бэктеста. Запусти бота: python main.py")
        return

    outcomes_map = {o["condition_id"]: o for o in outcomes}

    print(f"Загружено: {len(bets)} ставок, {len(outcomes)} исходов")
    print(f"Перебор: {len(args.min_edge)} x {len(args.kelly)} = {len(args.min_edge) * len(args.kelly)} комбинаций")

    results = run_grid(bets, outcomes_map, args.min_edge, args.kelly, args.budget, args.max_bet)
    print_grid(results)

    # Лучший по ROI
    best = max(results, key=lambda x: x.roi_pct)
    if best.total_wagered > 0:
        print(f"\nЛучшая комбинация: MIN_EDGE={best.min_edge:.2%}, KELLY={best.kelly_fraction:.2f}")
        print(f"  ROI={best.roi_pct:+.1f}%, Win rate={best.win_rate:.0%}, P&L=${best.total_pnl:+.2f}")


if __name__ == "__main__":
    main()
