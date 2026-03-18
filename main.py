import asyncio
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from datetime import datetime

import config
from core.database import init_db, load_bets
from core.polymarket_client import PolymarketClient
from core.news_monitor import NewsMonitor
from core.strategy import Strategy
from core.logger import log_decision, print_summary
from core.outcome_tracker import check_resolved_markets, print_calibration_report
from core import notifier


def ts():
    return datetime.now().strftime("%H:%M:%S")


async def _analyze_one(market: dict, news: NewsMonitor, strategy: Strategy, poly: PolymarketClient):
    """Анализирует один рынок: новости + цена + крипто сигнал + Claude.
    Возвращает (market, result, price_change) или (market, None, 0) при пропуске."""
    question = market["question"]
    try:
        articles, is_fresh = await news.get_news(question)
    except Exception as e:
        print(f"  [{ts()}] {question[:50]} — ошибка новостей: {e}")
        return market, None, 0.0

    if not articles:
        print(f"  [{ts()}] {question[:50]} — новостей нет, пропуск")
        return market, None, 0.0

    if not is_fresh:
        print(f"  [{ts()}] {question[:50]} — кэш, пропуск")
        return market, None, 0.0

    # Параллельно: цена за 1ч + крипто сигнал + fear & greed
    price_change, crypto_signal, fear_greed = await asyncio.gather(
        poly.get_price_change_1h(market["yes_token_id"]),
        poly.get_crypto_signal(question),
        poly.get_fear_greed(),
    )
    if fear_greed and crypto_signal:
        crypto_signal = f"{crypto_signal} | {fear_greed}"
    elif fear_greed:
        crypto_signal = fear_greed

    if abs(price_change) >= config.PRICE_CHANGE_THRESHOLD:
        direction = "+" if price_change > 0 else ""
        print(f"  [{ts()}] {question[:50]} — цена {direction}{price_change:.1%}/ч")
    if crypto_signal:
        print(f"  [{ts()}] {question[:50]} — {crypto_signal}")

    print(f"  [{ts()}] {question[:50]} — {len(articles)} новостей, анализируем...")
    try:
        result = await strategy.evaluate(market, articles, price_change_1h=price_change, crypto_signal=crypto_signal)
    except Exception as e:
        print(f"  [{ts()}] {question[:50]} — ошибка Claude: {e}")
        return market, None, 0.0

    return market, result, price_change


async def run_cycle(poly: PolymarketClient, news: NewsMonitor, strategy: Strategy, daily_mode: bool = False) -> int:
    """Возвращает количество сделанных ставок в цикле."""
    label = "daily" if daily_mode else "обычный"
    print(f"\n[{ts()}] Загружаем рынки ({label})...")
    try:
        markets = await poly.get_daily_markets() if daily_mode else await poly.get_markets()
    except Exception as e:
        print(f"[{ts()}] Ошибка загрузки рынков: {e}")
        await notifier.notify_error(f"Ошибка загрузки рынков: {e}")
        return 0

    print(f"[{ts()}] Найдено {len(markets)} рынков — запускаем параллельный анализ...")

    # Параллельный анализ всех рынков
    tasks = [_analyze_one(m, news, strategy, poly) for m in markets]
    analyses = await asyncio.gather(*tasks, return_exceptions=True)

    bets_placed = 0

    # Логирование и ставки — последовательно
    for item in analyses:
        if isinstance(item, Exception):
            print(f"[{ts()}] Неожиданная ошибка анализа: {item}")
            continue

        market, result, price_change = item
        end_date = (market.get("end_date") or "")[:10] or "?"
        print(f"\n[{ts()}] --- {market['question'][:60]} | до {end_date}")

        log_decision(market, result, config.DRY_RUN)

        if result:
            bets_placed += 1
            await notifier.notify_bet(market, result, config.DRY_RUN)

            token_id = market["yes_token_id"] if result["side"] == "YES" else market["no_token_id"]
            spread = await poly.get_spread(token_id)
            if spread > config.MAX_SPREAD:
                warn = "пропуск" if not config.DRY_RUN else "предупреждение (DRY RUN)"
                print(f"  [{ts()}] Спред {spread:.3f} > MAX_SPREAD {config.MAX_SPREAD} — {warn}")
                if not config.DRY_RUN:
                    continue
            try:
                await poly.place_bet(token_id, result["side"], result["bet_amount"])
            except Exception as e:
                print(f"  [{ts()}] Ошибка ставки: {e}")

    return bets_placed


async def main():
    init_db()
    daily_mode = config.DAILY_MODE
    interval = config.DAILY_POLL_INTERVAL if daily_mode else config.POLL_INTERVAL
    markets_mode = "DAILY" if daily_mode else "NORMAL"

    mode = "DRY RUN" if config.DRY_RUN else "LIVE ⚠️"
    print(f"[{ts()}] Бот запущен | Режим: {mode} | Бюджет: ${config.BUDGET}")
    print(f"[{ts()}] MIN_EDGE={config.MIN_EDGE:.0%} | KELLY={config.KELLY_FRACTION} | MAX_BET=${config.MAX_BET}")
    print(f"[{ts()}] Рынки: {markets_mode} | Интервал: {interval}с | Топ: {config.TOP_MARKETS}")
    print(f"[{ts()}] Категории: {', '.join(config.ACTIVE_CATEGORIES)}")
    tg_status = "включены" if config.TELEGRAM_BOT_TOKEN else "отключены"
    print(f"[{ts()}] Telegram: {tg_status}")

    await notifier.send(
        f"🚀 <b>Бот запущен</b>\n"
        f"Режим: {mode} | Рынки: {markets_mode}\n"
        f"Категории: {', '.join(config.ACTIVE_CATEGORIES)}"
    )

    poly = PolymarketClient()
    news = NewsMonitor()
    strategy = Strategy()

    try:
        async with poly, news:
            # Telegram команды — отдельная задача, работает ВСЕГДА (даже во время анализа)
            cmd_task = asyncio.create_task(_command_loop())
            _ = cmd_task  # prevent garbage collection

            cycle = 0
            while True:
                cycle += 1
                print(f"\n{'='*60}")
                print(f"[{ts()}] ЦИКЛ #{cycle}")
                print(f"{'='*60}")

                bets_placed = await run_cycle(poly, news, strategy, daily_mode=daily_mode)

                new_outcomes = await check_resolved_markets()
                if new_outcomes:
                    print(f"[{ts()}] Трекер: записано {new_outcomes} новых исходов")

                bets = load_bets()
                if bets:
                    print_summary(bets)

                print_calibration_report()

                await notifier.notify_cycle_summary(cycle, len(bets), bets_placed, new_outcomes)

                print(f"\n[{ts()}] Следующий цикл через {interval} сек...")
                await asyncio.sleep(interval)
    finally:
        await notifier.close()


async def _command_loop():
    """Фоновый цикл — проверяет Telegram команды каждые 3 сек, не зависит от анализа."""
    print(f"[{ts()}] Telegram command loop запущен")
    while True:
        try:
            await notifier.handle_commands()
        except Exception as e:
            print(f"  [TG] Ошибка обработки команд: {e}")
        await asyncio.sleep(3)


if __name__ == "__main__":
    asyncio.run(main())
