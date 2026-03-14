import os
from dotenv import load_dotenv

load_dotenv()


def _require(key: str) -> str:
    value = os.getenv(key)
    if not value:
        raise ValueError(f"Missing required env variable: {key}")
    return value


# API keys
ANTHROPIC_API_KEY: str = _require("ANTHROPIC_API_KEY")
GNEWS_API_KEY: str = _require("GNEWS_API_KEY")
TAVILY_API_KEY: str = os.getenv("TAVILY_API_KEY", "")  # если задан — используется вместо GNews
POLY_PRIVATE_KEY: str = os.getenv("POLY_PRIVATE_KEY", "")

# Budget
BUDGET: float = float(os.getenv("BUDGET", "100"))
MAX_BET: float = float(os.getenv("MAX_BET", "5"))

# Mode
DRY_RUN: bool = os.getenv("DRY_RUN", "true").lower() == "true"

# Strategy
MIN_EDGE: float = 0.05
KELLY_FRACTION: float = 0.25

# Runtime
POLL_INTERVAL: int = 300
DAILY_POLL_INTERVAL: int = 60   # daily рынки опрашивать каждую минуту
TOP_MARKETS: int = 10
MIN_MARKET_VOLUME: int = 1000
NEWS_CACHE_TTL: int = 14400  # 4 часа — вписываемся в 100 req/day GNews

# Daily mode
DAILY_MODE: bool = os.getenv("DAILY_MODE", "false").lower() == "true"
MAX_DAYS_TO_CLOSE: int = int(os.getenv("MAX_DAYS_TO_CLOSE", "0"))  # 0 = без ограничения

# CLOB фильтры (Этапы 12-13)
MAX_SPREAD: float = float(os.getenv("MAX_SPREAD", "0.05"))          # макс спред для ставки
PRICE_CHANGE_THRESHOLD: float = 0.03                                 # движение > 3% за час = сигнал

# Фильтр тем — только рынки где хотя бы одно слово есть в вопросе
# Спорт намеренно исключён — результаты матчей плохо предсказываются по новостям
ALLOWED_TOPICS: list = [
    # Крипто
    "bitcoin", "ethereum", "crypto", "btc", "eth", "solana", "sol",
    "coinbase", "binance", "stablecoin", "defi", "nft", "blockchain",
    # Политика / США
    "trump", "election", "president", "congress", "senate", "fed",
    "white house", "executive order", "tariff", "sanction",
    # Экономика
    "inflation", "recession", "gdp", "interest rate", "dollar",
    "stock market", "s&p", "nasdaq", "oil", "gold",
    # Технологии
    "openai", "gpt", "artificial intelligence", "ai ", "apple",
    "tesla", "spacex", "elon", "google", "microsoft",
    # Геополитика
    "ukraine", "russia", "china", "iran", "war", "ceasefire",
    "nato", "israel", "gaza",
]
