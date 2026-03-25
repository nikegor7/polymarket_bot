import asyncio
import io
import os
import sys
import time

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from datetime import datetime
from pathlib import Path


# ── Логирование в файл + консоль ──────────────────────────
LOG_FILE = Path(__file__).parent / "data" / "bot.log"


class _Tee:
    """Пишет одновременно в консоль и в лог-файл."""
    def __init__(self, original_stream, log_fh):
        self._original = original_stream
        self._log = log_fh

    def write(self, text):
        self._original.write(text)
        try:
            self._log.write(text)
            self._log.flush()
        except Exception:
            pass

    def flush(self):
        self._original.flush()
        try:
            self._log.flush()
        except Exception:
            pass

    def reconfigure(self, **kwargs):
        if hasattr(self._original, "reconfigure"):
            self._original.reconfigure(**kwargs)


def _setup_logging():
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    # Очищаем файл при каждом запуске (mode="w")
    log_fh = open(LOG_FILE, "w", encoding="utf-8", errors="replace")
    sys.stdout = _Tee(sys.stdout, log_fh)
    sys.stderr = _Tee(sys.stderr, log_fh)


_setup_logging()

import aiohttp
import config
from core.database import init_db, load_bets, count_bets, has_recent_bet, total_bet_amount_today, count_open_bets
from core.polymarket_client import PolymarketClient
from core.news_monitor import NewsMonitor
from core.strategy import Strategy
from core.logger import log_decision, print_summary
from core.outcome_tracker import check_resolved_markets, print_calibration_report
from core import notifier


def ts():
    return datetime.now().strftime("%H:%M:%S")


async def _analyze_one(market: dict, news: NewsMonitor, strategy: Strategy, poly: PolymarketClient,
                       orderbook: dict = None):
    """Анализирует один рынок: новости + цена + крипто сигнал + orderbook + Claude.
    orderbook — если передан, не запрашивается повторно (batch pre-fetch).
    Возвращает (market, result, price_signals) или (market, None, {}) при пропуске."""
    question = market["question"]
    condition_id = market.get("condition_id", "")

    # Дедупликация: не ставить на один рынок чаще чем раз в 24ч
    if condition_id and has_recent_bet(condition_id):
        print(f"  [{ts()}] {question[:50]} — уже ставили за 24ч, пропуск")
        return market, None, {}

    # Параллельно: новости + цены (мультитаймфрейм) + крипто + fear & greed + market signals + fee
    _t = int(os.getenv("API_TIMEOUT", "60"))
    news_task = asyncio.wait_for(news.get_news(question), timeout=_t)
    price_task = asyncio.wait_for(poly.get_price_signals(market["yes_token_id"]), timeout=_t)
    crypto_task = asyncio.wait_for(poly.get_crypto_signal(question), timeout=_t)
    fg_task = asyncio.wait_for(poly.get_fear_greed(), timeout=_t)
    msig_task = asyncio.wait_for(poly.get_market_signals(market), timeout=_t)
    fee_task = asyncio.wait_for(poly.get_fee_rate(market["yes_token_id"]), timeout=_t)

    results = await asyncio.gather(news_task, price_task, crypto_task, fg_task, msig_task, fee_task,
                                   return_exceptions=True)

    # Новости
    if isinstance(results[0], Exception):
        print(f"  [{ts()}] {question[:50]} — ошибка новостей: {results[0]}")
        articles, is_fresh = [], False
    else:
        articles, is_fresh = results[0]

    # Ценовые сигналы (мультитаймфрейм)
    price_signals = results[1] if not isinstance(results[1], Exception) else {}
    price_change = price_signals.get("change_1h", 0.0) if isinstance(price_signals, dict) else 0.0

    # Orderbook — уже получен через batch в run_cycle
    if orderbook is None:
        orderbook = {}

    # Market signals (OI, live volume, smart money)
    market_signals = results[4] if not isinstance(results[4], Exception) else {}

    # Fee rate из API
    fee_rate = results[5] if not isinstance(results[5], Exception) else 0.0

    # Pre-filter: если spread слишком большой — не тратим Claude
    ob_spread = orderbook.get("spread", 1.0) if isinstance(orderbook, dict) else 1.0
    if ob_spread > config.MAX_SPREAD and not config.DRY_RUN:
        print(f"  [{ts()}] {question[:50]} — spread {ob_spread:.3f} > MAX_SPREAD — пропуск (до Claude)")
        return market, None, price_signals

    # Крипто, fear & greed
    crypto_signal = results[2] if not isinstance(results[2], Exception) else ""
    fear_greed = results[3] if not isinstance(results[3], Exception) else ""

    # Fear & Greed только для крипто рынков (где есть CoinGecko сигнал)
    if fear_greed and crypto_signal:
        crypto_signal = f"{crypto_signal} | {fear_greed}"

    # Решаем, есть ли достаточно данных для анализа
    has_news = bool(articles)
    has_crypto = bool(crypto_signal)

    if not has_news and not has_crypto:
        print(f"  [{ts()}] {question[:50]} — нет данных (ни новостей, ни крипто), пропуск")
        return market, None, price_signals

    # Логирование сигналов
    signal_parts = []
    if abs(price_change) >= config.PRICE_CHANGE_THRESHOLD:
        signal_parts.append(f"1h:{price_change:+.1%}")
    ch24 = price_signals.get("change_24h", 0) if isinstance(price_signals, dict) else 0
    if abs(ch24) >= 0.03:
        signal_parts.append(f"24h:{ch24:+.1%}")
    if isinstance(orderbook, dict) and orderbook.get("reliable"):
        imb = orderbook.get("imbalance", 0.5)
        if abs(imb - 0.5) > 0.1:
            signal_parts.append(f"imb:{imb:.0%}")
    if isinstance(market_signals, dict):
        oi = market_signals.get("open_interest", 0)
        if oi > 0:
            signal_parts.append(f"OI:${oi:,.0f}")
        sm = market_signals.get("smart_money", {})
        if isinstance(sm, dict) and sm.get("reliable"):
            signal_parts.append(f"SM:{sm['bias']:.0%}YES")
    if crypto_signal:
        signal_parts.append(crypto_signal[:60])

    news_label = f"{len(articles)} новостей" + (" (кэш)" if not is_fresh else "")
    signals_str = " | ".join(signal_parts) if signal_parts else ""
    if signals_str:
        print(f"  [{ts()}] {question[:50]} — {signals_str}")
    print(f"  [{ts()}] {question[:50]} — {news_label}, анализируем...")

    try:
        result = await strategy.evaluate(
            market, articles,
            price_change_1h=price_change,
            crypto_signal=crypto_signal,
            price_signals=price_signals if isinstance(price_signals, dict) else None,
            orderbook=orderbook if isinstance(orderbook, dict) else None,
            market_signals=market_signals if isinstance(market_signals, dict) else None,
            fee_rate=fee_rate,
        )
    except Exception as e:
        print(f"  [{ts()}] {question[:50]} — ошибка Claude: {e}")
        return market, None, price_signals

    return market, result, price_signals


async def run_cycle(poly: PolymarketClient, news: NewsMonitor, strategy: Strategy, daily_mode: bool = False) -> int:
    """Возвращает количество сделанных ставок в цикле."""
    cycle_start = time.time()

    # Проверка лимитов перед циклом
    daily_total = total_bet_amount_today()
    if daily_total >= config.DAILY_BET_LIMIT:
        print(f"[{ts()}] Дневной лимит: ${daily_total:.2f} >= ${config.DAILY_BET_LIMIT} — пропуск цикла")
        return 0
    open_count = count_open_bets()
    if open_count >= config.MAX_OPEN_BETS:
        print(f"[{ts()}] Макс открытых ставок: {open_count} >= {config.MAX_OPEN_BETS} — пропуск цикла")
        return 0

    label = "daily" if daily_mode else "обычный"
    print(f"\n[{ts()}] Загружаем рынки ({label})...")
    try:
        markets = await poly.get_daily_markets() if daily_mode else await poly.get_markets()
    except Exception as e:
        print(f"[{ts()}] Ошибка загрузки рынков: {type(e).__name__}: {e}")
        await notifier.notify_error(f"Ошибка загрузки рынков: {type(e).__name__}: {e}")
        return 0

    tf_counts = {}
    for m in markets:
        tf = m.get("timeframe", "daily")
        tf_counts[tf] = tf_counts.get(tf, 0) + 1
    tf_str = ", ".join(f"{k}: {v}" for k, v in sorted(tf_counts.items()))
    print(f"[{ts()}] Найдено {len(markets)} рынков ({tf_str})")

    # Batch orderbook: 1 запрос POST /books вместо 10 GET /book
    token_ids = [m["yes_token_id"] for m in markets if m.get("yes_token_id")]
    print(f"[{ts()}] Batch orderbooks ({len(token_ids)} токенов)...")
    try:
        batch_obs = await asyncio.wait_for(poly.get_batch_orderbooks(token_ids), timeout=int(os.getenv("API_TIMEOUT", "60")))
    except Exception as e:
        print(f"[{ts()}] Batch orderbook ошибка: {e}, будут индивидуальные")
        batch_obs = {}

    print(f"[{ts()}] Запускаем параллельный анализ...")

    # Параллельный анализ всех рынков (orderbook уже получен batch'ем)
    tasks = [
        _analyze_one(m, news, strategy, poly,
                     orderbook=batch_obs.get(m.get("yes_token_id", ""), None))
        for m in markets
    ]
    analyses = await asyncio.gather(*tasks, return_exceptions=True)

    bets_placed = 0

    # Логирование и ставки — последовательно
    for item in analyses:
        if isinstance(item, Exception):
            print(f"[{ts()}] Неожиданная ошибка анализа: {item}")
            continue

        market, result, price_signals = item
        end_date = (market.get("end_date") or "")[:10] or "?"
        timeframe = market.get("timeframe", "daily")
        print(f"\n[{ts()}] --- [{timeframe}] {market['question'][:60]} | до {end_date}")

        log_decision(market, result, config.DRY_RUN)

        if result:
            bets_placed += 1
            await notifier.notify_bet(market, result, config.DRY_RUN)

            token_id = market["yes_token_id"] if result["side"] == "YES" else market["no_token_id"]
            # Spread уже проверен в _analyze_one (pre-filter), но повторно для LIVE
            if not config.DRY_RUN:
                spread = await poly.get_spread(token_id)
                if spread > config.MAX_SPREAD:
                    print(f"  [{ts()}] Спред {spread:.3f} > MAX_SPREAD {config.MAX_SPREAD} — пропуск ставки")
                    continue
            try:
                await poly.place_bet(token_id, result["side"], result["bet_amount"])
            except Exception as e:
                print(f"  [{ts()}] Ошибка ставки: {e}")

    cycle_time = time.time() - cycle_start
    # Rate limit stats
    from core.polymarket_client import _rate
    rate_stats = _rate.stats()
    rate_str = " | ".join(f"{k}:{v}" for k, v in rate_stats.items()) if rate_stats else "idle"
    print(f"\n[{ts()}] Цикл завершён за {cycle_time:.1f}с | Ставок: {bets_placed} | API: {rate_str}")
    if cycle_time > config.POLL_INTERVAL * 0.8:
        print(f"  [WARNING] Цикл занял {cycle_time:.0f}с — близко к интервалу {config.POLL_INTERVAL}с!")

    return bets_placed


async def _check_geoblock():
    """Проверка гео-ограничений при запуске."""
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=int(os.getenv("API_TIMEOUT", "60")))) as session:
            async with session.get("https://polymarket.com/api/geoblock") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("blocked"):
                        country = data.get("country", "?")
                        print(f"[{ts()}] ⚠️  GEO-BLOCK: IP заблокирован (страна: {country})")
                        print(f"[{ts()}]    Polymarket недоступен из вашего региона. Используйте VPN.")
                        return False
                    else:
                        print(f"[{ts()}] Гео-проверка: OK ({data.get('country', '?')})")
    except Exception:
        print(f"[{ts()}] Гео-проверка: не удалось (продолжаем)")
    return True


async def main():
    init_db()
    daily_mode = config.DAILY_MODE
    interval = config.DAILY_POLL_INTERVAL if daily_mode else config.POLL_INTERVAL
    markets_mode = "DAILY" if daily_mode else "NORMAL"

    mode = "DRY RUN" if config.DRY_RUN else "LIVE ⚠️"
    print(f"[{ts()}] Бот запущен | Режим: {mode} | Бюджет: ${config.BUDGET}")
    print(f"[{ts()}] MIN_EDGE={config.MIN_EDGE:.0%} (short={config.MIN_EDGE_SHORT:.0%}) | KELLY={config.KELLY_FRACTION} | MAX_BET=${config.MAX_BET}")
    print(f"[{ts()}] Рынки: {markets_mode} | Интервал: {interval}с | Топ: {config.TOP_MARKETS}")
    print(f"[{ts()}] Категории: {', '.join(config.ACTIVE_CATEGORIES)}")
    tg_status = "включены" if config.TELEGRAM_BOT_TOKEN else "отключены"
    print(f"[{ts()}] Telegram: {tg_status}")

    # Гео-проверка
    await _check_geoblock()

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

                bets = load_bets(limit=50)
                if bets:
                    print_summary(bets, total_count=count_bets())

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
