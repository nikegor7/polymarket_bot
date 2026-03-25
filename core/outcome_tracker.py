from __future__ import annotations

import re
import time
from datetime import datetime, timezone

import aiohttp

from core.database import (
    get_tracked_condition_ids,
    insert_outcome,
    load_bets,
    load_outcomes,
)
from core import notifier

GAMMA_BASE = "https://gamma-api.polymarket.com"

# Cooldown: не проверять один и тот же рынок чаще чем раз в 10 минут
_last_checked: dict[str, float] = {}
_CHECK_COOLDOWN = 600  # секунд (было 3600)


def _calc_hypothetical_pnl(side: str, our_prob: float, market_prob: float, bet_amount: float, resolved_yes: bool) -> float:
    """Гипотетический P&L если бы ставка была реальной.
    market_prob = yes_price (вероятность YES по рынку)."""
    won = (side == "YES" and resolved_yes) or (side == "NO" and not resolved_yes)
    if won:
        # Цена стороны: YES = market_prob, NO = 1 - market_prob
        price = market_prob if side == "YES" else (1 - market_prob)
        price = max(price, 0.001)  # защита от деления на 0
        return round(bet_amount * (1 / price - 1), 4)
    else:
        return round(-bet_amount, 4)


async def check_resolved_markets() -> int:
    """Проверяет bets на разрешённые рынки и записывает outcomes.
    Возвращает количество новых записей."""
    history = load_bets()
    if not history:
        return 0

    tracked_ids = get_tracked_condition_ids()
    now = datetime.now(timezone.utc)

    to_check = [
        r for r in history
        if r.get("condition_id")
        and len(r["condition_id"]) > 10  # пропуск dummy ID
        and r["condition_id"] not in tracked_ids
        and time.time() - _last_checked.get(r["condition_id"], 0) > _CHECK_COOLDOWN
    ]

    if not to_check:
        return 0

    new_count = 0
    async with aiohttp.ClientSession() as session:
        for record in to_check:
            condition_id = record["condition_id"]
            _last_checked[condition_id] = time.time()
            result = await _fetch_resolution(session, condition_id)
            if result is None:
                continue

            resolved_yes = result["resolved_yes"]
            won = (record["side"] == "YES" and resolved_yes) or (record["side"] == "NO" and not resolved_yes)
            pnl = _calc_hypothetical_pnl(
                record["side"], record["our_prob"], record["market_prob"],
                record["bet_amount"], resolved_yes,
            )

            outcome = {
                "condition_id": condition_id,
                "question": record["question"],
                "our_side": record["side"],
                "our_prob": record["our_prob"],
                "market_prob": record["market_prob"],
                "bet_amount": record["bet_amount"],
                "resolved_yes": resolved_yes,
                "won": won,
                "hypothetical_pnl": pnl,
                "resolved_at": result["resolved_at"],
            }
            insert_outcome(outcome)
            new_count += 1

            side_str = "YES" if resolved_yes else "NO"
            result_str = "ВЫИГРАЛ" if won else "ПРОИГРАЛ"
            print(f"  [Tracker] {record['question'][:55]} → {side_str} | {result_str} | P&L: {pnl:+.2f}")
            await notifier.notify_outcome(record["question"], won, pnl, record["side"])

    return new_count


_CATEGORIES = {
    "crypto":      ["bitcoin", "ethereum", "crypto", "btc", "eth", "solana", "sol",
                    "coinbase", "binance", "stablecoin", "defi", "nft", "blockchain"],
    "politics":    ["trump", "election", "president", "congress", "senate", "fed",
                    "white house", "executive order", "tariff", "sanction"],
    "economics":   ["inflation", "recession", "gdp", "interest rate", "dollar",
                    "stock market", "s&p", "nasdaq", "oil", "gold"],
    "tech":        ["openai", "gpt", "artificial intelligence", "ai ", "apple",
                    "tesla", "spacex", "elon", "google", "microsoft"],
    "geopolitics": ["ukraine", "russia", "china", "iran", "war", "ceasefire",
                    "nato", "israel", "gaza"],
}


def _detect_category(question: str) -> str:
    q = question.lower()
    for category, keywords in _CATEGORIES.items():
        if any(re.search(r'\b' + re.escape(kw.strip()) + r'\b', q) for kw in keywords):
            return category
    return "other"


def calibration_score(outcomes: list) -> dict:
    """Brier score + breakdown по диапазонам вероятности."""
    if not outcomes:
        return {}

    brier_sum = 0.0
    buckets: dict[str, dict] = {}

    for o in outcomes:
        expressed_prob = o["our_prob"] if o["our_side"] == "YES" else 1 - o["our_prob"]
        actual = 1.0 if o["won"] else 0.0
        brier_sum += (expressed_prob - actual) ** 2

        low = int(expressed_prob * 10) * 10
        key = f"{low}-{low + 10}%"
        if key not in buckets:
            buckets[key] = {"wins": 0, "total": 0}
        buckets[key]["total"] += 1
        if o["won"]:
            buckets[key]["wins"] += 1

    return {
        "brier_score": round(brier_sum / len(outcomes), 4),
        "buckets": buckets,
        "total": len(outcomes),
    }


def hypothetical_roi(outcomes: list) -> dict:
    """Гипотетический P&L и ROI по всем разрешённым ставкам + Sharpe + max drawdown."""
    if not outcomes:
        return {}

    total_pnl = sum(o["hypothetical_pnl"] for o in outcomes)
    total_bet = sum(o["bet_amount"] for o in outcomes)
    wins = sum(1 for o in outcomes if o["won"])

    # Max drawdown — наибольшая просадка
    cumulative = 0.0
    peak = 0.0
    max_drawdown = 0.0
    pnl_list = []
    for o in outcomes:
        pnl = o["hypothetical_pnl"]
        pnl_list.append(pnl)
        cumulative += pnl
        if cumulative > peak:
            peak = cumulative
        dd = peak - cumulative
        if dd > max_drawdown:
            max_drawdown = dd

    # Sharpe ratio (simplified: avg return / std dev)
    sharpe = 0.0
    if len(pnl_list) >= 3:
        avg_pnl = sum(pnl_list) / len(pnl_list)
        variance = sum((p - avg_pnl) ** 2 for p in pnl_list) / len(pnl_list)
        std_dev = variance ** 0.5
        if std_dev > 0:
            sharpe = round(avg_pnl / std_dev, 2)

    # Average edge на выигранных vs проигранных
    won_edges = [o.get("our_prob", 0.5) - o.get("market_prob", 0.5) for o in outcomes if o["won"]]
    lost_edges = [o.get("our_prob", 0.5) - o.get("market_prob", 0.5) for o in outcomes if not o["won"]]
    avg_edge_won = round(sum(won_edges) / len(won_edges), 3) if won_edges else 0.0
    avg_edge_lost = round(sum(lost_edges) / len(lost_edges), 3) if lost_edges else 0.0

    return {
        "total_pnl": round(total_pnl, 2),
        "total_bet": round(total_bet, 2),
        "roi_pct": round(total_pnl / total_bet * 100, 1) if total_bet > 0 else 0.0,
        "win_rate": round(wins / len(outcomes), 3),
        "wins": wins,
        "total": len(outcomes),
        "max_drawdown": round(max_drawdown, 2),
        "sharpe": sharpe,
        "avg_edge_won": avg_edge_won,
        "avg_edge_lost": avg_edge_lost,
    }


def win_rate_by_category(outcomes: list) -> dict[str, dict]:
    """Win rate разбитый по темам."""
    cats: dict[str, dict] = {}
    for o in outcomes:
        cat = _detect_category(o["question"])
        if cat not in cats:
            cats[cat] = {"wins": 0, "total": 0, "pnl": 0.0}
        cats[cat]["total"] += 1
        if o["won"]:
            cats[cat]["wins"] += 1
        cats[cat]["pnl"] = round(cats[cat]["pnl"] + o["hypothetical_pnl"], 2)
    return cats


def print_calibration_report() -> None:
    """Загружает outcomes из БД и печатает все калибровочные метрики."""
    outcomes = load_outcomes()
    if not outcomes:
        return

    roi = hypothetical_roi(outcomes)
    cal = calibration_score(outcomes)
    cats = win_rate_by_category(outcomes)

    print(f"\n=== КАЛИБРОВКА ({roi['total']} исходов) ===")
    print(f"  Win rate:        {roi['win_rate']:.0%}  ({roi['wins']}/{roi['total']})")
    print(f"  Гипотет. P&L:   ${roi['total_pnl']:+.2f}  (ROI {roi['roi_pct']:+.1f}%)")
    print(f"  Brier score:     {cal['brier_score']:.4f}  (цель < 0.05)")
    print(f"  Max drawdown:    ${roi['max_drawdown']:.2f}")
    if roi.get("sharpe"):
        print(f"  Sharpe ratio:    {roi['sharpe']:.2f}")
    if roi.get("avg_edge_won") or roi.get("avg_edge_lost"):
        print(f"  Avg edge (win):  {roi['avg_edge_won']:+.1%} | (loss): {roi['avg_edge_lost']:+.1%}")

    if cal.get("buckets"):
        print("  По диапазонам вероятности:")
        for bucket, data in sorted(cal["buckets"].items()):
            wr = data["wins"] / data["total"] if data["total"] else 0
            print(f"    {bucket:9s} → {data['wins']}/{data['total']} выиграно ({wr:.0%})")

    if cats:
        print("  По темам:")
        for cat, data in sorted(cats.items(), key=lambda x: -x[1]["total"]):
            wr = data["wins"] / data["total"] if data["total"] else 0
            print(f"    {cat:12s} → {data['wins']}/{data['total']} ({wr:.0%}) | P&L ${data['pnl']:+.2f}")


async def _fetch_resolution(session: aiohttp.ClientSession, condition_id: str) -> dict | None:
    """Запрашивает Gamma API и возвращает {resolved_yes, resolved_at} если рынок разрешён.
    Проверяет dispute status — не записывает outcome если рынок оспорен."""
    try:
        async with session.get(
            f"{GAMMA_BASE}/markets",
            params={"conditionIds": condition_id},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
    except Exception:
        return None

    if not data:
        return None

    market = data[0] if isinstance(data, list) else data

    # Проверка dispute status — если оспорен, не записываем outcome
    uma_status = market.get("uma_resolution_status") or market.get("umaResolutionStatus") or ""
    if uma_status.lower() in ("disputed", "pending", "challenge"):
        print(f"  [Tracker] {condition_id[:12]}... — UMA disputed ({uma_status}), ждём финального решения")
        return None

    if not market.get("resolved"):
        return None

    resolution_price = market.get("resolutionPrice")
    if resolution_price is None:
        return None

    # Поддержка partial resolution (>= 0.5 = YES wins)
    res_price = float(resolution_price)
    resolved_yes = res_price >= 0.5
    resolved_at = market.get("resolutionDate") or market.get("endDateIso") or ""

    return {"resolved_yes": resolved_yes, "resolved_at": resolved_at, "resolution_price": res_price}
