from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path

import aiohttp
import config

GNEWS_BASE = "https://gnews.io/api/v4/search"
TAVILY_BASE = "https://api.tavily.com/search"
CRYPTOPANIC_BASE = f"https://cryptopanic.com/api/{config.CRYPTOPANIC_PLAN}/v2/posts/"
CACHE_FILE = Path("data/news_cache.json")
GNEWS_DAILY_LIMIT = 95  # оставляем запас от 100 req/day
EMPTY_CACHE_TTL = 120   # пустые ответы кэшируем только 2 мин (а не 30 мин)

# Маппинг ключевых слов → тикеры CryptoPanic
_CRYPTO_TICKERS: dict[str, str] = {
    "bitcoin": "BTC", "btc": "BTC",
    "ethereum": "ETH", "eth": "ETH",
    "solana": "SOL", "sol": "SOL",
    "xrp": "XRP",
    "cardano": "ADA", "ada": "ADA",
    "dogecoin": "DOGE", "doge": "DOGE",
    "polygon": "MATIC", "matic": "MATIC",
    "avalanche": "AVAX", "avax": "AVAX",
    "chainlink": "LINK", "link": "LINK",
    "polkadot": "DOT", "dot": "DOT",
    "litecoin": "LTC", "ltc": "LTC",
    "uniswap": "UNI", "uni": "UNI",
    "toncoin": "TON", "ton": "TON",
    "pepe": "PEPE",
    "binance": "BNB", "bnb": "BNB",
}

STOPWORDS = {
    "will", "the", "a", "an", "be", "is", "are", "was", "were",
    "in", "on", "at", "by", "for", "of", "to", "and", "or", "not",
    "above", "below", "over", "under", "before", "after", "than",
    "its", "their", "his", "her", "this", "that", "have", "has",
    "do", "does", "did", "can", "could", "would", "should", "may",
    "might", "with", "from", "into", "about", "within", "between",
    "win", "won", "lose", "lost", "cup", "world", "year", "time",
    "week", "day", "new", "one", "two", "top", "big", "get", "say",
    "says", "said", "make", "makes", "made", "take", "takes", "took",
    "release", "released", "launch", "launched", "exceed", "above",
    "below", "reach", "surpass", "before", "after", "june", "july",
    "august", "september", "october", "november", "december",
    "january", "february", "march", "april",
}


def _extract_query(question: str) -> str:
    words = re.findall(r"[\w$']+", question)
    keywords = [w for w in words if w.lower() not in STOPWORDS and len(w) >= 4]
    return " ".join(keywords[:4])


def _load_cache() -> dict:
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_cache(cache: dict) -> None:
    CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


class NewsMonitor:
    def __init__(self):
        self.session: aiohttp.ClientSession | None = None
        self.cache: dict = _load_cache()
        self._gnews_calls_today: int = 0
        self._gnews_day: str = ""  # YYYY-MM-DD для сброса счётчика
        self._fetch_sem = asyncio.Semaphore(1)  # последовательные запросы к API (GNews 429)

    async def __aenter__(self):
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))
        return self

    async def __aexit__(self, *args):
        await self.session.close()
        _save_cache(self.cache)

    def _detect_crypto_tickers(self, question: str) -> list[str]:
        """Извлекает тикеры криптовалют из вопроса рынка."""
        q_lower = question.lower()
        tickers = list(dict.fromkeys(
            ticker for kw, ticker in _CRYPTO_TICKERS.items() if kw in q_lower
        ))
        return tickers

    async def get_news(self, question: str):
        """Возвращает (articles, is_fresh).
        is_fresh=True  — данные только что получены из API.
        is_fresh=False — из кэша (статьи есть, но не новые).
        Пустые ответы кэшируются только на 2 мин.
        Для крипто вопросов сначала CryptoPanic, потом fallback.
        """
        query = _extract_query(question)
        if not query:
            return [], False

        # Для крипто — используем CryptoPanic ключ кэша
        tickers = self._detect_crypto_tickers(question)
        cache_key = f"cp:{','.join(tickers)}" if tickers and config.CRYPTOPANIC_API_KEY else query

        cached = self.cache.get(cache_key)
        if cached:
            ttl = EMPTY_CACHE_TTL if not cached["articles"] else config.NEWS_CACHE_TTL
            if (time.time() - cached["fetched_at"]) < ttl:
                return cached["articles"], False

        # Крипто → CryptoPanic (если есть ключ и тикеры)
        articles = []
        if tickers and config.CRYPTOPANIC_API_KEY:
            articles = await self._fetch_cryptopanic(tickers)

        # Fallback на общий источник если CryptoPanic не дал результатов
        if not articles:
            articles = await self._fetch(query)
            cache_key = query  # кэшируем под обычным ключом

        self.cache[cache_key] = {"articles": articles, "fetched_at": time.time()}
        _save_cache(self.cache)
        return articles, True

    async def _fetch(self, query: str) -> list[dict]:
        async with self._fetch_sem:
            if config.TAVILY_API_KEY:
                return await self._fetch_tavily(query)
            # Задержка 1с между запросами — GNews бьёт 429 при частых обращениях
            await asyncio.sleep(1.0)
            return await self._fetch_gnews(query)

    async def _fetch_cryptopanic(self, tickers: list[str]) -> list[dict]:
        """Получает крипто-новости из CryptoPanic API с sentiment."""
        params = {
            "auth_token": config.CRYPTOPANIC_API_KEY,
            "currencies": ",".join(tickers),
            "filter": "rising",
            "public": "true",
        }
        try:
            async with self.session.get(CRYPTOPANIC_BASE, params=params) as resp:
                if resp.status == 429:
                    print(f"  [CryptoPanic] Rate limit — fallback")
                    return []
                resp.raise_for_status()
                data = await resp.json()
        except Exception as e:
            # Скрываем API ключ из сообщения об ошибке
            err_msg = str(e).replace(config.CRYPTOPANIC_API_KEY, "***")
            print(f"  [CryptoPanic] Ошибка: {err_msg}")
            return []

        articles = []
        for post in data.get("results", [])[:5]:
            title = post.get("title", "").strip()
            if not title:
                continue
            # Sentiment из votes
            votes = post.get("votes", {})
            pos = votes.get("positive", 0)
            neg = votes.get("negative", 0)
            sentiment = ""
            if pos + neg > 0:
                sentiment = "bullish" if pos > neg else "bearish" if neg > pos else "neutral"

            desc_parts = []
            if sentiment:
                desc_parts.append(f"Sentiment: {sentiment} (+{pos}/-{neg})")
            source = post.get("source", {}).get("title", "")
            if source:
                desc_parts.append(f"Source: {source}")

            articles.append({
                "title": title,
                "description": " | ".join(desc_parts),
                "publishedAt": post.get("published_at", ""),
            })
        return articles

    async def _fetch_tavily(self, query: str) -> list[dict]:
        payload = {
            "api_key": config.TAVILY_API_KEY,
            "query": query,
            "search_depth": "basic",
            "max_results": 5,
            "include_answer": False,
        }
        try:
            async with self.session.post(TAVILY_BASE, json=payload) as resp:
                resp.raise_for_status()
                data = await resp.json()
        except Exception as e:
            print(f"  [Tavily] Ошибка запроса '{query}': {e}")
            return []

        articles = []
        for r in data.get("results", []):
            title = r.get("title", "").strip()
            if not title:
                continue
            articles.append({
                "title": title,
                "description": r.get("content", "")[:300].strip(),
                "publishedAt": r.get("published_date", ""),
            })
        return articles

    async def _fetch_gnews(self, query: str) -> list[dict]:
        from datetime import date
        today = date.today().isoformat()
        if self._gnews_day != today:
            self._gnews_day = today
            self._gnews_calls_today = 0
        if self._gnews_calls_today >= GNEWS_DAILY_LIMIT:
            print(f"  [GNews] Лимит {GNEWS_DAILY_LIMIT} запросов/день исчерпан — пропуск")
            return []
        self._gnews_calls_today += 1

        params = {
            "q": query,
            "lang": "en",
            "max": 5,
            "sortby": "publishedAt",
            "apikey": config.GNEWS_API_KEY,
        }
        try:
            async with self.session.get(GNEWS_BASE, params=params) as resp:
                resp.raise_for_status()
                data = await resp.json()
        except Exception as e:
            err_msg = str(e).replace(config.GNEWS_API_KEY, "***")
            print(f"  [GNews] Ошибка запроса '{query}': {err_msg}")
            return []

        articles = []
        for a in data.get("articles", []):
            title = a.get("title", "").strip()
            description = a.get("description", "").strip()
            if not title:
                continue
            articles.append({
                "title": title,
                "description": description,
                "publishedAt": a.get("publishedAt", ""),
            })
        return articles
