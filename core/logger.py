from __future__ import annotations

from datetime import datetime

from core.database import insert_bet, load_bets


def log_decision(market: dict, result, dry_run: bool) -> None:
    """Записывает решение бота в БД и выводит в консоль."""
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
        "end_date": market.get("end_date", ""),
        "our_prob": result["our_prob"],
        "market_prob": result["market_prob"],
        "edge": round(result["edge"], 4),
        "confidence": result["confidence"],
        "side": result["side"],
        "bet_amount": result["bet_amount"],
        "dry_run": dry_run,
        "reasoning": result["reasoning"],
        "prompt_text": result.get("prompt_text", ""),
        "raw_response": result.get("raw_response", ""),
    }

    insert_bet(record)


def print_summary(bets: list) -> None:
    """Выводит статистику по накопленной истории."""
    if not bets:
        print("История пуста.")
        return

    total = len(bets)
    total_bet = sum(r["bet_amount"] for r in bets)
    avg_edge = sum(r["edge"] for r in bets) / total

    print(f"\n=== СТАТИСТИКА ({total} ставок) ===")
    print(f"  Общая сумма ставок: ${total_bet:.2f}")
    print(f"  Средний edge:       {avg_edge:+.1%}")
    print(f"  DRY RUN:            {sum(1 for r in bets if r['dry_run'])}")
    print(f"  LIVE:               {sum(1 for r in bets if not r['dry_run'])}")
