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

# Telegram (опционально — если не заданы, уведомления отключены)
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")

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
MIN_MARKET_VOLUME: int = 1000   # минимальный объём 24h
MIN_MARKET_VOLUME_TOTAL: int = 10000  # минимальный общий объём
NEWS_CACHE_TTL: int = 14400  # 4 часа — вписываемся в 100 req/day GNews

# Daily mode
DAILY_MODE: bool = os.getenv("DAILY_MODE", "false").lower() == "true"
MAX_DAYS_TO_CLOSE: int = int(os.getenv("MAX_DAYS_TO_CLOSE", "0"))  # 0 = без ограничения

# CLOB фильтры (Этапы 12-13)
MAX_SPREAD: float = float(os.getenv("MAX_SPREAD", "0.05"))          # макс спред для ставки
PRICE_CHANGE_THRESHOLD: float = 0.03                                 # движение > 3% за час = сигнал

# Топики по категориям — спорт намеренно исключён
CATEGORY_TOPICS: dict = {
    "crypto": [
        "bitcoin", "ethereum", "crypto", "btc", "eth", "solana", "sol",
        "coinbase", "binance", "stablecoin", "defi", "nft", "blockchain", "xrp",
        "cardano", "ada", "dogecoin", "doge", "polygon", "matic", "avalanche",
        "avax", "chainlink", "link", "polkadot", "dot", "litecoin", "ltc",
        "uniswap", "uni", "toncoin", "ton", "pepe", "memecoin",
    ],
    "politics": [
        "trump", "president", "congress", "senate", "fed",
        "white house", "executive order", "tariff", "sanction",
        "republican", "democrat", "us election", "american election",
    ],
    "economics": [
        "inflation", "recession", "gdp", "interest rate", "dollar",
        "stock market", "s&p", "nasdaq", "oil", "gold", "federal reserve",
    ],
    "tech": [
        "openai", "gpt", "artificial intelligence", "apple",
        "tesla", "spacex", "elon", "google", "microsoft", "nvidia",
    ],
    "geopolitics": [
        "ukraine", "russia", "china", "iran", "war", "ceasefire",
        "nato", "israel", "gaza", "taiwan", "north korea",
    ],
}

# Активные категории для бота — можно переопределить через .env
# Например: ACTIVE_CATEGORIES=crypto,economics
_active_raw = os.getenv("ACTIVE_CATEGORIES", "")
ACTIVE_CATEGORIES: list[str] = (
    [c.strip() for c in _active_raw.split(",") if c.strip()]
    if _active_raw
    else list(CATEGORY_TOPICS.keys())  # все категории по умолчанию
)

# Плоский список топиков для активных категорий
ALLOWED_TOPICS: list = [
    kw for cat in ACTIVE_CATEGORIES for kw in CATEGORY_TOPICS.get(cat, [])
]
