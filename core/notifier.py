"""Telegram уведомления через Bot API.

Отправляет алерты при:
  - Найден edge → ставка (DRY RUN или LIVE)
  - Рынок разрешён → выигрыш/проигрыш
  - Ошибки в работе бота

Настройка:
  TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID в .env
  Если не заданы — уведомления отключены (silent mode).
"""
from __future__ import annotations

import aiohttp
import config

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


def _enabled() -> bool:
    return bool(config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID)


async def send(text: str, silent: bool = False) -> bool:
    """Отправляет сообщение в Telegram. Возвращает True при успехе."""
    if not _enabled():
        return False

    url = TELEGRAM_API.format(token=config.TELEGRAM_BOT_TOKEN)
    payload = {
        "chat_id": config.TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_notification": silent,
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    print(f"  [TG] Ошибка {resp.status}: {body[:100]}")
                    return False
                return True
    except Exception as e:
        print(f"  [TG] Ошибка отправки: {e}")
        return False


async def notify_bet(market: dict, result: dict, dry_run: bool) -> None:
    """Уведомление о новой ставке."""
    mode = "🧪 DRY RUN" if dry_run else "🔴 LIVE"
    text = (
        f"{mode} <b>СТАВКА</b>\n\n"
        f"❓ {_escape(market['question'][:80])}\n"
        f"📊 Наша: <b>{result['our_prob']:.0%}</b> | Рынок: {result['market_prob']:.0%}\n"
        f"📈 Edge: <b>{result['edge']:+.1%}</b> | Сторона: <b>{result['side']}</b>\n"
        f"💰 Ставка: ${result['bet_amount']:.2f}\n"
        f"💡 {_escape(result['reasoning'][:150])}"
    )
    await send(text)


async def notify_outcome(question: str, won: bool, pnl: float, side: str) -> None:
    """Уведомление о разрешённом рынке."""
    emoji = "✅" if won else "❌"
    result = "ВЫИГРАЛ" if won else "ПРОИГРАЛ"
    text = (
        f"{emoji} <b>{result}</b>\n\n"
        f"❓ {_escape(question[:80])}\n"
        f"📊 Сторона: {side} | P&L: <b>${pnl:+.2f}</b>"
    )
    await send(text)


async def notify_cycle_summary(cycle: int, analyzed: int, bets_placed: int, new_outcomes: int) -> None:
    """Краткая сводка после цикла (отправляется тихо)."""
    if bets_placed == 0 and new_outcomes == 0:
        return  # не спамить если ничего не произошло

    parts = [f"🔄 <b>Цикл #{cycle}</b>"]
    parts.append(f"Проанализировано: {analyzed} рынков")
    if bets_placed:
        parts.append(f"Новых ставок: {bets_placed}")
    if new_outcomes:
        parts.append(f"Разрешено рынков: {new_outcomes}")
    await send("\n".join(parts), silent=True)


async def notify_error(error: str) -> None:
    """Уведомление об ошибке."""
    text = f"⚠️ <b>Ошибка бота</b>\n\n{_escape(error[:300])}"
    await send(text)


def _escape(text: str) -> str:
    """Экранирует HTML спецсимволы."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
