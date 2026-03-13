import json
from datetime import datetime
from pathlib import Path

HISTORY_FILE = Path("bet_history.json")


def _load_history() -> list:
    if HISTORY_FILE.exists():
        try:
            return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []
    return []


def _save_history(history: list) -> None:
    HISTORY_FILE.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")


def log_decision(market: dict, result, dry_run: bool) -> None:
    """Записывает решение бота в bet_history.json и выводит в консоль."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    question = market["question"]

    if result is None:
        print(f"  [{timestamp}] ПРОПУСК: {question[:60]}")
        return

    action = "[DRY RUN]" if dry_run else "[LIVE]"
    print(
        f"  [{timestamp}] {action} СТАВКА\n"
        f"    Вопрос:  {question[:70]}\n"
        f"    Наша оценка: {result['our_prob']:.0%} | Рынок: {result['market_prob']:.0%} | Edge: {result['edge']:+.1%}\n"
        f"    Ставка:  {result['side']} ${result['bet_amount']:.2f} | conf={result['confidence']}\n"
        f"    Причина: {result['reasoning']}"
    )

    record = {
        "timestamp": timestamp,
        "question": question,
        "condition_id": market.get("condition_id", ""),
        "our_prob": result["our_prob"],
        "market_prob": result["market_prob"],
        "edge": round(result["edge"], 4),
        "confidence": result["confidence"],
        "side": result["side"],
        "bet_amount": result["bet_amount"],
        "dry_run": dry_run,
        "reasoning": result["reasoning"],
    }

    history = _load_history()
    history.append(record)
    _save_history(history)


def print_summary(history: list) -> None:
    """Выводит статистику по накопленной истории."""
    if not history:
        print("История пуста.")
        return

    total = len(history)
    total_bet = sum(r["bet_amount"] for r in history)
    avg_edge = sum(r["edge"] for r in history) / total

    print(f"\n=== СТАТИСТИКА ({total} ставок) ===")
    print(f"  Общая сумма ставок: ${total_bet:.2f}")
    print(f"  Средний edge:       {avg_edge:+.1%}")
    print(f"  DRY RUN:            {sum(1 for r in history if r['dry_run'])}")
    print(f"  LIVE:               {sum(1 for r in history if not r['dry_run'])}")
