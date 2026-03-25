from __future__ import annotations

import asyncio
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone

import aiohttp
import config

GAMMA_BASE = "https://gamma-api.polymarket.com"
CLOB_BASE = "https://clob.polymarket.com"
DATA_BASE = "https://data-api.polymarket.com"
COINGECKO_BASE = "https://api.coingecko.com/api/v3"
MARKETS_CACHE_TTL = 900   # 15 минут
DAILY_CACHE_TTL = 120     # 2 минуты
CRYPTO_CACHE_TTL = 300    # 5 минут — CoinGecko
FG_CACHE_TTL = 3600       # 1 час — Fear & Greed (обновляется раз в день)
DATA_CACHE_TTL = 600      # 10 минут — OI, holders, live volume (медленно меняются)
FEE_CACHE_TTL = 1800      # 30 минут — комиссии меняются очень редко

FEAR_GREED_URL = "https://api.alternative.me/fng/"

# HTTP таймаут для всех запросов (VPN может добавлять 20-30с латентности)
_TIMEOUT = aiohttp.ClientTimeout(total=int(os.getenv("API_TIMEOUT", "60")))

# Retry конфигурация
_MAX_RETRIES = 3
_RETRY_BACKOFF = [2, 5, 10]  # секунды между попытками


# ── Rate Limit Budget ─────────────────────────────────────
# Polymarket rate limits (per 10 seconds window):
#   Gamma /markets: 300, Data API: 1000, CLOB /book: 1500, CLOB /books: 500
# Cloudflare НЕ шлёт 429 — просто тормозит. Поэтому считаем сами.

class _RateBudget:
    """Скользящее окно запросов за 10 секунд по категориям API."""

    _LIMITS = {
        "gamma_markets": 250,   # из 300 — оставляем запас
        "clob_book": 1200,      # из 1500
        "clob_books": 400,      # из 500
        "data_api": 800,        # из 1000
    }

    def __init__(self):
        self._windows: dict[str, list[float]] = {}

    def _cleanup(self, bucket: str) -> None:
        now = time.time()
        if bucket in self._windows:
            self._windows[bucket] = [t for t in self._windows[bucket] if now - t < 10]

    def can_request(self, bucket: str) -> bool:
        self._cleanup(bucket)
        limit = self._LIMITS.get(bucket, 9999)
        return len(self._windows.get(bucket, [])) < limit

    def record(self, bucket: str) -> None:
        self._windows.setdefault(bucket, []).append(time.time())

    async def wait_if_needed(self, bucket: str) -> None:
        """Если бюджет исчерпан, ждём до освобождения слота."""
        while not self.can_request(bucket):
            self._cleanup(bucket)
            if self._windows.get(bucket):
                oldest = self._windows[bucket][0]
                wait = 10 - (time.time() - oldest) + 0.1
                if wait > 0:
                    print(f"  [Rate] {bucket}: бюджет исчерпан, жду {wait:.1f}с")
                    await asyncio.sleep(wait)
            else:
                break

    def stats(self) -> dict[str, str]:
        """Текущее использование для логирования."""
        result = {}
        for bucket, limit in self._LIMITS.items():
            self._cleanup(bucket)
            used = len(self._windows.get(bucket, []))
            if used > 0:
                result[bucket] = f"{used}/{limit}"
        return result


_rate = _RateBudget()


async def _request_with_retry(session: aiohttp.ClientSession, method: str, url: str,
                               retries: int = _MAX_RETRIES, rate_bucket: str = "",
                               **kwargs) -> aiohttp.ClientResponse:
    """HTTP запрос с retry, exponential backoff и rate limit budget."""
    if rate_bucket:
        await _rate.wait_if_needed(rate_bucket)

    last_exc = None
    for attempt in range(retries):
        try:
            if rate_bucket:
                _rate.record(rate_bucket)
            resp = await session.request(method, url, **kwargs)
            # 425 = matching engine restart (вторник 7 AM ET), retry
            if resp.status == 425:
                delay = _RETRY_BACKOFF[min(attempt, len(_RETRY_BACKOFF) - 1)]
                print(f"  [HTTP] 425 (engine restart) {url.split('/')[-1]} — retry {attempt+1}/{retries} через {delay}с")
                await asyncio.sleep(delay)
                continue
            # 5xx = серверная ошибка, retry
            if resp.status >= 500:
                delay = _RETRY_BACKOFF[min(attempt, len(_RETRY_BACKOFF) - 1)]
                print(f"  [HTTP] {resp.status} {url.split('/')[-1]} — retry {attempt+1}/{retries} через {delay}с")
                await asyncio.sleep(delay)
                continue
            return resp
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            last_exc = e
            delay = _RETRY_BACKOFF[min(attempt, len(_RETRY_BACKOFF) - 1)]
            if attempt < retries - 1:
                print(f"  [HTTP] {type(e).__name__} {url.split('/')[-1]} — retry {attempt+1}/{retries} через {delay}с")
                await asyncio.sleep(delay)
    raise last_exc or aiohttp.ClientError(f"All {retries} retries failed for {url}")

_COIN_MAP = {
    "bitcoin": "bitcoin", "btc": "bitcoin",
    "ethereum": "ethereum", "eth": "ethereum",
    "solana": "solana", "sol": "solana",
    "binance": "binancecoin", "bnb": "binancecoin",
    "coinbase": "coinbase-exchange-token",
    "xrp": "ripple",
    "cardano": "cardano", "ada": "cardano",
    "dogecoin": "dogecoin", "doge": "dogecoin",
    "polygon": "matic-network", "matic": "matic-network",
    "avalanche": "avalanche-2", "avax": "avalanche-2",
    "chainlink": "chainlink", "link": "chainlink",
    "polkadot": "polkadot", "dot": "polkadot",
    "litecoin": "litecoin", "ltc": "litecoin",
    "uniswap": "uniswap", "uni": "uniswap",
    "toncoin": "the-open-network", "ton": "the-open-network",
    "pepe": "pepe",
}


class PolymarketClient:
    def __init__(self):
        self.session: aiohttp.ClientSession | None = None
        self._markets_cache: list = []
        self._markets_fetched_at: float = 0
        self._daily_cache: list = []
        self._daily_fetched_at: float = 0
        # Кеши для CoinGecko и Fear & Greed
        self._crypto_cache: dict[str, tuple[str, float]] = {}  # coins_key -> (result, timestamp)
        self._fg_cache: tuple[str, float] = ("", 0)
        # Кеши для Data API (OI, holders, live volume) — key -> (result, timestamp)
        self._data_cache: dict[str, tuple] = {}
        # Кеш для fee-rate — token_id -> (fee_bps, timestamp)
        self._fee_cache: dict[str, tuple[float, float]] = {}

    async def __aenter__(self):
        self.session = aiohttp.ClientSession(timeout=_TIMEOUT)
        return self

    async def __aexit__(self, *args):
        await self.session.close()

    @staticmethod
    def _classify_timeframe(end_date_iso: str) -> str:
        """Определяет таймфрейм рынка по времени до закрытия.

        Polymarket endDateIso часто приходит без времени ('2026-03-20').
        Реальное закрытие daily рынков — 16:00 UTC (12 PM ET).
        """
        if not end_date_iso:
            return "daily"
        try:
            if "T" in end_date_iso:
                end_dt = datetime.fromisoformat(end_date_iso.replace("Z", "+00:00"))
            else:
                # Только дата — подставляем 16:00 UTC (реальное время закрытия daily)
                end_dt = datetime.fromisoformat(end_date_iso + "T16:00:00+00:00")
            now = datetime.now(timezone.utc)
            hours_left = (end_dt - now).total_seconds() / 3600
            if hours_left <= 1.5:
                return "1ч"
            elif hours_left <= 5:
                return "4ч"
            else:
                return "daily"
        except (ValueError, TypeError):
            return "daily"

    def _parse_market(self, m: dict) -> dict | None:
        if not m.get("acceptingOrders"):
            return None

        try:
            volume = float(m.get("volumeNum") or 0)
            volume_24hr = float(m.get("volume24hr") or 0)
            liquidity = float(m.get("liquidityNum") or 0)
        except (TypeError, ValueError):
            return None

        if volume_24hr < config.MIN_MARKET_VOLUME:
            return None

        if volume < config.MIN_MARKET_VOLUME_TOTAL:
            return None

        try:
            prices = json.loads(m.get("outcomePrices", "[]"))
            outcomes = json.loads(m.get("outcomes", "[]"))
        except (json.JSONDecodeError, TypeError):
            return None

        if len(prices) != 2 or len(outcomes) != 2:
            return None

        yes_idx = next((i for i, o in enumerate(outcomes) if o.lower() == "yes"), None)
        no_idx = next((i for i, o in enumerate(outcomes) if o.lower() == "no"), None)

        if yes_idx is None or no_idx is None:
            return None

        yes_price = float(prices[yes_idx])
        no_price = float(prices[no_idx])

        if yes_price == 0 or no_price == 0:
            return None

        # Фильтр экстремальных цен — tail events и уже решённые рынки
        if yes_price < config.MIN_YES_PRICE or yes_price > config.MAX_YES_PRICE:
            return None

        # Валидация condition_id
        condition_id = m.get("conditionId") or ""
        if len(condition_id) < 10:
            return None

        question_lower = (m.get("question") or "").lower()
        if not any(re.search(r'\b' + re.escape(topic) + r'\b', question_lower) for topic in config.ALLOWED_TOPICS):
            return None

        clob_token_ids = m.get("clobTokenIds") or []
        yes_token_id = clob_token_ids[yes_idx] if len(clob_token_ids) > yes_idx else ""
        no_token_id = clob_token_ids[no_idx] if len(clob_token_ids) > no_idx else ""

        end_date_iso = m.get("endDateIso") or ""
        timeframe = self._classify_timeframe(end_date_iso)

        return {
            "condition_id": condition_id,
            "question": m.get("question"),
            "volume": volume,
            "volume_24hr": volume_24hr,
            "liquidity": liquidity,
            "yes_price": yes_price,
            "no_price": no_price,
            "yes_token_id": yes_token_id,
            "no_token_id": no_token_id,
            "end_date": end_date_iso,
            "timeframe": timeframe,
            "spread": float(m.get("spread", 0) or 0),
            "best_bid": float(m.get("bestBid", 0) or 0),
            "best_ask": float(m.get("bestAsk", 0) or 0),
            "tick_size": str(m.get("minimum_tick_size", "0.01") or "0.01"),
            "neg_risk": bool(m.get("neg_risk", False)),
            "event_id": m.get("eventId") or m.get("event_id") or "",
        }

    async def get_markets(self, limit: int = config.TOP_MARKETS) -> list[dict]:
        if self._markets_cache and (time.time() - self._markets_fetched_at) < MARKETS_CACHE_TTL:
            return self._markets_cache

        params = {
            "active": "true",
            "closed": "false",
            "limit": 100,
            "_sort": "volume24hr",
            "_order": "DESC",
            # Серверная фильтрация — не тащим мусор
            "volume_num_min": config.MIN_MARKET_VOLUME_TOTAL,
        }

        if config.MAX_DAYS_TO_CLOSE > 0:
            end_max = (datetime.now(timezone.utc) + timedelta(days=config.MAX_DAYS_TO_CLOSE)).strftime("%Y-%m-%dT23:59:59Z")
            params["end_date_max"] = end_max

        resp = await _request_with_retry(self.session, "GET", f"{GAMMA_BASE}/markets",
                                          params=params, rate_bucket="gamma_markets")
        resp.raise_for_status()
        data = await resp.json()

        markets = [m for raw in data if (m := self._parse_market(raw)) is not None]
        markets.sort(key=lambda x: x["volume_24hr"], reverse=True)
        self._markets_cache = markets[:limit]
        self._markets_fetched_at = time.time()
        return self._markets_cache

    async def get_daily_markets(self, limit: int = config.TOP_MARKETS) -> list[dict]:
        """Рынки, закрывающиеся сегодня/завтра в 16:00 UTC (12 PM ET).

        Если daily рынков нет — fallback на обычные рынки (get_markets).
        """
        if self._daily_cache and (time.time() - self._daily_fetched_at) < DAILY_CACHE_TTL:
            return self._daily_cache

        now = datetime.now(timezone.utc)

        # Дедлайн daily рынков = 16:00 UTC (12 PM ET)
        today_deadline = now.replace(hour=16, minute=0, second=0, microsecond=0)
        if now >= today_deadline:
            # Уже после 16:00 — ищем завтрашние
            target_deadline = today_deadline + timedelta(days=1)
        else:
            target_deadline = today_deadline

        # Окно: от NOW до дедлайна + 1 час запас
        end_min = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        end_max = (target_deadline + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")

        params = {
            "active": "true",
            "closed": "false",
            "end_date_min": end_min,
            "end_date_max": end_max,
            "limit": 500,
            "_sort": "volume24hr",
            "_order": "DESC",
        }

        resp = await _request_with_retry(self.session, "GET", f"{GAMMA_BASE}/markets",
                                          params=params, rate_bucket="gamma_markets")
        resp.raise_for_status()
        data = await resp.json()

        markets = [m for raw in data if (m := self._parse_market(raw)) is not None]

        if not markets:
            # Fallback: API может не поддерживать end_date фильтры —
            # загружаем все активные и фильтруем клиентски по endDateIso
            print(f"  [{datetime.now().strftime('%H:%M:%S')}] Daily: API вернул 0, пробуем fallback...")
            fallback_params = {
                "active": "true",
                "closed": "false",
                "limit": 100,
                "_sort": "volume24hr",
                "_order": "DESC",
                "volume_num_min": config.MIN_MARKET_VOLUME_TOTAL,
            }
            resp = await _request_with_retry(self.session, "GET", f"{GAMMA_BASE}/markets",
                                              params=fallback_params, rate_bucket="gamma_markets")
            resp.raise_for_status()
            data = await resp.json()

            all_markets = [m for raw in data if (m := self._parse_market(raw)) is not None]
            # Фильтруем: end_date <= target_deadline + 1 час
            deadline_str = (target_deadline + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
            markets = [m for m in all_markets if m["end_date"] and m["end_date"] <= deadline_str]

            if not markets:
                # Совсем нет daily — берём обычные топ рынки
                print(f"  [{datetime.now().strftime('%H:%M:%S')}] Daily рынков нет, fallback на обычные рынки")
                self._daily_cache = all_markets[:limit]
                self._daily_fetched_at = time.time()
                return self._daily_cache

        # Ближайшие к закрытию — в топе
        markets.sort(key=lambda x: x["end_date"] or "9999")
        self._daily_cache = markets[:limit]
        self._daily_fetched_at = time.time()
        return self._daily_cache

    async def get_price_signals(self, token_id: str) -> dict:
        """Многотаймфреймовый анализ цены.
        Возвращает {change_1h, change_24h, change_7d, volatility_24h, reliable}.
        """
        empty = {"change_1h": 0.0, "change_24h": 0.0, "change_7d": 0.0, "volatility_24h": 0.0, "reliable": False}
        if not token_id:
            return empty
        now = int(time.time())

        async def _fetch_history(start_ts: int, end_ts: int, fidelity: int = 1) -> list:
            try:
                params = {"market": token_id, "startTs": start_ts, "endTs": end_ts, "fidelity": fidelity}
                resp = await _request_with_retry(self.session, "GET", f"{CLOB_BASE}/prices-history",
                                                  params=params, retries=2, rate_bucket="clob_book")
                if resp.status != 200:
                    return []
                data = await resp.json()
                return data.get("history", [])
            except Exception:
                return []

        # Параллельно: 1h (fidelity=1), 24h (fidelity=15), 7d (fidelity=60)
        h1_task = _fetch_history(now - 3600, now, fidelity=1)
        h24_task = _fetch_history(now - 86400, now, fidelity=15)
        h7d_task = _fetch_history(now - 604800, now, fidelity=60)

        h1, h24, h7d = await asyncio.gather(h1_task, h24_task, h7d_task, return_exceptions=True)

        result = dict(empty)

        # 1h change
        if isinstance(h1, list) and len(h1) >= 2:
            first, last = float(h1[0].get("p", 0)), float(h1[-1].get("p", 0))
            result["change_1h"] = round(last - first, 4)
            result["reliable"] = True

        # 24h change
        if isinstance(h24, list) and len(h24) >= 2:
            first, last = float(h24[0].get("p", 0)), float(h24[-1].get("p", 0))
            result["change_24h"] = round(last - first, 4)
            result["reliable"] = True

            # Волатильность (std dev цен за 24h)
            prices = [float(p.get("p", 0)) for p in h24 if p.get("p")]
            if len(prices) >= 5:
                mean = sum(prices) / len(prices)
                variance = sum((p - mean) ** 2 for p in prices) / len(prices)
                result["volatility_24h"] = round(variance ** 0.5, 4)

        # 7d change
        if isinstance(h7d, list) and len(h7d) >= 2:
            first, last = float(h7d[0].get("p", 0)), float(h7d[-1].get("p", 0))
            result["change_7d"] = round(last - first, 4)

        return result

    async def get_price_change_1h(self, token_id: str) -> float:
        """Обратная совместимость — возвращает только 1h change."""
        signals = await self.get_price_signals(token_id)
        return signals["change_1h"]

    @staticmethod
    def _analyze_book_data(bids: list, asks: list) -> dict:
        """Анализ книги ордеров из raw bid/ask данных.
        Общая логика для single и batch запросов."""
        empty = {"spread": 1.0, "vwap": 0.0, "bid_volume": 0.0, "ask_volume": 0.0,
                 "imbalance": 0.5, "depth_bid": 0.0, "depth_ask": 0.0, "reliable": False}
        if not bids or not asks:
            return empty

        best_bid = max(float(b["price"]) for b in bids)
        best_ask = min(float(a["price"]) for a in asks)
        spread = round(best_ask - best_bid, 4)

        # Volume-weighted midprice (top 5 levels)
        top_bids = sorted(bids, key=lambda b: float(b["price"]), reverse=True)[:5]
        top_asks = sorted(asks, key=lambda a: float(a["price"]))[:5]

        bid_vol = sum(float(b["size"]) for b in top_bids)
        ask_vol = sum(float(a["size"]) for a in top_asks)

        if bid_vol + ask_vol > 0:
            vwap = (best_bid * ask_vol + best_ask * bid_vol) / (bid_vol + ask_vol)
        else:
            vwap = (best_bid + best_ask) / 2

        total_vol = bid_vol + ask_vol
        imbalance = bid_vol / total_vol if total_vol > 0 else 0.5

        mid = (best_bid + best_ask) / 2
        depth_bid = sum(float(b["size"]) for b in bids if float(b["price"]) >= mid - 0.05)
        depth_ask = sum(float(a["size"]) for a in asks if float(a["price"]) <= mid + 0.05)

        return {
            "spread": spread,
            "vwap": round(vwap, 4),
            "bid_volume": round(bid_vol, 2),
            "ask_volume": round(ask_vol, 2),
            "imbalance": round(imbalance, 3),
            "depth_bid": round(depth_bid, 2),
            "depth_ask": round(depth_ask, 2),
            "reliable": True,
        }

    async def get_orderbook_analysis(self, token_id: str) -> dict:
        """Глубокий анализ книги ордеров (single token).
        Для batch используй get_batch_orderbooks()."""
        empty = {"spread": 1.0, "vwap": 0.0, "bid_volume": 0.0, "ask_volume": 0.0,
                 "imbalance": 0.5, "depth_bid": 0.0, "depth_ask": 0.0, "reliable": False}
        if not token_id:
            return empty
        try:
            resp = await _request_with_retry(self.session, "GET", f"{CLOB_BASE}/book",
                                              params={"token_id": token_id}, retries=2,
                                              rate_bucket="clob_book")
            if resp.status != 200:
                print(f"  [Orderbook] HTTP {resp.status} для {token_id[:10]}...")
                return empty
            data = await resp.json()
            return self._analyze_book_data(data.get("bids", []), data.get("asks", []))
        except Exception as e:
            print(f"  [Orderbook] Ошибка {token_id[:10]}: {type(e).__name__}: {e}")
            return empty

    async def get_batch_orderbooks(self, token_ids: list[str]) -> dict[str, dict]:
        """Batch анализ книг ордеров через POST /books (1 запрос вместо N).
        Возвращает {token_id: orderbook_analysis_dict}."""
        empty = {"spread": 1.0, "vwap": 0.0, "bid_volume": 0.0, "ask_volume": 0.0,
                 "imbalance": 0.5, "depth_bid": 0.0, "depth_ask": 0.0, "reliable": False}
        valid_ids = [tid for tid in token_ids if tid]
        if not valid_ids:
            return {}

        try:
            body = [{"token_id": tid} for tid in valid_ids]
            resp = await _request_with_retry(self.session, "POST", f"{CLOB_BASE}/books",
                                              json=body, retries=2, rate_bucket="clob_books")
            if resp.status != 200:
                # Fallback: индивидуальные запросы
                print(f"  [Batch OB] HTTP {resp.status}, fallback на индивидуальные")
                results = {}
                for tid in valid_ids:
                    results[tid] = await self.get_orderbook_analysis(tid)
                return results

            books = await resp.json()
            results = {}
            if isinstance(books, list):
                for i, book in enumerate(books):
                    tid = valid_ids[i] if i < len(valid_ids) else ""
                    if not tid:
                        continue
                    results[tid] = self._analyze_book_data(
                        book.get("bids", []), book.get("asks", []))
            elif isinstance(books, dict):
                # Ответ может быть dict с token_id как ключами
                for tid in valid_ids:
                    book = books.get(tid, {})
                    results[tid] = self._analyze_book_data(
                        book.get("bids", []), book.get("asks", []))

            # Заполняем пропуски
            for tid in valid_ids:
                if tid not in results:
                    results[tid] = dict(empty)
            return results

        except Exception as e:
            print(f"  [Batch OB] Ошибка: {type(e).__name__}: {e}, fallback")
            results = {}
            for tid in valid_ids:
                results[tid] = await self.get_orderbook_analysis(tid)
            return results

    async def get_spread(self, token_id: str) -> float:
        """Обратная совместимость — возвращает только spread."""
        ob = await self.get_orderbook_analysis(token_id)
        return ob["spread"]

    async def get_fee_rate(self, token_id: str) -> float:
        """Реальная комиссия из API (basis points → доля). Кеш 30 мин."""
        if not token_id:
            return 0.0
        cached = self._fee_cache.get(token_id)
        if cached and (time.time() - cached[1]) < FEE_CACHE_TTL:
            return cached[0]
        try:
            resp = await _request_with_retry(
                self.session, "GET", f"{CLOB_BASE}/fee-rate",
                params={"token_id": token_id}, retries=2, rate_bucket="clob_book",
            )
            if resp.status != 200:
                return 0.0
            data = await resp.json()
            # base_fee в basis points, конвертируем в долю (100 bps = 1%)
            fee_bps = float(data.get("base_fee", 0) or 0)
            fee_rate = fee_bps / 10000
            self._fee_cache[token_id] = (fee_rate, time.time())
            return fee_rate
        except Exception:
            return 0.0

    async def get_crypto_signal(self, question: str) -> str:
        """Возвращает строку с ценой и 24h изменением для крипто-монет из вопроса.
        Пример: 'BTC: $85,000 (+3.2% 24h), ETH: $3,200 (-1.1% 24h)'
        Кеширует на 5 мин. Если монет нет или ошибка — пустая строка."""
        q = question.lower()
        # Word boundary matching — "ton" не найдётся в "Washington"
        coins = list(dict.fromkeys(
            cid for kw, cid in _COIN_MAP.items()
            if re.search(r'\b' + re.escape(kw) + r'\b', q)
        ))
        if not coins:
            return ""

        # Кеш на 5 минут
        cache_key = ",".join(sorted(coins))
        cached = self._crypto_cache.get(cache_key)
        if cached and (time.time() - cached[1]) < CRYPTO_CACHE_TTL:
            return cached[0]

        try:
            resp = await _request_with_retry(
                self.session, "GET", f"{COINGECKO_BASE}/simple/price",
                params={"ids": ",".join(coins), "vs_currencies": "usd", "include_24hr_change": "true"},
                retries=2,
            )
            if resp.status != 200:
                return ""
            data = await resp.json()
        except Exception:
            return ""

        parts = []
        for coin in coins:
            info = data.get(coin, {})
            price = info.get("usd")
            change = info.get("usd_24h_change")
            if price:
                name = coin.upper()
                change_str = f" ({change:+.1f}% 24h)" if change is not None else ""
                parts.append(f"{name}: ${price:,.0f}{change_str}")

        result = ", ".join(parts)
        self._crypto_cache[cache_key] = (result, time.time())
        return result

    async def get_fear_greed(self) -> str:
        """Crypto Fear & Greed Index. Кеш 1 час (обновляется раз в день)."""
        # Кеш на 1 час
        cached_val, cached_at = self._fg_cache
        if cached_val and (time.time() - cached_at) < FG_CACHE_TTL:
            return cached_val

        try:
            resp = await _request_with_retry(self.session, "GET", FEAR_GREED_URL,
                                              params={"limit": 1}, retries=2)
            if resp.status != 200:
                return ""
            data = await resp.json()
            entry = data.get("data", [{}])[0]
            value = entry.get("value", "")
            label = entry.get("value_classification", "")
            if value:
                result = f"Fear & Greed Index: {value} ({label})"
                self._fg_cache = (result, time.time())
                return result
            return ""
        except Exception:
            return ""

    def _data_cached(self, key: str):
        """Возвращает кешированное значение или None если протухло."""
        cached = self._data_cache.get(key)
        if cached and (time.time() - cached[1]) < DATA_CACHE_TTL:
            return cached[0]
        return None

    def _data_store(self, key: str, value) -> None:
        self._data_cache[key] = (value, time.time())

    async def get_open_interest(self, condition_id: str) -> float:
        """Open Interest для рынка (Data API). Кеш 10 мин."""
        if not condition_id:
            return 0.0
        cache_key = f"oi:{condition_id}"
        cached = self._data_cached(cache_key)
        if cached is not None:
            return cached
        try:
            resp = await _request_with_retry(
                self.session, "GET", f"{DATA_BASE}/oi",
                params={"market[]": condition_id}, retries=2, rate_bucket="data_api",
            )
            if resp.status != 200:
                return 0.0
            data = await resp.json()
            if isinstance(data, list):
                result = sum(float(item.get("openInterest", 0) or 0) for item in data)
            else:
                result = float(data.get("openInterest", 0) or 0)
            self._data_store(cache_key, result)
            return result
        except Exception:
            return 0.0

    async def get_live_volume(self, event_id: str) -> float:
        """Live volume для события (Data API). Кеш 10 мин."""
        if not event_id:
            return 0.0
        cache_key = f"lv:{event_id}"
        cached = self._data_cached(cache_key)
        if cached is not None:
            return cached
        try:
            resp = await _request_with_retry(
                self.session, "GET", f"{DATA_BASE}/live-volume",
                params={"id": event_id}, retries=2, rate_bucket="data_api",
            )
            if resp.status != 200:
                return 0.0
            data = await resp.json()
            result = float(data.get("volume", 0) or 0)
            self._data_store(cache_key, result)
            return result
        except Exception:
            return 0.0

    async def get_top_holders(self, condition_id: str, limit: int = 10, min_balance: float = 500) -> list[dict]:
        """Крупные держатели позиций (smart money сигнал). Кеш 10 мин."""
        if not condition_id:
            return []
        cache_key = f"holders:{condition_id}"
        cached = self._data_cached(cache_key)
        if cached is not None:
            return cached
        try:
            resp = await _request_with_retry(
                self.session, "GET", f"{DATA_BASE}/holders",
                params={"market[]": condition_id, "limit": limit, "minBalance": min_balance},
                retries=2, rate_bucket="data_api",
            )
            if resp.status != 200:
                return []
            data = await resp.json()
            if not isinstance(data, list):
                return []
            holders = []
            for h in data[:limit]:
                holders.append({
                    "address": h.get("proxyWallet") or h.get("address") or "",
                    "balance": float(h.get("balance") or h.get("size") or 0),
                    "side": h.get("outcome") or "",
                })
            self._data_store(cache_key, holders)
            return holders
        except Exception:
            return []

    async def get_smart_money_signal(self, condition_id: str) -> dict:
        """Агрегированный smart money сигнал. Кеш 10 мин (через get_top_holders)."""
        empty = {"yes_volume": 0.0, "no_volume": 0.0, "bias": 0.5, "holder_count": 0, "reliable": False}
        holders = await self.get_top_holders(condition_id, limit=20, min_balance=500)
        if len(holders) < 3:
            return empty

        yes_vol = sum(h["balance"] for h in holders if h["side"].lower() == "yes")
        no_vol = sum(h["balance"] for h in holders if h["side"].lower() == "no")
        total = yes_vol + no_vol
        bias = yes_vol / total if total > 0 else 0.5

        return {
            "yes_volume": round(yes_vol, 2),
            "no_volume": round(no_vol, 2),
            "bias": round(bias, 3),
            "holder_count": len(holders),
            "reliable": total > 1000,
        }

    async def get_market_signals(self, market: dict) -> dict:
        """Собирает все дополнительные сигналы для рынка одним вызовом.
        Возвращает {open_interest, live_volume, smart_money}."""
        condition_id = market.get("condition_id", "")
        event_id = market.get("event_id", "")

        oi_task = self.get_open_interest(condition_id)
        vol_task = self.get_live_volume(event_id)
        sm_task = self.get_smart_money_signal(condition_id)

        results = await asyncio.gather(oi_task, vol_task, sm_task, return_exceptions=True)

        return {
            "open_interest": results[0] if not isinstance(results[0], Exception) else 0.0,
            "live_volume": results[1] if not isinstance(results[1], Exception) else 0.0,
            "smart_money": results[2] if not isinstance(results[2], Exception) else {},
        }

    async def place_bet(self, token_id: str, side: str, amount_usdc: float) -> dict:
        if config.DRY_RUN:
            print(f"  [DRY RUN] Ставка: {side} ${amount_usdc:.2f} | token_id={token_id[:10]}...")
            return {"dry_run": True, "side": side, "amount": amount_usdc}

        # Этап 9: реальные ставки через py-clob-client
        # from py_clob_client.client import ClobClient
        # from py_clob_client.clob_types import OrderArgs
        # client = ClobClient("https://clob.polymarket.com", key=config.POLY_PRIVATE_KEY, chain_id=137)
        # order_args = OrderArgs(token_id=token_id, price=..., size=amount_usdc, side=side)
        # return client.create_and_post_order(order_args)
        raise NotImplementedError("Реальные ставки включаются на Этапе 9")
