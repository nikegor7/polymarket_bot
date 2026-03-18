from __future__ import annotations

from anthropic import AsyncAnthropic
import config

client = AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)

# Tool schema для structured output — Claude вернёт JSON через tool_use
_ANALYSIS_TOOL = {
    "name": "submit_analysis",
    "description": "Отправить результат анализа рынка предсказаний.",
    "input_schema": {
        "type": "object",
        "properties": {
            "probability": {
                "type": "number",
                "description": "Вероятность события (от 0.01 до 0.99)",
            },
            "confidence": {
                "type": "string",
                "enum": ["low", "medium", "high"],
                "description": "Уверенность в оценке",
            },
            "reasoning": {
                "type": "string",
                "description": "Краткое обоснование на 1-2 предложения",
            },
        },
        "required": ["probability", "confidence", "reasoning"],
    },
}


def _build_news_block(articles: list[dict]) -> str:
    if not articles:
        return "Свежих новостей не найдено."
    lines = []
    for a in articles:
        date = a.get("publishedAt", "")[:10]
        title = a.get("title", "").strip()
        desc = a.get("description", "").strip()
        lines.append(f"[{date}] {title}")
        if desc:
            lines.append(f"  {desc}")
    return "\n".join(lines)


def _build_prompt(question: str, market_price: float, news_block: str, price_change_1h: float = 0.0, crypto_signal: str = "") -> str:
    price_signal = ""
    if abs(price_change_1h) >= 0.03:
        direction = "вырос" if price_change_1h > 0 else "упал"
        price_signal = f"\nСигнал рынка: цена YES {direction} на {abs(price_change_1h):.1%} за последний час."

    crypto_block = f"\nКрипто данные: {crypto_signal}" if crypto_signal else ""

    crypto_rules = ""
    if crypto_signal:
        crypto_rules = """
- Для крипто-рынков учитывай: текущую цену монеты, 24h тренд, Fear & Greed Index
- Fear & Greed > 75 = рынок перегрет (вероятность коррекции растёт)
- Fear & Greed < 25 = рынок в страхе (возможен отскок)
- Резкий рост цены за 24ч может означать как продолжение тренда, так и откат"""

    return f"""Ты — аналитик рынков предсказаний. Оцени вероятность события на основе новостей и данных.

Вопрос рынка: {question}
Текущая рыночная цена YES: {market_price:.0%}{price_signal}{crypto_block}

Свежие новости:
{news_block}

Правила:
- probability: число от 0.01 до 0.99
- Используй confidence="low" если новостей недостаточно или ситуация неясна
- Не повторяй рыночную цену как свою оценку без весомых оснований
- Если есть сильный edge — используй confidence="high"{crypto_rules}

Вызови submit_analysis с результатом."""


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


def _kelly_bet(our_prob: float, market_price: float) -> float:
    b = (1 / market_price) - 1
    q = 1 - our_prob
    kelly = (our_prob * b - q) / b
    bet = kelly * config.KELLY_FRACTION * config.BUDGET
    bet = min(bet, config.MAX_BET)
    return max(bet, 0.0)


class Strategy:
    async def evaluate(self, market: dict, articles: list[dict], price_change_1h: float = 0.0, crypto_signal: str = ""):
        question = market["question"]
        yes_price = market["yes_price"]
        no_price = market["no_price"]
        news_block = _build_news_block(articles)
        prompt = _build_prompt(question, yes_price, news_block, price_change_1h, crypto_signal)

        try:
            response = await client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=256,
                tools=[_ANALYSIS_TOOL],
                tool_choice={"type": "tool", "name": "submit_analysis"},
                messages=[{"role": "user", "content": prompt}],
            )
        except Exception as e:
            print(f"  [Claude] Ошибка: {e}")
            return None

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

        edge_yes = our_prob - yes_price
        edge_no = (1 - our_prob) - no_price

        if edge_yes >= edge_no and edge_yes >= config.MIN_EDGE:
            side = "YES"
            edge = edge_yes
            bet = _kelly_bet(our_prob, yes_price)
            market_prob = yes_price
        elif edge_no > edge_yes and edge_no >= config.MIN_EDGE:
            side = "NO"
            edge = edge_no
            bet = _kelly_bet(1 - our_prob, no_price)
            market_prob = no_price
        else:
            print(f"  [Claude] Edge YES={edge_yes:+.1%} NO={edge_no:+.1%} < MIN_EDGE {config.MIN_EDGE:.0%} — пропускаем")
            return None

        if bet <= 0:
            print(f"  [Claude] Kelly=0 — пропускаем")
            return None

        return {
            "our_prob": our_prob,
            "market_prob": market_prob,
            "edge": edge,
            "confidence": confidence,
            "bet_amount": round(bet, 2),
            "side": side,
            "reasoning": reasoning,
        }
