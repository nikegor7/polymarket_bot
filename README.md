# Polymarket Prediction Bot

An automated prediction market bot that analyzes [Polymarket](https://polymarket.com) markets using news sentiment, on-chain signals, and Claude AI to identify profitable betting opportunities.

## How It Works

The bot runs in continuous cycles:

1. **Market Discovery** — Fetches active markets from Polymarket's Gamma API, filtered by volume, category, and time-to-close. Supports both regular and daily (short-timeframe) modes.

2. **Signal Collection** — For each market, gathers data in parallel:
   - **News** — from CryptoPanic, Tavily, and GNews (priority fallback chain)
   - **Price signals** — multi-timeframe (1h / 24h / 7d) price changes and volatility from CLOB API
   - **Order book** — batch-fetched via `POST /books`, analyzed for VWAP, bid/ask imbalance, and depth
   - **Data API** — open interest, live volume, and smart money (large holder bias)
   - **Crypto signals** — CoinGecko prices for crypto markets + Fear & Greed index

3. **AI Analysis** — Claude evaluates each market with all collected signals, news articles (tagged as BREAKING / RECENT / OLD), and category-specific rules. Returns a structured JSON decision with probability estimate, confidence, and reasoning.

4. **Strategy** — Kelly criterion sizing with per-category minimum edge thresholds (crypto: 6%, politics: 4%, geopolitics: 7%), correlated position scaling, and real fee rates from the API.

5. **Execution** — In DRY RUN mode, logs hypothetical bets to SQLite. Live execution via CLOB API is implemented but blocked by geo-restrictions.

6. **Outcome Tracking** — Monitors resolved markets, calculates hypothetical P&L, Sharpe ratio, max drawdown, Brier score calibration, and per-category win rates.

## Architecture

```
main.py                  — Main loop, parallel market analysis, cycle orchestration
config.py                — All configuration via environment variables
core/
  polymarket_client.py   — Gamma API, CLOB API, Data API, CoinGecko, rate budget
  news_monitor.py        — CryptoPanic / Tavily / GNews with smart caching
  strategy.py            — Claude AI evaluation, Kelly sizing, category detection
  database.py            — SQLite storage for bets and outcomes
  outcome_tracker.py     — Resolution tracking, P&L, calibration metrics
  notifier.py            — Telegram bot (notifications + interactive commands)
  logger.py              — Decision logging and summary stats
  backtester.py          — Backtesting module for historical analysis
dashboard/
  app.py                 — Streamlit dashboard for visualization
tests/                   — pytest test suite
```

## Setup

### Prerequisites

- Python 3.11+
- API keys: Anthropic (Claude), GNews

### Installation

```bash
git clone https://github.com/nikegor7/polymarket_bot.git
cd polymarket_bot
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### Configuration

Create a `.env` file:

```env
# Required
ANTHROPIC_API_KEY=sk-ant-...
GNEWS_API_KEY=...

# Optional — enhanced signals
TAVILY_API_KEY=tvly-...
CRYPTOPANIC_API_KEY=...
CRYPTOPANIC_PLAN=free          # free, developer, pro, enterprise

# Optional — Telegram notifications
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...

# Mode
DRY_RUN=true                   # false for live trading (requires VPN + wallet)
DAILY_MODE=false               # true for short-timeframe markets only
BUDGET=100
MAX_BET=5

# Tuning
CLAUDE_MODEL=claude-haiku-4-5-20251001
TOP_MARKETS=10
ACTIVE_CATEGORIES=crypto,politics,economics,tech,geopolitics
MAX_DAYS_TO_CLOSE=0            # 0 = no limit
```

See `config.py` for all available options.

### Run

```bash
python main.py
```

## Telegram Commands

| Command | Description |
|---------|-------------|
| `/stats` | P&L, win rate, ROI, Sharpe ratio |
| `/last` | Recent bets with edge and amounts |
| `/accuracy` | Brier score, per-category win rates |
| `/config` | Current bot configuration |
| `/help` | Available commands |

## Key Features

- **Rate limit budget** — Sliding window tracking per API category (Gamma, CLOB, Data API) to stay within Polymarket's limits without getting throttled
- **Retry with backoff** — Automatic retry on 425 (engine restart), 5xx errors, and timeouts
- **Smart caching** — Separate TTLs for markets (15min), crypto prices (5min), fees (30min), and Data API signals (10min)
- **Batch orderbooks** — Single `POST /books` request instead of N individual fetches
- **News freshness** — Articles sorted by date with BREAKING/RECENT/OLD labels that influence Claude's analysis
- **Correlated Kelly** — Position sizing accounts for exposure in the same category
- **Geo-block check** — Detects IP restrictions at startup

## Disclaimer

This bot is for educational and research purposes. Prediction market trading involves significant risk. The bot defaults to DRY RUN mode — no real money is risked unless explicitly configured. Always comply with local regulations regarding prediction markets.
