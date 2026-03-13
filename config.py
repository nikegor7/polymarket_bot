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
TOP_MARKETS: int = 10
MIN_MARKET_VOLUME: int = 1000
NEWS_CACHE_TTL: int = 14400  # 4 часа — вписываемся в 100 req/day GNews
