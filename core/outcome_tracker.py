from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import aiohttp

GAMMA_BASE = "https://gamma-api.polymarket.com"
HISTORY_FILE = Path("data/bet_history.json")
OUTCOMES_FILE = Path("data/outcomes.json")


def _load_json(path: Path) -> list:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []
    return []


def _save_outcomes(outcomes: list) -> None:
    OUTCOMES_FILE.write_text(json.dumps(outcomes, ensure_ascii=False, indent=2), encoding="utf-8")


def _calc_hypothetical_pnl(side: str, our_prob: float, market_prob: float, bet_amount: float, resolved_yes: bool) -> float:
    """Гипотетический P&L если бы ставка была реальной."""
    won = (side == "YES" and resolved_yes) or (side == "NO" and not resolved_yes)
    if won:
        price = market_prob  # цена по которой ставили
        return round(bet_amount * (1 / price - 1), 4)
    else:
        return round(-bet_amount, 4)


async def check_resolved_markets() -> int:
    """Проверяет bet_history на разрешённые рынки и дописывает outcomes.json.
    Возвращает количество новых записей."""
    history = _load_json(HISTORY_FILE)
    if not history:
        return 0

    outcomes = _load_json(OUTCOMES_FILE)
    tracked_ids = {o["condition_id"] for o in outcomes}

    now = datetime.now(timezone.utc)
    to_check = [
        r for r in history
        if r.get("condition_id")
        and r["condition_id"] not in tracked_ids
        and r.get("end_date")
        and r["end_date"] < now.isoformat()
    ]

    if not to_check:
        return 0

    new_count = 0
    async with aiohttp.ClientSession() as session:
        for record in to_check:
            condition_id = record["condition_id"]
            result = await _fetch_resolution(session, condition_id)
            if result is None:
                continue  # ещё не разрешён или ошибка

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
            outcomes.append(outcome)
            new_count += 1

            side_str = "YES" if resolved_yes else "NO"
            result_str = "ВЫИГРАЛ" if won else "ПРОИГРАЛ"
            print(f"  [Tracker] {record['question'][:55]} → {side_str} | {result_str} | P&L: {pnl:+.2f}")

    if new_count:
        _save_outcomes(outcomes)

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

    # Для калибровки используем вероятность нашей ставки (не всегда our_prob=prob YES)
    brier_sum = 0.0
    buckets: dict[str, dict] = {}

    for o in outcomes:
        # expressed_prob = вероятность того исхода, на который мы ставили
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
    """Гипотетический P&L и ROI по всем разрешённым ставкам."""
    if not outcomes:
        return {}

    total_pnl = sum(o["hypothetical_pnl"] for o in outcomes)
    total_bet = sum(o["bet_amount"] for o in outcomes)
    wins = sum(1 for o in outcomes if o["won"])

    return {
        "total_pnl": round(total_pnl, 2),
        "total_bet": round(total_bet, 2),
        "roi_pct": round(total_pnl / total_bet * 100, 1) if total_bet > 0 else 0.0,
        "win_rate": round(wins / len(outcomes), 3),
        "wins": wins,
        "total": len(outcomes),
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
    """Загружает outcomes.json и печатает все калибровочные метрики."""
    outcomes = _load_json(OUTCOMES_FILE)
    if not outcomes:
        return

    roi = hypothetical_roi(outcomes)
    cal = calibration_score(outcomes)
    cats = win_rate_by_category(outcomes)

    print(f"\n=== КАЛИБРОВКА ({roi['total']} исходов) ===")
    print(f"  Win rate:        {roi['win_rate']:.0%}  ({roi['wins']}/{roi['total']})")
    print(f"  Гипотет. P&L:   ${roi['total_pnl']:+.2f}  (ROI {roi['roi_pct']:+.1f}%)")
    print(f"  Brier score:     {cal['brier_score']:.4f}  (цель < 0.05)")

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
    """Запрашивает Gamma API и возвращает {resolved_yes, resolved_at} если рынок разрешён."""
    try:
        async with session.get(
            f"{GAMMA_BASE}/markets",
            params={"conditionIds": condition_id},
        ) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
    except Exception:
        return None

    if not data:
        return None

    market = data[0] if isinstance(data, list) else data
    if not market.get("resolved"):
        return None

    resolution_price = market.get("resolutionPrice")
    if resolution_price is None:
        return None

    resolved_yes = float(resolution_price) >= 0.99
    resolved_at = market.get("resolutionDate") or market.get("endDateIso") or ""

    return {"resolved_yes": resolved_yes, "resolved_at": resolved_at}
