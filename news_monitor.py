import json
import re
import time
from pathlib import Path

import aiohttp
import config

GNEWS_BASE = "https://gnews.io/api/v4/search"
CACHE_FILE = Path("news_cache.json")

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

    async def __aenter__(self):
        self.session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, *args):
        await self.session.close()
        _save_cache(self.cache)

    async def get_news(self, question: str):
        """Возвращает (articles, is_fresh).
        is_fresh=True  — данные только что получены из GNews (стоит звать Claude).
        is_fresh=False — из кэша (Claude пропускаем).
        """
        query = _extract_query(question)
        if not query:
            return [], False

        cached = self.cache.get(query)
        if cached and (time.time() - cached["fetched_at"]) < config.NEWS_CACHE_TTL:
            return cached["articles"], False

        articles = await self._fetch(query)
        self.cache[query] = {"articles": articles, "fetched_at": time.time()}
        _save_cache(self.cache)
        return articles, True

    async def _fetch(self, query: str) -> list[dict]:
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
            print(f"  [GNews] Ошибка запроса '{query}': {e}")
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
