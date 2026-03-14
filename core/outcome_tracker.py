import json
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

    resolved_yes = float(resolution_price) == 1.0
    resolved_at = market.get("resolutionDate") or market.get("endDateIso") or ""

    return {"resolved_yes": resolved_yes, "resolved_at": resolved_at}
