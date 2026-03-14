import json
import re

from anthropic import AsyncAnthropic
import config

client = AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)


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

    return f"""Ты — аналитик рынков предсказаний. Оцени вероятность события на основе новостей.

Вопрос рынка: {question}
Текущая рыночная цена YES: {market_price:.0%}{price_signal}{crypto_block}

Свежие новости:
{news_block}

Ответь ТОЛЬКО валидным JSON без markdown и пояснений:
{{
  "probability": 0.72,
  "confidence": "medium",
  "reasoning": "Краткое обоснование на 1-2 предложения"
}}

Правила:
- probability: число от 0.01 до 0.99
- confidence: "low" | "medium" | "high"
- Используй "low" если новостей недостаточно или ситуация неясна
- Не повторяй рыночную цену как свою оценку без весомых оснований"""


def _parse_response(text: str):
    # Ищем JSON даже если модель добавила лишний текст
    match = re.search(r"\{.*?\}", text, re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group())
        prob = float(data.get("probability", 0))
        conf = data.get("confidence", "low")
        reasoning = data.get("reasoning", "")
        if not (0.01 <= prob <= 0.99):
            return None
        if conf not in ("low", "medium", "high"):
            return None
        return {"probability": prob, "confidence": conf, "reasoning": reasoning}
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


def _kelly_bet(our_prob: float, market_price: float) -> float:
    # b = коэффициент выигрыша (во сколько раз вернётся ставка)
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
                messages=[{"role": "user", "content": prompt}],
            )
            text = response.content[0].text
        except Exception as e:
            print(f"  [Claude] Ошибка: {e}")
            return None

        parsed = _parse_response(text)
        if parsed is None:
            print(f"  [Claude] Не удалось распарсить ответ: {text[:100]}")
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
            best = max(edge_yes, edge_no)
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
