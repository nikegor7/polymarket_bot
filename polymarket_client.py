import json
import aiohttp
import config

GAMMA_BASE = "https://gamma-api.polymarket.com"


class PolymarketClient:
    def __init__(self):
        self.session: aiohttp.ClientSession | None = None

    async def __aenter__(self):
        self.session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, *args):
        await self.session.close()

    async def get_markets(self, limit: int = config.TOP_MARKETS) -> list[dict]:
        params = {
            "active": "true",
            "closed": "false",
            "limit": 100,  # берём с запасом, потом сортируем и обрезаем
        }

        async with self.session.get(f"{GAMMA_BASE}/markets", params=params) as resp:
            resp.raise_for_status()
            data = await resp.json()

        markets = []
        for m in data:
            if not m.get("acceptingOrders"):
                continue

            try:
                volume = float(m.get("volumeNum") or 0)
                liquidity = float(m.get("liquidityNum") or 0)
            except (TypeError, ValueError):
                continue

            if volume < config.MIN_MARKET_VOLUME:
                continue

            # outcomePrices и outcomes — JSON-строки вида '["0.61", "0.39"]'
            try:
                prices = json.loads(m.get("outcomePrices", "[]"))
                outcomes = json.loads(m.get("outcomes", "[]"))
            except (json.JSONDecodeError, TypeError):
                continue

            if len(prices) != 2 or len(outcomes) != 2:
                continue

            yes_idx = next((i for i, o in enumerate(outcomes) if o.lower() == "yes"), None)
            no_idx = next((i for i, o in enumerate(outcomes) if o.lower() == "no"), None)

            if yes_idx is None or no_idx is None:
                continue

            yes_price = float(prices[yes_idx])
            no_price = float(prices[no_idx])

            if yes_price == 0 or no_price == 0:
                continue

            # token_id для ставок (нужен в Этапе 8)
            clob_token_ids = m.get("clobTokenIds") or []
            yes_token_id = clob_token_ids[yes_idx] if len(clob_token_ids) > yes_idx else ""
            no_token_id = clob_token_ids[no_idx] if len(clob_token_ids) > no_idx else ""

            markets.append({
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
            })

        # Сортируем по объёму за 24ч — самые горячие рынки в топе
        markets.sort(key=lambda x: x["volume_24hr"], reverse=True)
        return markets[:limit]

    async def place_bet(self, token_id: str, side: str, amount_usdc: float) -> dict:
        if config.DRY_RUN:
            print(f"  [DRY RUN] Ставка: {side} ${amount_usdc:.2f} | token_id={token_id[:10]}...")
            return {"dry_run": True, "side": side, "amount": amount_usdc}

        # Этап 8: реальные ставки через py-clob-client
        # from py_clob_client.client import ClobClient
        # from py_clob_client.clob_types import OrderArgs
        # client = ClobClient("https://clob.polymarket.com", key=config.POLY_PRIVATE_KEY, chain_id=137)
        # order_args = OrderArgs(token_id=token_id, price=..., size=amount_usdc, side=side)
        # return client.create_and_post_order(order_args)
        raise NotImplementedError("Реальные ставки включаются на Этапе 8")
