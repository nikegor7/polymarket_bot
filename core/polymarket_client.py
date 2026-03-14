from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta, timezone

import aiohttp
import config

GAMMA_BASE = "https://gamma-api.polymarket.com"
CLOB_BASE = "https://clob.polymarket.com"
COINGECKO_BASE = "https://api.coingecko.com/api/v3"
MARKETS_CACHE_TTL = 900   # 15 минут
DAILY_CACHE_TTL = 120     # 2 минуты

_COIN_MAP = {
    "bitcoin": "bitcoin", "btc": "bitcoin",
    "ethereum": "ethereum", "eth": "ethereum",
    "solana": "solana", "sol": "solana",
    "binance": "binancecoin", "bnb": "binancecoin",
    "coinbase": "coinbase-exchange-token",
    "xrp": "ripple",
}


class PolymarketClient:
    def __init__(self):
        self.session: aiohttp.ClientSession | None = None
        self._markets_cache: list = []
        self._markets_fetched_at: float = 0
        self._daily_cache: list = []
        self._daily_fetched_at: float = 0

    async def __aenter__(self):
        self.session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, *args):
        await self.session.close()

    def _parse_market(self, m: dict) -> dict | None:
        if not m.get("acceptingOrders"):
            return None

        try:
            volume = float(m.get("volumeNum") or 0)
            liquidity = float(m.get("liquidityNum") or 0)
        except (TypeError, ValueError):
            return None

        if volume < config.MIN_MARKET_VOLUME:
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

        question_lower = (m.get("question") or "").lower()
        if not any(re.search(r'\b' + re.escape(topic) + r'\b', question_lower) for topic in config.ALLOWED_TOPICS):
            return None

        clob_token_ids = m.get("clobTokenIds") or []
        yes_token_id = clob_token_ids[yes_idx] if len(clob_token_ids) > yes_idx else ""
        no_token_id = clob_token_ids[no_idx] if len(clob_token_ids) > no_idx else ""

        return {
            "condition_id": m.get("conditionId"),
            "question": m.get("question"),
            "volume": volume,
            "volume_24hr": float(m.get("volume24hr") or 0),
            "liquidity": liquidity,
            "yes_price": yes_price,
            "no_price": no_price,
            "yes_token_id": yes_token_id,
            "no_token_id": no_token_id,
            "end_date": m.get("endDateIso"),
        }

    async def get_markets(self, limit: int = config.TOP_MARKETS) -> list[dict]:
        if self._markets_cache and (time.time() - self._markets_fetched_at) < MARKETS_CACHE_TTL:
            return self._markets_cache

        params = {
            "active": "true",
            "closed": "false",
            "limit": 200,
        }

        if config.MAX_DAYS_TO_CLOSE > 0:
            end_max = (datetime.now(timezone.utc) + timedelta(days=config.MAX_DAYS_TO_CLOSE)).strftime("%Y-%m-%dT23:59:59Z")
            params["end_date_max"] = end_max

        async with self.session.get(f"{GAMMA_BASE}/markets", params=params) as resp:
            resp.raise_for_status()
            data = await resp.json()

        markets = [m for raw in data if (m := self._parse_market(raw)) is not None]
        markets.sort(key=lambda x: x["volume_24hr"], reverse=True)
        self._markets_cache = markets[:limit]
        self._markets_fetched_at = time.time()
        return self._markets_cache

    async def get_daily_markets(self, limit: int = config.TOP_MARKETS) -> list[dict]:
        """Рынки, закрывающиеся в ближайшие 24 часа."""
        if self._daily_cache and (time.time() - self._daily_fetched_at) < DAILY_CACHE_TTL:
            return self._daily_cache

        now = datetime.now(timezone.utc)
        end_min = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        end_max = (now + timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")

        params = {
            "active": "true",
            "closed": "false",
            "end_date_min": end_min,
            "end_date_max": end_max,
            "limit": 200,
        }

        async with self.session.get(f"{GAMMA_BASE}/markets", params=params) as resp:
            resp.raise_for_status()
            data = await resp.json()

        markets = [m for raw in data if (m := self._parse_market(raw)) is not None]
        # Ближайшие к закрытию — в топе
        markets.sort(key=lambda x: x["end_date"] or "9999")
        self._daily_cache = markets[:limit]
        self._daily_fetched_at = time.time()
        return self._daily_cache

    async def get_price_change_1h(self, token_id: str) -> float:
        """Изменение цены YES за последний час. + = рост, - = падение."""
        if not token_id:
            return 0.0
        now = int(time.time())
        params = {"market": token_id, "startTs": now - 3600, "endTs": now, "fidelity": 1}
        try:
            async with self.session.get(f"{CLOB_BASE}/prices-history", params=params) as resp:
                if resp.status != 200:
                    return 0.0
                data = await resp.json()
                history = data.get("history", [])
                if len(history) < 2:
                    return 0.0
                first = float(history[0].get("p", 0))
                last = float(history[-1].get("p", 0))
                return round(last - first, 4)
        except Exception:
            return 0.0

    async def get_spread(self, token_id: str) -> float:
        """Спред bid-ask для токена. Меньше = лучше исполнение."""
        if not token_id:
            return 1.0
        try:
            async with self.session.get(f"{CLOB_BASE}/book", params={"token_id": token_id}) as resp:
                if resp.status != 200:
                    return 1.0
                data = await resp.json()
                bids = data.get("bids", [])
                asks = data.get("asks", [])
                if not bids or not asks:
                    return 1.0
                best_bid = max(float(b["price"]) for b in bids)
                best_ask = min(float(a["price"]) for a in asks)
                return round(best_ask - best_bid, 4)
        except Exception:
            return 1.0

    async def get_crypto_signal(self, question: str) -> str:
        """Возвращает строку с ценой и 24h изменением для крипто-монет из вопроса.
        Пример: 'BTC: $85,000 (+3.2% 24h), ETH: $3,200 (-1.1% 24h)'
        Если монет нет или ошибка — пустая строка."""
        q = question.lower()
        coins = list(dict.fromkeys(cid for kw, cid in _COIN_MAP.items() if kw in q))
        if not coins:
            return ""
        try:
            async with self.session.get(
                f"{COINGECKO_BASE}/simple/price",
                params={"ids": ",".join(coins), "vs_currencies": "usd", "include_24hr_change": "true"},
            ) as resp:
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
        return ", ".join(parts)

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
