import json
import re
import time
from pathlib import Path

import aiohttp
import config

NEWS_API_BASE = "https://newsapi.org/v2/everything"
CACHE_FILE = Path("news_cache.json")

STOPWORDS = {
    "will", "the", "a", "an", "be", "is", "are", "was", "were",
    "in", "on", "at", "by", "for", "of", "to", "and", "or", "not",
    "above", "below", "over", "under", "before", "after", "than",
    "its", "their", "his", "her", "this", "that", "have", "has",
    "do", "does", "did", "can", "could", "would", "should", "may",
    "might", "with", "from", "into", "about", "within", "between",
}


def _extract_query(question: str) -> str:
    words = re.findall(r"[A-Za-z0-9$']+", question)
    keywords = [w for w in words if w.lower() not in STOPWORDS and len(w) > 2]
    return " ".join(keywords[:5])


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

    async def get_news(self, question: str) -> list[dict]:
        query = _extract_query(question)
        if not query:
            return []

        cached = self.cache.get(query)
        if cached and (time.time() - cached["fetched_at"]) < config.NEWS_CACHE_TTL:
            return cached["articles"]

        articles = await self._fetch(query)
        self.cache[query] = {"articles": articles, "fetched_at": time.time()}
        _save_cache(self.cache)
        return articles

    async def _fetch(self, query: str) -> list[dict]:
        params = {
            "q": query,
            "sortBy": "publishedAt",
            "pageSize": 5,
            "language": "en",
            "apiKey": config.NEWS_API_KEY,
        }
        try:
            async with self.session.get(NEWS_API_BASE, params=params) as resp:
                resp.raise_for_status()
                data = await resp.json()
        except Exception as e:
            print(f"  [NewsAPI] Ошибка запроса '{query}': {e}")
            return []

        articles = []
        for a in data.get("articles", []):
            title = a.get("title") or ""
            description = a.get("description") or ""
            if title == "[Removed]":
                continue
            articles.append({
                "title": title,
                "description": description,
                "publishedAt": a.get("publishedAt", ""),
            })
        return articles
