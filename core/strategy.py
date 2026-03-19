from __future__ import annotations

from anthropic import AsyncAnthropic
import config
from core.database import count_open_bets

client = AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)

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


def _build_news_block(articles: list[dict]) -> str:
    if not articles:
        return "No recent news available. Use crypto market data and price signals if provided."
    lines = []
    for a in articles:
        date = a.get("publishedAt", "")[:10]
        title = a.get("title", "").strip()
        desc = a.get("description", "").strip()
        lines.append(f"[{date}] {title}")
        if desc:
            lines.append(f"  {desc}")
    return "\n".join(lines)


def _build_prompt(question: str, market_price: float, news_block: str,
                  price_change_1h: float = 0.0, crypto_signal: str = "",
                  volume: float = 0, liquidity: float = 0, end_date: str = "") -> str:
    price_signal = ""
    if abs(price_change_1h) >= 0.03:
        direction = "rose" if price_change_1h > 0 else "fell"
        price_signal = f"\nPrice signal: YES price {direction} by {abs(price_change_1h):.1%} in the last hour."

    crypto_block = f"\nCrypto data: {crypto_signal}" if crypto_signal else ""

    crypto_rules = ""
    if crypto_signal:
        crypto_rules = """
- For crypto markets: consider current price, 24h trend, and Fear & Greed Index
- Fear & Greed > 75 = overheated market (correction risk rises)
- Fear & Greed < 25 = fear/panic (possible bounce)
- Sharp 24h price move can mean trend continuation OR reversal"""

    return f"""You are a prediction market analyst. Estimate the probability of this event.

Market question: {question}
Current YES price: {market_price:.1%}
Market volume: ${volume:,.0f} | Liquidity: ${liquidity:,.0f}
Closing date: {end_date or 'unknown'}{price_signal}{crypto_block}

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
7. Your reasoning MUST explain WHY you deviate from the market price (if you do){crypto_rules}

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


def _kelly_bet(our_prob: float, market_price: float, exposure_scale: float = 1.0) -> float:
    b = (1 / market_price) - 1
    q = 1 - our_prob
    kelly = (our_prob * b - q) / b
    bet = kelly * config.KELLY_FRACTION * config.BUDGET * exposure_scale
    bet = min(bet, config.MAX_BET)
    return max(bet, 0.0)


class Strategy:
    async def evaluate(self, market: dict, articles: list[dict], price_change_1h: float = 0.0, crypto_signal: str = ""):
        question = market["question"]
        yes_price = market["yes_price"]
        no_price = market["no_price"]
        news_block = _build_news_block(articles)
        prompt = _build_prompt(
            question, yes_price, news_block, price_change_1h, crypto_signal,
            volume=market.get("volume", 0),
            liquidity=market.get("liquidity", 0),
            end_date=(market.get("end_date") or "")[:10],
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

        # Portfolio Kelly: уменьшаем ставку при росте открытых позиций
        open_bets = count_open_bets()
        exposure_scale = 1.0 / (1 + open_bets * 0.1)

        # Ставим только на ту сторону, в которую верит Claude
        if our_prob >= 0.5:
            side = "YES"
            edge = our_prob - yes_price
            bet = _kelly_bet(our_prob, yes_price, exposure_scale) if edge >= config.MIN_EDGE else 0
        else:
            side = "NO"
            edge = (1 - our_prob) - no_price
            bet = _kelly_bet(1 - our_prob, no_price, exposure_scale) if edge >= config.MIN_EDGE else 0

        if edge < config.MIN_EDGE:
            print(f"  [Claude] Edge {side}={edge:+.1%} < MIN_EDGE {config.MIN_EDGE:.0%} — пропускаем")
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
        }
