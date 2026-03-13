import asyncio
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from datetime import datetime

import config
from polymarket_client import PolymarketClient
from news_monitor import NewsMonitor
from strategy import Strategy
from logger import log_decision, print_summary, _load_history


def ts():
    return datetime.now().strftime("%H:%M:%S")


async def run_cycle(poly: PolymarketClient, news: NewsMonitor, strategy: Strategy):
    print(f"\n[{ts()}] Загружаем рынки...")
    try:
        markets = await poly.get_markets()
    except Exception as e:
        print(f"[{ts()}] Ошибка загрузки рынков: {e}")
        return

    print(f"[{ts()}] Найдено {len(markets)} рынков для анализа")

    for market in markets:
        question = market["question"]
        print(f"\n[{ts()}] --- {question[:70]}")

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

        # Шаг 2: оценка Claude
        try:
            result = await strategy.evaluate(market, articles)
        except Exception as e:
            print(f"  [{ts()}] Ошибка Claude: {e}")
            continue

        # Шаг 3: логирование
        log_decision(market, result, config.DRY_RUN)

        # Шаг 4: ставка
        if result:
            token_id = market["yes_token_id"] if result["side"] == "YES" else market["no_token_id"]
            try:
                await poly.place_bet(token_id, result["side"], result["bet_amount"])
            except Exception as e:
                print(f"  [{ts()}] Ошибка ставки: {e}")


async def main():
    mode = "DRY RUN" if config.DRY_RUN else "LIVE ⚠️"
    print(f"[{ts()}] Бот запущен | Режим: {mode} | Бюджет: ${config.BUDGET}")
    print(f"[{ts()}] MIN_EDGE={config.MIN_EDGE:.0%} | KELLY={config.KELLY_FRACTION} | MAX_BET=${config.MAX_BET}")
    print(f"[{ts()}] Интервал: {config.POLL_INTERVAL}с | Топ рынков: {config.TOP_MARKETS}")

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

            await run_cycle(poly, news, strategy)

            history = _load_history()
            if history:
                print_summary(history)

            print(f"\n[{ts()}] Следующий цикл через {config.POLL_INTERVAL} сек...")
            await asyncio.sleep(config.POLL_INTERVAL)


if __name__ == "__main__":
    asyncio.run(main())
