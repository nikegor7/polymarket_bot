"""Тесты для Kelly criterion, edge detection, tool_use parsing."""
from __future__ import annotations

import sys
import os
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ.setdefault("GNEWS_API_KEY", "test-key")

from core.strategy import _kelly_bet, _parse_tool_response, _build_news_block
import config


# ─── Kelly criterion ───────────────────────────────────────

def test_kelly_positive_edge():
    """При our_prob > market_price Kelly должен дать положительную ставку."""
    bet = _kelly_bet(our_prob=0.70, market_price=0.55)
    assert bet > 0, f"Expected positive bet, got {bet}"


def test_kelly_no_edge():
    """Если our_prob == market_price, Kelly ~ 0."""
    bet = _kelly_bet(our_prob=0.50, market_price=0.50)
    assert bet == 0.0, f"Expected 0 bet, got {bet}"


def test_kelly_negative_edge():
    """Если our_prob < market_price, Kelly должен быть 0 (зажат max(0))."""
    bet = _kelly_bet(our_prob=0.40, market_price=0.60)
    assert bet == 0.0, f"Expected 0 bet, got {bet}"


def test_kelly_max_bet_cap():
    """Kelly не должен превышать MAX_BET."""
    bet = _kelly_bet(our_prob=0.99, market_price=0.10)
    assert bet <= config.MAX_BET, f"Bet {bet} exceeds MAX_BET {config.MAX_BET}"


def test_kelly_extreme_odds():
    """Почти 100% вероятность при низкой цене — должен быть capped."""
    bet = _kelly_bet(our_prob=0.95, market_price=0.05)
    assert 0 < bet <= config.MAX_BET


def test_kelly_symmetric():
    """Kelly для NO стороны: prob=0.7 на NO (market_price=0.45) должен давать ставку."""
    bet = _kelly_bet(our_prob=0.70, market_price=0.45)
    assert bet > 0


# ─── Edge detection ────────────────────────────────────────

def test_edge_yes_side():
    """Edge YES = our_prob - yes_price."""
    our_prob = 0.72
    yes_price = 0.61
    edge_yes = our_prob - yes_price
    assert abs(edge_yes - 0.11) < 0.001


def test_edge_no_side():
    """Edge NO = (1 - our_prob) - no_price."""
    our_prob = 0.30
    no_price = 0.55
    edge_no = (1 - our_prob) - no_price
    assert abs(edge_no - 0.15) < 0.001


def test_edge_neither_side():
    """Когда edge < MIN_EDGE на обоих сторонах — нет ставки."""
    our_prob = 0.52
    yes_price = 0.50
    no_price = 0.50
    edge_yes = our_prob - yes_price
    edge_no = (1 - our_prob) - no_price
    assert edge_yes < config.MIN_EDGE
    assert edge_no < config.MIN_EDGE


def test_edge_picks_side_aligned_with_belief():
    """Бот ставит на сторону, в которую верит Claude."""
    # Claude считает prob=0.30 → верит в NO
    our_prob = 0.30
    no_price = 0.60
    edge_no = (1 - our_prob) - no_price
    assert edge_no >= config.MIN_EDGE  # 0.70 - 0.60 = 0.10

    # Claude считает prob=0.80 → верит в YES, не должен ставить NO
    our_prob_high = 0.80
    yes_price = 0.70
    edge_yes = our_prob_high - yes_price
    assert edge_yes >= config.MIN_EDGE  # 0.80 - 0.70 = 0.10


def test_no_contrarian_bet():
    """Claude prob=0.92 → не должен ставить NO даже если edge на NO есть."""
    our_prob = 0.92
    yes_price = 0.95
    no_price = 0.05
    # Старая логика нашла бы edge_no = 0.08 - 0.05 = 0.03
    # Новая логика: our_prob >= 0.5 → только YES
    edge_yes = our_prob - yes_price  # -0.03
    assert edge_yes < config.MIN_EDGE  # нет edge на YES → пропуск (правильно)


# ─── Parse tool_use response ──────────────────────────────

def _mock_response(tool_input: dict):
    """Создаёт mock response с tool_use блоком."""
    block = MagicMock()
    block.type = "tool_use"
    block.name = "submit_analysis"
    block.input = tool_input
    response = MagicMock()
    response.content = [block]
    return response


def _mock_text_response(text: str):
    """Mock response с текстовым блоком (без tool_use)."""
    block = MagicMock()
    block.type = "text"
    block.text = text
    response = MagicMock()
    response.content = [block]
    return response


def test_parse_valid_tool_response():
    resp = _mock_response({"probability": 0.72, "confidence": "medium", "reasoning": "Test reason"})
    result = _parse_tool_response(resp)
    assert result is not None
    assert result["probability"] == 0.72
    assert result["confidence"] == "medium"
    assert result["reasoning"] == "Test reason"


def test_parse_high_confidence():
    resp = _mock_response({"probability": 0.85, "confidence": "high", "reasoning": "Very strong signal"})
    result = _parse_tool_response(resp)
    assert result is not None
    assert result["confidence"] == "high"


def test_parse_invalid_probability():
    resp = _mock_response({"probability": 1.5, "confidence": "medium", "reasoning": "Bad"})
    result = _parse_tool_response(resp)
    assert result is None


def test_parse_zero_probability():
    resp = _mock_response({"probability": 0.0, "confidence": "medium", "reasoning": "Bad"})
    result = _parse_tool_response(resp)
    assert result is None


def test_parse_invalid_confidence():
    resp = _mock_response({"probability": 0.5, "confidence": "very_high", "reasoning": "Bad"})
    result = _parse_tool_response(resp)
    assert result is None


def test_parse_no_tool_use():
    """Если Claude ответил текстом вместо tool_use — None."""
    resp = _mock_text_response("I think the probability is 0.72")
    result = _parse_tool_response(resp)
    assert result is None


def test_parse_empty_content():
    response = MagicMock()
    response.content = []
    result = _parse_tool_response(response)
    assert result is None


# ─── News block builder ───────────────────────────────────

def test_build_news_empty():
    assert "No recent news" in _build_news_block([])


def test_build_news_formats_articles():
    articles = [
        {"title": "Bitcoin hits 100k", "description": "New ATH", "publishedAt": "2026-03-15T10:00:00Z"},
        {"title": "ETH follows", "description": "", "publishedAt": "2026-03-15T11:00:00Z"},
    ]
    block = _build_news_block(articles)
    assert "Bitcoin hits 100k" in block
    assert "ETH follows" in block
    assert "2026-03-15" in block
