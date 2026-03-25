from __future__ import annotations

import re
from anthropic import AsyncAnthropic
import config
from core.database import count_open_bets

client = AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)

# Per-category MIN_EDGE — разные категории = разная предсказуемость
_MIN_EDGE_BY_CATEGORY = {
    "crypto": 0.06,       # шумные рынки, нужен больший edge
    "politics": 0.04,     # более предсказуемые
    "economics": 0.05,
    "tech": 0.05,
    "geopolitics": 0.07,  # высокая неопределённость
}

_CATEGORIES = {
    "crypto": ["bitcoin", "ethereum", "crypto", "btc", "eth", "solana", "sol",
               "coinbase", "binance", "stablecoin", "defi", "nft", "blockchain", "xrp",
               "cardano", "dogecoin", "doge", "polygon", "avalanche", "chainlink",
               "polkadot", "litecoin", "uniswap", "toncoin", "pepe", "memecoin"],
    "politics": ["trump", "president", "congress", "senate", "fed",
                 "white house", "executive order", "tariff", "sanction",
                 "republican", "democrat", "election"],
    "economics": ["inflation", "recession", "gdp", "interest rate", "dollar",
                  "stock market", "s&p", "nasdaq", "oil", "gold", "federal reserve"],
    "tech": ["openai", "gpt", "artificial intelligence", "apple",
             "tesla", "spacex", "elon", "google", "microsoft", "nvidia"],
    "geopolitics": ["ukraine", "russia", "china", "iran", "war", "ceasefire",
                    "nato", "israel", "gaza", "taiwan", "north korea"],
}


def _detect_category(question: str) -> str:
    q = question.lower()
    for category, keywords in _CATEGORIES.items():
        if any(re.search(r'\b' + re.escape(kw.strip()) + r'\b', q) for kw in keywords):
            return category
    return "other"

# Tool schema для structured output — Claude вернёт JSON через tool_use
_ANALYSIS_TOOL = {
    "name": "submit_analysis",
    "description": "Submit prediction market analysis result.",
    "input_schema": {
        "type": "object",
        "properties": {
            "probability": {
                "type": "number",
                "description": "Probability of YES outcome (0.01 to 0.99)",
            },
            "confidence": {
                "type": "string",
                "enum": ["low", "medium", "high"],
                "description": "Confidence in your estimate",
            },
            "reasoning": {
                "type": "string",
                "description": "Brief justification (1-2 sentences)",
            },
        },
        "required": ["probability", "confidence", "reasoning"],
    },
}

# Максимальное отклонение от рыночной цены (hard cap)
MAX_DEVIATION = 0.30


def _news_freshness_label(published_at: str) -> str:
    """Возвращает метку свежести: BREAKING / RECENT / OLD."""
    if not published_at:
        return ""
    try:
        from datetime import datetime, timezone
        # Парсим ISO формат
        dt_str = published_at.replace("Z", "+00:00")
        if "T" in dt_str:
            dt = datetime.fromisoformat(dt_str)
        else:
            dt = datetime.fromisoformat(dt_str + "T00:00:00+00:00")
        hours_ago = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
        if hours_ago <= 2:
            return "BREAKING"
        elif hours_ago <= 12:
            return "RECENT"
        elif hours_ago <= 48:
            return ""
        else:
            return "OLD"
    except (ValueError, TypeError):
        return ""


def _build_news_block(articles: list[dict]) -> str:
    if not articles:
        return "No recent news available. Use crypto market data and price signals if provided."
    # Сортируем по дате — свежие первыми
    sorted_articles = sorted(articles, key=lambda a: a.get("publishedAt", ""), reverse=True)
    lines = []
    for a in sorted_articles:
        date = a.get("publishedAt", "")[:16]  # YYYY-MM-DDTHH:MM
        title = a.get("title", "").strip()
        desc = a.get("description", "").strip()
        freshness = _news_freshness_label(a.get("publishedAt", ""))
        label = f" *** {freshness} ***" if freshness else ""
        lines.append(f"[{date}]{label} {title}")
        if desc:
            lines.append(f"  {desc}")
    return "\n".join(lines)


def _build_prompt(question: str, market_price: float, news_block: str,
                  price_change_1h: float = 0.0, crypto_signal: str = "",
                  volume: float = 0, liquidity: float = 0, end_date: str = "",
                  price_signals: dict = None, orderbook: dict = None,
                  market_signals: dict = None) -> str:
    # Многотаймфреймовые ценовые сигналы
    ps = price_signals or {}
    price_lines = []
    ch1 = ps.get("change_1h", price_change_1h)
    ch24 = ps.get("change_24h", 0)
    ch7d = ps.get("change_7d", 0)
    vol24 = ps.get("volatility_24h", 0)

    if abs(ch1) >= 0.02:
        price_lines.append(f"1h: {ch1:+.1%}")
    if abs(ch24) >= 0.03:
        price_lines.append(f"24h: {ch24:+.1%}")
    if abs(ch7d) >= 0.05:
        price_lines.append(f"7d: {ch7d:+.1%}")
    if vol24 >= 0.02:
        price_lines.append(f"volatility(24h): {vol24:.1%}")

    price_signal = ""
    if price_lines:
        price_signal = f"\nPrice movements: {' | '.join(price_lines)}"

    # Orderbook сигналы
    ob = orderbook or {}
    orderbook_block = ""
    if ob.get("reliable"):
        imbalance = ob.get("imbalance", 0.5)
        imb_label = "buyers dominate" if imbalance > 0.6 else "sellers dominate" if imbalance < 0.4 else "balanced"
        orderbook_block = (
            f"\nOrderbook: spread={ob['spread']:.3f} | "
            f"bid_vol=${ob['bid_volume']:,.0f} vs ask_vol=${ob['ask_volume']:,.0f} ({imb_label}) | "
            f"depth(±5%): bid=${ob['depth_bid']:,.0f} ask=${ob['depth_ask']:,.0f}"
        )

    # Market signals (OI, live volume, smart money)
    ms = market_signals or {}
    market_signals_block = ""
    ms_parts = []
    oi = ms.get("open_interest", 0)
    if oi > 0:
        ms_parts.append(f"Open Interest: ${oi:,.0f}")
    lv = ms.get("live_volume", 0)
    if lv > 0:
        ms_parts.append(f"Live volume: ${lv:,.0f}")
    sm = ms.get("smart_money", {})
    if isinstance(sm, dict) and sm.get("reliable"):
        bias = sm["bias"]
        bias_label = "YES-heavy" if bias > 0.6 else "NO-heavy" if bias < 0.4 else "balanced"
        ms_parts.append(f"Smart money ({sm['holder_count']} whales): {bias:.0%} YES ({bias_label})")
    if ms_parts:
        market_signals_block = "\n" + " | ".join(ms_parts)

    crypto_block = f"\nCrypto data: {crypto_signal}" if crypto_signal else ""

    crypto_rules = ""
    if crypto_signal:
        crypto_rules = """
- For crypto markets: consider current price, 24h trend, and Fear & Greed Index
- Fear & Greed > 75 = overheated market (correction risk rises)
- Fear & Greed < 25 = fear/panic (possible bounce)
- Sharp 24h price move can mean trend continuation OR reversal
- If multiple crypto bets are open — correlated risk, be conservative"""

    return f"""You are a prediction market analyst. Estimate the probability of this event.

Market question: {question}
Current YES price: {market_price:.1%}
Market volume: ${volume:,.0f} | Liquidity: ${liquidity:,.0f}
Closing date: {end_date or 'unknown'}{price_signal}{orderbook_block}{market_signals_block}{crypto_block}

Recent news:
{news_block}

CRITICAL RULES:
1. The market price ({market_price:.1%}) represents the collective estimate of thousands of traders with real money at stake. Treat it as a STRONG prior.
2. You need CONCRETE evidence from the news to justify deviating from the market price.
3. Deviation guidelines:
   - Without strong evidence: stay within ±5% of market price
   - With moderate evidence (relevant news): deviate up to ±15%
   - With extraordinary evidence (breaking news, confirmed events): deviate up to ±25%
   - NEVER deviate more than 30% from the market price
4. If market says <10%, do NOT estimate above 25% without extraordinary evidence
5. If market says >90%, do NOT estimate below 75% without extraordinary evidence
6. Use confidence="low" ONLY if you have zero information (no news AND no crypto data). If crypto data is provided, use at least "medium"
7. Your reasoning MUST explain WHY you deviate from the market price (if you do)

MARKET SIGNALS TO CONSIDER:
- Low liquidity (<$10k) = higher uncertainty, stay closer to market price
- High volume spike (24h vol > 10% of total) = something happened, weight news heavily
- Orderbook imbalance > 0.65 or < 0.35 = strong directional pressure from traders
- Price moved significantly in multiple timeframes in same direction = strong trend
- High volatility (>5%) = uncertain market, widen your confidence interval
- Smart money bias > 0.7 or < 0.3 = whales have strong conviction, consider following
- High Open Interest = more money at stake, market price is more reliable
- News marked *** BREAKING *** should be weighted much more than *** OLD *** news
- If BREAKING news contradicts market price, larger deviation is justified{crypto_rules}

Call submit_analysis with your result."""


def _parse_tool_response(response) -> dict | None:
    """Извлекает structured output из tool_use блока."""
    for block in response.content:
        if block.type == "tool_use" and block.name == "submit_analysis":
            data = block.input
            prob = data.get("probability", 0)
            conf = data.get("confidence", "low")
            reasoning = data.get("reasoning", "")
            if not (0.01 <= prob <= 0.99):
                return None
            if conf not in ("low", "medium", "high"):
                return None
            return {"probability": prob, "confidence": conf, "reasoning": reasoning}
    return None


def _kelly_bet(our_prob: float, market_price: float, exposure_scale: float = 1.0,
               fee_rate: float = 0.0) -> float:
    """Kelly criterion с учётом комиссии."""
    # Уменьшаем odds на fee (для crypto до 1.56%)
    b = (1 / market_price) - 1
    if fee_rate > 0:
        b = b * (1 - fee_rate)
    q = 1 - our_prob
    kelly = (our_prob * b - q) / b
    bet = kelly * config.KELLY_FRACTION * config.BUDGET * exposure_scale
    bet = min(bet, config.MAX_BET)
    return max(bet, 0.0)


def _estimate_fee(market_price: float, category: str) -> float:
    """Оценка комиссии по категории и цене."""
    if category == "crypto":
        # feeRate=0.25, exponent=2
        p = market_price
        return 0.25 * (p * (1 - p)) ** 2 * 2  # упрощённая формула
    return 0.0


class Strategy:
    async def evaluate(self, market: dict, articles: list[dict],
                       price_change_1h: float = 0.0, crypto_signal: str = "",
                       price_signals: dict = None, orderbook: dict = None,
                       market_signals: dict = None, fee_rate: float = 0.0):
        question = market["question"]
        yes_price = market["yes_price"]
        no_price = market["no_price"]
        timeframe = market.get("timeframe", "daily")
        category = _detect_category(question)
        news_block = _build_news_block(articles)
        prompt = _build_prompt(
            question, yes_price, news_block, price_change_1h, crypto_signal,
            volume=market.get("volume", 0),
            liquidity=market.get("liquidity", 0),
            end_date=(market.get("end_date") or "")[:10],
            price_signals=price_signals,
            orderbook=orderbook,
            market_signals=market_signals,
        )

        try:
            response = await client.messages.create(
                model=config.CLAUDE_MODEL,
                max_tokens=config.CLAUDE_MAX_TOKENS,
                tools=[_ANALYSIS_TOOL],
                tool_choice={"type": "tool", "name": "submit_analysis"},
                messages=[{"role": "user", "content": prompt}],
            )
        except Exception as e:
            print(f"  [Claude] Ошибка: {e}")
            return None

        # Сохраняем raw response для логирования
        raw_response = ""
        for block in response.content:
            if block.type == "tool_use":
                raw_response = str(block.input)
            elif block.type == "text":
                raw_response = block.text

        parsed = _parse_tool_response(response)
        if parsed is None:
            print(f"  [Claude] Не удалось получить structured output")
            return None

        our_prob = parsed["probability"]
        confidence = parsed["confidence"]
        reasoning = parsed["reasoning"]

        if confidence == "low":
            print(f"  [Claude] conf=low — пропускаем")
            return None

        # Hard cap: ограничить отклонение от рыночной цены
        deviation = our_prob - yes_price
        if abs(deviation) > MAX_DEVIATION:
            capped = yes_price + MAX_DEVIATION if deviation > 0 else yes_price - MAX_DEVIATION
            capped = max(0.01, min(0.99, capped))
            print(f"  [WARNING] Claude deviation {abs(deviation):.1%} capped: {our_prob:.1%} → {capped:.1%} (market={yes_price:.1%})")
            our_prob = capped

        # Portfolio Kelly: корреляционное масштабирование
        open_bets = count_open_bets()
        # Базовый exposure scale
        exposure_scale = 1.0 / (1 + open_bets * 0.1)
        # Дополнительная коррекция для коррелированных категорий (crypto)
        from core.database import count_open_bets_by_category
        open_by_cat = count_open_bets_by_category()
        same_cat_bets = open_by_cat.get(category, 0)
        if same_cat_bets >= 2:
            # Внутри категории корреляция выше — агрессивнее уменьшаем
            exposure_scale *= 1.0 / (1 + same_cat_bets * 0.2)

        # MIN_EDGE зависит от таймфрейма и категории
        if timeframe in ("1ч", "4ч"):
            min_edge = config.MIN_EDGE_SHORT
        else:
            min_edge = _MIN_EDGE_BY_CATEGORY.get(category, config.MIN_EDGE)

        # Fee: используем реальную ставку из API, fallback на estimate
        if fee_rate <= 0:
            fee_rate = _estimate_fee(yes_price, category)

        # Ставим только на ту сторону, в которую верит Claude
        if our_prob >= 0.5:
            side = "YES"
            edge = our_prob - yes_price
            bet = _kelly_bet(our_prob, yes_price, exposure_scale, fee_rate) if edge >= min_edge else 0
        else:
            side = "NO"
            edge = (1 - our_prob) - no_price
            bet = _kelly_bet(1 - our_prob, no_price, exposure_scale, fee_rate) if edge >= min_edge else 0

        if edge < min_edge:
            print(f"  [Claude] Edge {side}={edge:+.1%} < MIN_EDGE {min_edge:.0%} [{timeframe}/{category}] — пропускаем")
            return None

        if bet <= 0:
            print(f"  [Claude] Kelly=0 — пропускаем")
            return None

        return {
            "our_prob": our_prob,
            "market_prob": yes_price,  # всегда YES price = вероятность YES по рынку
            "edge": edge,
            "confidence": confidence,
            "bet_amount": round(bet, 2),
            "side": side,
            "reasoning": reasoning,
            "prompt_text": prompt,
            "raw_response": raw_response,
            "category": category,
        }
