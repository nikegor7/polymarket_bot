"""Telegram уведомления и команды через Bot API.

Отправляет алерты при:
  - Найден edge → ставка (DRY RUN или LIVE)
  - Рынок разрешён → выигрыш/проигрыш
  - Ошибки в работе бота

Принимает команды: /stats, /last, /help

Безопасность:
  TELEGRAM_ALLOWED_USERS в .env — только эти user ID могут слать команды.
  По умолчанию = только владелец (TELEGRAM_CHAT_ID).
  Посторонние пользователи получают отказ.

Реализация: urllib (синхронный) + asyncio.to_thread() — обходит проблемы
aiohttp + WindowsSelectorEventLoopPolicy на Windows.
"""
from __future__ import annotations

import asyncio
import json
import urllib.request
import urllib.error
import urllib.parse
import config

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"

_poll_offset: int = 0
_consecutive_errors: int = 0  # для backoff при сетевых ошибках


def _tg_post(method: str, payload: dict) -> tuple[int, dict]:
    """Синхронный POST к Telegram API."""
    url = TELEGRAM_API.format(token=config.TELEGRAM_BOT_TOKEN, method=method)
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        try:
            return e.code, json.loads(body)
        except json.JSONDecodeError:
            return e.code, {"error": body[:200]}


def _tg_get(method: str, params: dict) -> tuple[int, dict]:
    """Синхронный GET к Telegram API."""
    url = TELEGRAM_API.format(token=config.TELEGRAM_BOT_TOKEN, method=method)
    qs = urllib.parse.urlencode(params)
    req = urllib.request.Request(f"{url}?{qs}")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        try:
            return e.code, json.loads(body)
        except json.JSONDecodeError:
            return e.code, {"error": body[:200]}


async def close() -> None:
    """Заглушка для совместимости с main.py."""
    pass


def _enabled() -> bool:
    return bool(config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID)


def is_authorized(user_id: int) -> bool:
    """Проверяет имеет ли пользователь доступ к боту."""
    return user_id in config.TELEGRAM_ALLOWED_USERS


async def send(text: str, silent: bool = False, chat_id: str = "") -> bool:
    """Отправляет сообщение в Telegram. Возвращает True при успехе."""
    if not _enabled():
        return False

    payload = {
        "chat_id": chat_id or config.TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_notification": silent,
    }

    try:
        status, data = await asyncio.to_thread(_tg_post, "sendMessage", payload)
        if status != 200:
            print(f"  [TG] Ошибка {status}: {str(data)[:100]}")
            return False
        return True
    except Exception as e:
        if _consecutive_errors <= 2:
            print(f"  [TG] Ошибка отправки: {type(e).__name__}: {e}")
        return False


async def poll_commands() -> list[dict]:
    """Получает новые сообщения от пользователей.
    Возвращает список {user_id, chat_id, text}."""
    global _poll_offset, _consecutive_errors
    if not _enabled():
        return []

    # Backoff при повторных ошибках: 3с → 6с → 12с → ... до 60с
    if _consecutive_errors > 0:
        delay = min(3 * (2 ** (_consecutive_errors - 1)), 60)
        await asyncio.sleep(delay)

    params = {"offset": _poll_offset, "timeout": 10, "allowed_updates": '["message","callback_query"]'}

    try:
        status, data = await asyncio.to_thread(_tg_get, "getUpdates", params)
        if status != 200:
            _consecutive_errors += 1
            return []
        _consecutive_errors = 0
    except Exception as e:
        _consecutive_errors += 1
        if _consecutive_errors <= 2:
            print(f"  [TG] Ошибка poll: {type(e).__name__}: {e}")
        elif _consecutive_errors == 3:
            print(f"  [TG] Сеть недоступна, подавляю повторные ошибки (backoff {min(3 * (2 ** (_consecutive_errors - 1)), 60)}с)")
        return []

    messages = []
    for update in data.get("result", []):
        _poll_offset = update["update_id"] + 1

        # Нажатие inline кнопки
        cb = update.get("callback_query")
        if cb:
            user_id = cb.get("from", {}).get("id", 0)
            chat_id = str(cb.get("message", {}).get("chat", {}).get("id", ""))
            text = cb.get("data", "")
            await _answer_callback(cb.get("id", ""))

            if not is_authorized(user_id):
                await send("⛔ Доступ запрещён.", chat_id=chat_id)
                continue

            messages.append({"user_id": user_id, "chat_id": chat_id, "text": text})
            continue

        # Текстовое сообщение
        msg = update.get("message", {})
        text = msg.get("text", "")
        user_id = msg.get("from", {}).get("id", 0)
        chat_id = str(msg.get("chat", {}).get("id", ""))

        if not text.startswith("/"):
            continue

        if not is_authorized(user_id):
            await send("⛔ Доступ запрещён.", chat_id=chat_id)
            print(f"  [TG] Отказ: user_id={user_id} попытался вызвать {text}")
            continue

        messages.append({"user_id": user_id, "chat_id": chat_id, "text": text})

    return messages


async def handle_commands() -> None:
    """Обрабатывает входящие команды от разрешённых пользователей."""
    from core.database import load_bets, load_outcomes
    from core.outcome_tracker import hypothetical_roi, calibration_score, win_rate_by_category

    commands = await poll_commands()
    for cmd in commands:
        text = cmd["text"].strip().lower()
        chat_id = cmd["chat_id"]

        if text == "/stats":
            bets = load_bets()
            outcomes = load_outcomes()
            roi = hypothetical_roi(outcomes) if outcomes else {}

            msg = f"📊 <b>Статистика</b>\n\n"
            msg += f"Ставок: {len(bets)}\n"
            if roi:
                msg += f"Исходов: {roi['total']}\n"
                msg += f"Win rate: {roi['win_rate']:.0%} ({roi['wins']}/{roi['total']})\n"
                msg += f"P&L: ${roi['total_pnl']:+.2f}\n"
                msg += f"ROI: {roi['roi_pct']:+.1f}%\n"
                msg += f"Max drawdown: ${roi['max_drawdown']:.2f}\n"
                if roi.get("sharpe"):
                    msg += f"Sharpe: {roi['sharpe']:.2f}\n"
                cal = calibration_score(outcomes)
                if cal:
                    msg += f"Brier: {cal['brier_score']:.4f}"
            else:
                msg += "Исходов пока нет."
            await send(msg, chat_id=chat_id)

        elif text == "/last":
            bets = load_bets()
            if not bets:
                await send("Ставок пока нет.", chat_id=chat_id)
                continue

            last5 = bets[-5:]
            lines = ["📋 <b>Последние 5 ставок</b>\n"]
            for b in reversed(last5):
                q = _escape(b["question"][:50])
                lines.append(f"• {b['side']} {b['edge']:+.1%} ${b['bet_amount']:.2f}\n  {q}")
            await send("\n".join(lines), chat_id=chat_id)

        elif text == "/accuracy":
            outcomes = load_outcomes()
            if not outcomes:
                await send("Исходов пока нет — accuracy неизвестен.", chat_id=chat_id)
                continue

            cats = win_rate_by_category(outcomes)
            cal = calibration_score(outcomes)
            roi = hypothetical_roi(outcomes)

            lines = ["🎯 <b>Accuracy Report</b>\n"]
            lines.append(f"Brier score: {cal['brier_score']:.4f} (цель &lt; 0.05)")
            lines.append(f"Avg edge (win): {roi.get('avg_edge_won', 0):+.1%}")
            lines.append(f"Avg edge (loss): {roi.get('avg_edge_lost', 0):+.1%}\n")

            if cal.get("buckets"):
                lines.append("<b>По вероятности:</b>")
                for bucket, data in sorted(cal["buckets"].items()):
                    wr = data["wins"] / data["total"] if data["total"] else 0
                    lines.append(f"  {bucket}: {data['wins']}/{data['total']} ({wr:.0%})")

            if cats:
                lines.append("\n<b>По темам:</b>")
                for cat, data in sorted(cats.items(), key=lambda x: -x[1]["total"]):
                    wr = data["wins"] / data["total"] if data["total"] else 0
                    lines.append(f"  {_escape(cat)}: {data['wins']}/{data['total']} ({wr:.0%}) P&L ${data['pnl']:+.2f}")

            await send("\n".join(lines), chat_id=chat_id)

        elif text == "/config":
            lines = [
                "⚙️ <b>Конфигурация</b>\n",
                f"Режим: {'DRY RUN' if config.DRY_RUN else 'LIVE'}",
                f"Бюджет: ${config.BUDGET} | Max bet: ${config.MAX_BET}",
                f"Kelly: {config.KELLY_FRACTION} | MIN_EDGE: {config.MIN_EDGE:.0%}",
                f"Категории: {', '.join(config.ACTIVE_CATEGORIES)}",
                f"Интервал: {config.POLL_INTERVAL}с | Top: {config.TOP_MARKETS}",
                f"Daily mode: {'ON' if config.DAILY_MODE else 'OFF'}",
                f"Max spread: {config.MAX_SPREAD}",
            ]
            await send("\n".join(lines), chat_id=chat_id)

        elif text in ("/help", "/start"):
            await send_with_buttons(
                "🤖 <b>Polymarket Bot</b>\n\nВыбери команду:",
                [
                    [{"text": "📊 Статистика", "callback_data": "/stats"}],
                    [{"text": "📋 Последние ставки", "callback_data": "/last"}],
                    [{"text": "🎯 Accuracy", "callback_data": "/accuracy"}],
                    [{"text": "⚙️ Конфигурация", "callback_data": "/config"}],
                ],
                chat_id=chat_id,
            )
        else:
            await send(f"Неизвестная команда. Напиши /help", chat_id=chat_id)


async def send_with_buttons(text: str, buttons: list, chat_id: str = "") -> bool:
    """Отправляет сообщение с inline кнопками."""
    if not _enabled():
        return False

    payload = {
        "chat_id": chat_id or config.TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "reply_markup": {"inline_keyboard": buttons},
    }

    try:
        status, _ = await asyncio.to_thread(_tg_post, "sendMessage", payload)
        return status == 200
    except Exception:
        return False


async def _answer_callback(callback_id: str) -> None:
    """Убирает 'часики' после нажатия кнопки."""
    try:
        await asyncio.to_thread(_tg_post, "answerCallbackQuery", {"callback_query_id": callback_id})
    except Exception:
        pass


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
        return

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
