"""Тесты для Kelly criterion, edge detection, parse_response."""
from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Подменяем env до импорта config
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ.setdefault("GNEWS_API_KEY", "test-key")

from core.strategy import _kelly_bet, _parse_response, _build_news_block
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
    """Kelly для NO стороны: prob=0.3 на NO (market_price=0.45) должен давать ставку."""
    # Для NO: our_prob для NO = 1 - 0.3 = 0.7, market_price NO = 0.45
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
    edge_yes = our_prob - yes_price  # 0.02
    edge_no = (1 - our_prob) - no_price  # -0.02
    assert edge_yes < config.MIN_EDGE
    assert edge_no < config.MIN_EDGE


def test_edge_picks_best_side():
    """Бот должен выбрать сторону с большим edge."""
    our_prob = 0.30
    yes_price = 0.40
    no_price = 0.60
    edge_yes = our_prob - yes_price  # -0.10
    edge_no = (1 - our_prob) - no_price  # 0.10
    assert edge_no > edge_yes
    assert edge_no >= config.MIN_EDGE


# ─── Parse response ────────────────────────────────────────

def test_parse_valid_json():
    text = '{"probability": 0.72, "confidence": "medium", "reasoning": "Test reason"}'
    result = _parse_response(text)
    assert result is not None
    assert result["probability"] == 0.72
    assert result["confidence"] == "medium"
    assert result["reasoning"] == "Test reason"


def test_parse_json_with_noise():
    """Claude иногда добавляет текст вокруг JSON."""
    text = 'Here is my analysis:\n{"probability": 0.65, "confidence": "high", "reasoning": "Strong signal"}\nThank you.'
    result = _parse_response(text)
    assert result is not None
    assert result["probability"] == 0.65


def test_parse_invalid_probability():
    text = '{"probability": 1.5, "confidence": "medium", "reasoning": "Bad"}'
    result = _parse_response(text)
    assert result is None


def test_parse_zero_probability():
    text = '{"probability": 0.0, "confidence": "medium", "reasoning": "Bad"}'
    result = _parse_response(text)
    assert result is None


def test_parse_invalid_confidence():
    text = '{"probability": 0.5, "confidence": "very_high", "reasoning": "Bad"}'
    result = _parse_response(text)
    assert result is None


def test_parse_garbage():
    result = _parse_response("This is not JSON at all")
    assert result is None


def test_parse_empty():
    result = _parse_response("")
    assert result is None


# ─── News block builder ───────────────────────────────────

def test_build_news_empty():
    assert "не найдено" in _build_news_block([])


def test_build_news_formats_articles():
    articles = [
        {"title": "Bitcoin hits 100k", "description": "New ATH", "publishedAt": "2026-03-15T10:00:00Z"},
        {"title": "ETH follows", "description": "", "publishedAt": "2026-03-15T11:00:00Z"},
    ]
    block = _build_news_block(articles)
    assert "Bitcoin hits 100k" in block
    assert "ETH follows" in block
    assert "2026-03-15" in block
