import asyncio
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from datetime import datetime

import config
from polymarket_client import PolymarketClient
from news_monitor import NewsMonitor
from strategy import Strategy
from logger import log_decision, print_summary, _load_history


def ts():
    return datetime.now().strftime("%H:%M:%S")


async def run_cycle(poly: PolymarketClient, news: NewsMonitor, strategy: Strategy, daily_mode: bool = False):
    label = "daily" if daily_mode else "обычный"
    print(f"\n[{ts()}] Загружаем рынки ({label})...")
    try:
        markets = await poly.get_daily_markets() if daily_mode else await poly.get_markets()
    except Exception as e:
        print(f"[{ts()}] Ошибка загрузки рынков: {e}")
        return

    print(f"[{ts()}] Найдено {len(markets)} рынков для анализа")

    for market in markets:
        question = market["question"]
        end_date = (market.get("end_date") or "")[:10] or "?"
        print(f"\n[{ts()}] --- {question[:60]} | до {end_date}")

        # Шаг 1: новости
        try:
            articles, is_fresh = await news.get_news(question)
        except Exception as e:
            print(f"  [{ts()}] Ошибка NewsAPI: {e}")
            continue

        if not articles:
            print(f"  [{ts()}] Новостей нет -- пропуск")
            continue

        if not is_fresh:
            print(f"  [{ts()}] Новости в кэше -- Claude не зовём, пропуск")
            continue

        print(f"  [{ts()}] Новостей: {len(articles)} | анализируем...")

        # Шаг 2: движение цены за 1ч (Этап 12)
        price_change = await poly.get_price_change_1h(market["yes_token_id"])
        if abs(price_change) >= config.PRICE_CHANGE_THRESHOLD:
            direction = "+" if price_change > 0 else ""
            print(f"  [{ts()}] Цена движется: {direction}{price_change:.1%} за час")

        # Шаг 3: оценка Claude
        try:
            result = await strategy.evaluate(market, articles, price_change_1h=price_change)
        except Exception as e:
            print(f"  [{ts()}] Ошибка Claude: {e}")
            continue

        # Шаг 4: логирование
        log_decision(market, result, config.DRY_RUN)

        # Шаг 5: проверка спреда и ставка (Этап 13)
        if result:
            token_id = market["yes_token_id"] if result["side"] == "YES" else market["no_token_id"]
            spread = await poly.get_spread(token_id)
            spread_ok = spread <= config.MAX_SPREAD
            if not spread_ok:
                warn = "пропуск" if not config.DRY_RUN else "предупреждение (DRY RUN)"
                print(f"  [{ts()}] Спред {spread:.3f} > MAX_SPREAD {config.MAX_SPREAD} — {warn}")
                if not config.DRY_RUN:
                    continue
            try:
                await poly.place_bet(token_id, result["side"], result["bet_amount"])
            except Exception as e:
                print(f"  [{ts()}] Ошибка ставки: {e}")


async def main():
    daily_mode = config.DAILY_MODE
    interval = config.DAILY_POLL_INTERVAL if daily_mode else config.POLL_INTERVAL
    markets_mode = "DAILY" if daily_mode else "NORMAL"

    mode = "DRY RUN" if config.DRY_RUN else "LIVE ⚠️"
    print(f"[{ts()}] Бот запущен | Режим: {mode} | Бюджет: ${config.BUDGET}")
    print(f"[{ts()}] MIN_EDGE={config.MIN_EDGE:.0%} | KELLY={config.KELLY_FRACTION} | MAX_BET=${config.MAX_BET}")
    print(f"[{ts()}] Рынки: {markets_mode} | Интервал: {interval}с | Топ: {config.TOP_MARKETS}")

    poly = PolymarketClient()
    news = NewsMonitor()
    strategy = Strategy()

    async with poly, news:
        cycle = 0
        while True:
            cycle += 1
            print(f"\n{'='*60}")
            print(f"[{ts()}] ЦИКЛ #{cycle}")
            print(f"{'='*60}")

            await run_cycle(poly, news, strategy, daily_mode=daily_mode)

            history = _load_history()
            if history:
                print_summary(history)

            print(f"\n[{ts()}] Следующий цикл через {interval} сек...")
            await asyncio.sleep(interval)


if __name__ == "__main__":
    asyncio.run(main())
