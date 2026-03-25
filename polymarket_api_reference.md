# Polymarket API — Полный справочник для идеального бота

> Источник: https://docs.polymarket.com/api-reference/ (204 страницы, sitemap.xml)
> Дата: 2026-03-20

---

## СОДЕРЖАНИЕ

1. [Архитектура API](#1-архитектура-api)
2. [Python SDK (py-clob-client)](#2-python-sdk-py-clob-client)
3. [Аутентификация](#3-аутентификация)
4. [Gamma API — Рынки и события](#4-gamma-api--рынки-и-события)
5. [CLOB API — Рыночные данные](#5-clob-api--рыночные-данные-без-авторизации)
6. [CLOB API — Торговля](#6-clob-api--торговля-требует-l2-авторизации)
7. [Data API — Позиции и аналитика](#7-data-api--позиции-и-аналитика)
8. [WebSocket — Real-time данные](#8-websocket--real-time-данные)
9. [Rate Limits](#9-rate-limits)
10. [Комиссии и Tick Sizes](#10-комиссии-и-tick-sizes)
11. [Контракты и CTF операции](#11-контракты-polygon-chainid137)
12. [Matching Engine и Error Codes](#12-matching-engine)
13. [Гео-ограничения](#13-гео-ограничения)
14. [Rewards](#14-rewards)
15. [Текущие дыры нашего бота](#15-текущие-дыры-нашего-бота)
16. [План улучшений](#16-план-улучшений)

---

## 1. Архитектура API

| API | Base URL | Авторизация | Для чего |
|-----|----------|-------------|----------|
| **Gamma API** | `https://gamma-api.polymarket.com` | Нет | Рынки, события, поиск, теги |
| **Data API** | `https://data-api.polymarket.com` | Нет | Позиции, сделки, PnL, лидерборд |
| **CLOB API** | `https://clob.polymarket.com` | Публичные — нет; Торговля — да | Ордера, книга ордеров, цены, торговля |
| **Bridge API** | `https://bridge.polymarket.com` | Нет | Депозиты/выводы |

**Staging:** `https://clob-staging.polymarket.com` (для тестирования)

---

## 2. Python SDK (py-clob-client)

### Установка
```bash
pip install py-clob-client
```

### Уровни доступа

| Уровень | Что нужно | Что можно |
|---------|-----------|-----------|
| **L0** (read-only) | Ничего | Цены, книга ордеров, рынки |
| **L1** (key only) | Private key | Создание API ключей |
| **L2** (full auth) | Private key + API creds | Торговля, отмена, позиции |

### Инициализация клиента

```python
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds

# Read-only (L0)
client = ClobClient("https://clob.polymarket.com")

# Full trading (L2)
client = ClobClient(
    host="https://clob.polymarket.com",
    chain_id=137,                    # Polygon mainnet
    key=os.getenv("PRIVATE_KEY"),    # Wallet private key
    creds=ApiCreds(
        api_key=os.getenv("CLOB_API_KEY"),
        api_secret=os.getenv("CLOB_SECRET"),
        api_passphrase=os.getenv("CLOB_PASS_PHRASE"),
    ),
    signature_type=0,   # 0=EOA, 1=Email/Magic, 2=Browser proxy
    funder=None,        # Только если signing key ≠ funded account
)
```

### Генерация API credentials (один раз)

```python
temp_client = ClobClient(
    host="https://clob.polymarket.com",
    chain_id=137,
    key=private_key
)
# Создаёт или находит существующие
creds = temp_client.create_or_derive_api_creds()
# creds = { api_key, api_secret, api_passphrase }

# Потом используем:
client.set_api_creds(creds)
```

### Ключевые типы

```python
from py_clob_client.clob_types import (
    OrderArgs, MarketOrderArgs, OrderType,
    BookParams, OpenOrderParams, TradeParams,
    BalanceAllowanceParams, AssetType, PostOrdersArgs,
    ApiCreds
)
from py_clob_client.order_builder.constants import BUY, SELL
```

```python
# Лимитный ордер
OrderArgs(
    token_id: str,      # CLOB token ID
    price: float,       # 0.00 - 1.00
    size: float,        # Количество shares
    side: str,          # BUY или SELL
    fee_rate_bps: int = 0,
    nonce: int = 0,
    expiration: int = 0,       # Unix timestamp (для GTD)
    taker: str = ZERO_ADDRESS,
)

# Рыночный ордер
MarketOrderArgs(
    token_id: str,
    amount: float,   # BUY → доллары USD; SELL → количество shares
    side: str,
    price: float = 0,          # Worst-price limit (slippage protection)
    fee_rate_bps: int = 0,
    nonce: int = 0,
    taker: str = ZERO_ADDRESS,
    order_type: OrderType = OrderType.FOK,
)
```

### Полный список методов SDK

**Публичные (L0):**
```python
client.get_ok()                          # Health check
client.get_server_time()                 # Серверное время
client.get_markets()                     # Все рынки
client.get_simplified_markets()          # Упрощённый список
client.get_sampling_markets()            # Сэмплированные рынки
client.get_market("condition_id")        # Один рынок

client.get_order_book(token_id)          # Книга ордеров
client.get_order_books([BookParams()])   # Batch книга ордеров

client.get_price(token_id, "BUY")        # Цена покупки
client.get_midpoint(token_id)            # Средняя цена
client.get_midpoints(params)             # Batch средние цены
client.get_spread(token_id)              # Спред
client.get_spreads(params)               # Batch спреды
client.get_last_trade_price(token_id)    # Последняя сделка
client.get_tick_size(token_id)           # Шаг цены
client.get_neg_risk(token_id)            # Neg risk флаг
client.get_fee_rate_bps(token_id)        # Комиссия в bps
client.get_prices_history(params)        # История цен
```

**Торговля (L2):**
```python
# Создать + разместить (всё в одном)
client.create_and_post_order(OrderArgs, options, order_type)
client.create_and_post_market_order(token_id, side, amount, price, options, order_type)

# Или поэтапно:
signed = client.create_order(OrderArgs)
resp = client.post_order(signed, OrderType.GTC)

# Batch (до 15 ордеров)
client.post_orders([PostOrdersArgs(order=..., orderType=..., postOnly=False)])

# Отмена
client.cancel(order_id)
client.cancel_orders([id1, id2])
client.cancel_all()
client.cancel_market_orders(market, asset_id)

# Мои ордера и сделки
client.get_order(order_id)
client.get_orders(OpenOrderParams(market="0x..."))
client.get_trades(TradeParams(maker_address=...))

# Баланс
client.get_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
client.get_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.CONDITIONAL, token_id="..."))

# Allowance (для EOA кошельков, один раз)
client.update_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
client.update_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.CONDITIONAL, token_id="..."))

# Heartbeat (каждые 5 сек!)
client.post_heartbeat(heartbeat_id)

# Уведомления
client.get_notifications()
client.drop_notifications(ids)
```

---

## 3. Аутентификация

### L1 — Создание API ключей (EIP-712)
- Подпись EIP-712 сообщения приватным ключом кошелька
- Domain: `name="ClobAuthDomain"`, `version="1"`, `chainId=137` (Polygon)
- Message: "This message attests that I control the given wallet"
- Headers: `POLY_ADDRESS`, `POLY_SIGNATURE`, `POLY_TIMESTAMP`, `POLY_NONCE`
- Endpoints: `POST /auth/api-key`, `GET /auth/derive-api-key`

### L2 — Торговые операции (HMAC-SHA256)
- Credentials: apiKey, secret, passphrase (получаются через L1)
- Headers: `POLY_API_KEY`, `POLY_ADDRESS`, `POLY_SIGNATURE`, `POLY_PASSPHRASE`, `POLY_TIMESTAMP`

### Builder Authentication (альтернатива)
- Headers: `POLY_BUILDER_API_KEY`, `POLY_BUILDER_PASSPHRASE`, `POLY_BUILDER_SIGNATURE`, `POLY_BUILDER_TIMESTAMP`

### Signature Types
- `0` = EOA (MetaMask, hardware wallets, прямой private key)
- `1` = POLY_PROXY (Email/Magic wallet)
- `2` = GNOSIS_SAFE (Browser proxy)

### Token Allowances (EOA — один раз)
Email/Magic wallet юзерам НЕ нужно. EOA юзеры должны approve эти контракты:
- USDC: `0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174`
- Conditional Tokens: `0x4D97DCd97eC945f40cF65F87097ACe5EA0476045`

Для exchange контрактов:
- `0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E` (Main)
- `0xC5d563A36AE78145C45a50134d48A1215220f80a` (Neg Risk)
- `0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296` (Neg Risk Adapter)

---

## 4. Gamma API — Рынки и события

### GET /markets — Список рынков ✅ (используем)
```
GET https://gamma-api.polymarket.com/markets
```

**Все параметры фильтрации:**
| Параметр | Тип | Описание |
|----------|-----|----------|
| `limit` | int | Количество результатов |
| `offset` | int | Смещение |
| `order` | string | Поле сортировки |
| `ascending` | bool | Направление сортировки |
| `id[]` | string[] | Фильтр по ID |
| `slug[]` | string[] | Фильтр по slug |
| `clob_token_ids[]` | string[] | По CLOB токенам |
| `condition_ids[]` | string[] | По condition ID |
| `liquidity_num_min` | number | Мин. ликвидность |
| `liquidity_num_max` | number | Макс. ликвидность |
| `volume_num_min` | number | Мин. объём |
| `volume_num_max` | number | Макс. объём |
| `start_date_min` | datetime | Дата начала от |
| `start_date_max` | datetime | Дата начала до |
| `end_date_min` | datetime | Дата окончания от |
| `end_date_max` | datetime | Дата окончания до |
| `tag_id` | int | По тегу/категории |
| `closed` | bool | Закрытые |
| `active` | bool | Активные |
| `include_tag` | bool | Включить тег в ответ |
| `rewards_min_size` | number | Мин. размер rewards |
| `uma_resolution_status` | string | Статус UMA resolution |

**Ответ — ключевые поля Market:**
```json
{
  "id": 12345,
  "question": "Will Bitcoin reach $100k by June?",
  "conditionId": "0x...",
  "slug": "will-bitcoin-reach-100k",
  "outcomePrices": "[0.65, 0.35]",
  "volume": 1500000,
  "volume24hr": 50000,
  "volume1wk": 200000,
  "volume1mo": 800000,
  "volume1yr": 1500000,
  "liquidity": 250000,
  "bestBid": 0.64,
  "bestAsk": 0.66,
  "spread": 0.02,
  "lastTradePrice": 0.65,
  "clobTokenIds": "[\"token_yes_id\", \"token_no_id\"]",
  "active": true,
  "closed": false,
  "resolved": false,
  "resolutionPrice": null,
  "resolutionDate": null,
  "endDateIso": "2026-06-30T00:00:00Z",
  "startDateIso": "2026-01-01T00:00:00Z",
  "minimum_tick_size": 0.01,
  "neg_risk": false,
  "tags": [{"label": "Crypto"}]
}
```

### GET /markets/{id} — Конкретный рынок
```
GET https://gamma-api.polymarket.com/markets/{id}?include_tag=true
```

### GET /markets/slug/{slug} — По slug
```
GET https://gamma-api.polymarket.com/markets/slug/will-bitcoin-reach-100k
```

### GET /events — Список событий (рынки сгруппированы)
```
GET https://gamma-api.polymarket.com/events
```
**Параметры:** `limit`, `offset`, `order`, `ascending`, `id[]`, `slug[]`, `tag_id`, `exclude_tag_id[]`, `tag_slug`, `related_tags`, `active`, `archived`, `featured`, `closed`, `include_chat`, `include_template`, `recurrence`, `liquidity_min/max`, `volume_min/max`, `start_date_min/max`, `end_date_min/max`

**Ответ:** массив Event с вложенным `markets[]`

### GET /events/{id} и GET /events/slug/{slug}

### GET /public-search — Полнотекстовый поиск
```
GET https://gamma-api.polymarket.com/public-search?q=bitcoin&limit_per_type=10
```
**Параметры:**
| Параметр | Описание |
|----------|----------|
| `q` | Строка поиска (обязательный) |
| `cache` | Использовать кеш |
| `events_status` | Фильтр статуса |
| `limit_per_type` | Лимит на тип результата |
| `page` | Страница |
| `events_tag[]` | Фильтр по тегам |
| `keep_closed_markets` | Включать закрытые |
| `sort`, `ascending` | Сортировка |

**Ответ:**
```json
{
  "events": [...],
  "tags": [...],
  "profiles": [...],
  "pagination": {"hasMore": true, "totalResults": 150}
}
```

### GET /tags — Все теги/категории
### GET /comments — Комментарии
### GET /public-profile — Профиль пользователя

---

## 5. CLOB API — Рыночные данные (без авторизации)

### GET /book — Книга ордеров ✅ (используем)
```
GET https://clob.polymarket.com/book?token_id=TOKEN_ID
```
**Ответ:**
```json
{
  "market": "condition_id",
  "asset_id": "token_id",
  "timestamp": "1234567890",
  "hash": "0x...",
  "bids": [{"price": "0.64", "size": "1500"}, ...],
  "asks": [{"price": "0.66", "size": "2000"}, ...],
  "min_order_size": "5",
  "tick_size": "0.01",
  "neg_risk": false,
  "last_trade_price": "0.65"
}
```

### POST /books — Batch книг ордеров ⚡ (нужно добавить)
```json
POST https://clob.polymarket.com/books
Body: [
  {"token_id": "id1"},
  {"token_id": "id2", "side": "BUY"}
]
```
**Ответ:** массив OrderBookSummary

### GET /price — Текущая цена
```
GET https://clob.polymarket.com/price?token_id=...&side=BUY
→ {"price": "0.65"}
```

### GET /midpoint — Средняя цена
```
GET https://clob.polymarket.com/midpoint?token_id=...
→ {"mid": "0.65"}
```

### GET /midpoints — Batch средних цен ⚡ (нужно добавить)
```
GET https://clob.polymarket.com/midpoints?token_ids=id1,id2,id3
→ {"id1": "0.65", "id2": "0.42", "id3": "0.78"}
```

### GET /spread — Спред
```
GET https://clob.polymarket.com/spread?token_id=...
→ {"spread": "0.02"}
```

### POST /spreads — Batch спредов ⚡ (нужно добавить)
```json
POST https://clob.polymarket.com/spreads
Body: [{"token_id": "id1"}, {"token_id": "id2"}]
→ {"id1": "0.02", "id2": "0.05"}
```

### GET /prices-history — История цен ✅ (используем)
```
GET https://clob.polymarket.com/prices-history?market={asset_id}&interval=1h&fidelity=1
```
| Параметр | Описание |
|----------|----------|
| `market` | Asset ID (обязательный) |
| `startTs` | Начало диапазона (unix) |
| `endTs` | Конец диапазона (unix) |
| `interval` | `max`/`all`/`1m`/`1w`/`1d`/`6h`/`1h` |
| `fidelity` | Гранулярность в минутах (default 1) |

**Ответ:** `{history: [{t: 1234567890, p: 0.65}, ...]}`

### GET /tick-size
```
GET https://clob.polymarket.com/tick-size?token_id=...
→ {"minimum_tick_size": 0.01}
```
Варианты: `0.1`, `0.01`, `0.001`, `0.0001`

### GET /last-trade-price
```
GET https://clob.polymarket.com/last-trade-price?token_id=...
→ {"price": "0.65", "side": "BUY"}
```
Default `"0.5"` если сделок не было.

### GET /fee-rate
```
GET https://clob.polymarket.com/fee-rate?token_id=...
→ {"base_fee": 0}  // basis points
```

### GET /time — Серверное время
```
GET https://clob.polymarket.com/time
→ 1234567890
```

---

## 6. CLOB API — Торговля (требует L2 авторизации)

### POST /order — Размещение ордера 🔴 (критично добавить)

**Лимитный ордер (GTC) — Python SDK:**
```python
from py_clob_client.clob_types import OrderArgs, OrderType
from py_clob_client.order_builder.constants import BUY, SELL

# Вариант 1: Создать + разместить в одном вызове
resp = client.create_and_post_order(
    OrderArgs(
        token_id="TOKEN_ID",
        price=0.50,       # $0.50 за share
        size=100,         # 100 shares
        side=BUY,
    ),
    options={"tick_size": "0.01", "neg_risk": False},
    order_type=OrderType.GTC,
)

# Вариант 2: Поэтапно
signed_order = client.create_order(
    OrderArgs(token_id="TOKEN_ID", price=0.50, size=100, side=BUY)
)
resp = client.post_order(signed_order, OrderType.GTC)
```

**Рыночный ордер (FOK):**
```python
# BUY — amount в долларах USD
resp = client.create_and_post_market_order(
    token_id="TOKEN_ID",
    side=BUY,
    amount=100,       # $100
    price=0.55,       # Worst-price (slippage protection)
    options={"tick_size": "0.01", "neg_risk": False},
    order_type=OrderType.FOK,
)

# SELL — amount в shares
resp = client.create_and_post_market_order(
    token_id="TOKEN_ID",
    side=SELL,
    amount=100,       # 100 shares
    price=0.45,       # Min price
    options={"tick_size": "0.01", "neg_risk": False},
    order_type=OrderType.FOK,
)
```

**Post-Only ордер (гарантированный maker):**
```python
signed = client.create_order(OrderArgs(price=0.40, size=50, side=BUY, token_id="..."))
resp = client.post_order(signed, orderType=OrderType.GTC, post_only=True)
# Отклоняется если бы пересёк спред!
```

**GTD ордер (с датой истечения):**
```python
import time
expiration = int(time.time()) + 60 + 3600  # ОБЯЗАТЕЛЬНО: +60 сек буфер + время жизни

signed = client.create_order(OrderArgs(
    token_id="...", price=0.50, size=100, side=BUY,
    expiration=str(expiration),
))
resp = client.post_order(signed, OrderType.GTD)
```

**Batch ордера (до 15 штук):**
```python
resp = client.post_orders([
    PostOrdersArgs(
        order=client.create_order(OrderArgs(price=0.5, size=100, side=BUY, token_id="id1")),
        orderType=OrderType.GTC,
        postOnly=False,
    ),
    PostOrdersArgs(
        order=client.create_order(OrderArgs(price=0.3, size=200, side=BUY, token_id="id2")),
        orderType=OrderType.GTC,
        postOnly=False,
    ),
])
```

**Типы ордеров:**
| Тип | Описание | Post-Only |
|-----|----------|-----------|
| `GTC` | Висит пока не исполнится/отменят | ✅ |
| `GTD` | Автоотмена после expiration (мин. +60 сек от now) | ✅ |
| `FOK` | Fill-Or-Kill: целиком или ничего | ❌ |
| `FAK` | Fill-And-Kill: что можно, остаток отменяется | ❌ |

**Ответ:**
```json
{
  "success": true,
  "orderID": "0x...",
  "status": "live",       // "live" | "matched" | "delayed"
  "makingAmount": "50000000",
  "takingAmount": "100000000",
  "transactionsHashes": ["0x..."],
  "tradeIDs": ["trade_1"],
  "errorMsg": ""
}
```

### DELETE /order — Отмена ордера
```python
client.cancel(order_id="0xaaaa")
```

### DELETE /orders — Batch отмена (макс. 3000)
```python
client.cancel_orders(["id1", "id2", "id3"])
```

### DELETE /cancel-all — Отменить все
```python
client.cancel_all()
```

### DELETE /cancel-market-orders — Отменить по рынку
```python
client.cancel_market_orders(market="condition_id", asset_id="token_id")
```

### GET /orders — Мои ордера
```python
orders = client.get_orders(OpenOrderParams(market="0x...condition_id"))
# Ответ: {limit, next_cursor, count, data: [OpenOrder]}
```

**OpenOrder:**
```json
{
  "id": "order_id",
  "status": "live",
  "owner": "uuid",
  "maker_address": "0x...",
  "market": "condition_id",
  "asset_id": "token_id",
  "side": "BUY",
  "original_size": "100",
  "size_matched": "50",
  "price": "0.50",
  "outcome": "Yes",
  "expiration": "0",
  "order_type": "GTC",
  "associate_trades": ["trade_1"],
  "created_at": "2026-03-20T12:00:00Z"
}
```

### GET /trades — Мои сделки (CLOB)
```python
trades = client.get_trades(TradeParams(maker_address=client.get_address()))
```
**Trade statuses:** `MATCHED` → `MINED` → `CONFIRMED` (успех) или `RETRYING` → `FAILED`

### POST /heartbeats — Heartbeat сессии ⚠️ КРИТИЧНО
```python
import time

heartbeat_id = ""
while True:
    resp = client.post_heartbeat(heartbeat_id)
    heartbeat_id = resp["heartbeat_id"]
    time.sleep(5)  # Каждые 5 сек!
```
**БЕЗ heartbeat в течение 10 сек (+5 сек буфер) — ВСЕ ордера автоматически отменяются!**

---

## 7. Data API — Позиции и аналитика

### GET /positions — Текущие позиции 🔴 (критично добавить)
```
GET https://data-api.polymarket.com/positions?user={address}
```
| Параметр | Описание |
|----------|----------|
| `user` | Адрес кошелька (обязательный) |
| `market[]` | Фильтр по condition ID |
| `eventId[]` | Фильтр по event ID |
| `sizeThreshold` | Мин. размер (default 1) |
| `redeemable` | Только redeemable |
| `mergeable` | Только mergeable |
| `limit` | Max 500 |
| `offset` | Max 10000 |
| `sortBy` | CURRENT/INITIAL/TOKENS/CASHPNL/PERCENTPNL/TITLE/RESOLVING/PRICE/AVGPRICE |
| `sortDirection` | ASC/DESC |
| `title` | Поиск по названию |

**Ответ:**
```json
{
  "proxyWallet": "0x...",
  "asset": "token_id",
  "conditionId": "0x...",
  "size": "100",
  "avgPrice": "0.45",
  "initialValue": "45.00",
  "currentValue": "65.00",
  "cashPnl": "20.00",
  "percentPnl": "44.4",
  "totalBought": "100",
  "realizedPnl": "0",
  "curPrice": "0.65",
  "redeemable": false,
  "mergeable": false,
  "title": "Will Bitcoin reach $100k?",
  "slug": "will-bitcoin-reach-100k",
  "outcome": "Yes",
  "outcomeIndex": 0,
  "negativeRisk": false
}
```

### GET /closed-positions — Закрытые позиции
```
GET https://data-api.polymarket.com/closed-positions?user={address}&limit=50
```
`sortBy`: REALIZEDPNL/TITLE/PRICE/AVGPRICE/TIMESTAMP

### GET /trades — История сделок (Data API)
```
GET https://data-api.polymarket.com/trades?user={address}&limit=100
```
| Параметр | Описание |
|----------|----------|
| `limit` | Max 10000 |
| `takerOnly` | Default true |
| `filterType` | CASH или TOKENS |
| `filterAmount` | Мин. сумма |
| `market[]` | По condition ID |
| `eventId[]` | По event ID |
| `side` | BUY/SELL |

### GET /activity — Полная активность
```
GET https://data-api.polymarket.com/activity?user={address}
```
**Типы:** TRADE, SPLIT, MERGE, REDEEM, REWARD, CONVERSION, MAKER_REBATE

### GET /value — Общая стоимость портфеля
```
GET https://data-api.polymarket.com/value?user={address}
```

### GET /oi — Open Interest ⚡ (полезный сигнал)
```
GET https://data-api.polymarket.com/oi?market[]={conditionId}
```

### GET /live-volume — Live объём ⚡ (полезный сигнал)
```
GET https://data-api.polymarket.com/live-volume?id={eventId}
```

### GET /holders — Крупные держатели ⚡ (smart money сигнал)
```
GET https://data-api.polymarket.com/holders?market[]={conditionId}&limit=20&minBalance=1000
```

### GET /v1/leaderboard — Лидерборд ⚡ (copy-trading)
```
GET https://data-api.polymarket.com/v1/leaderboard?category=CRYPTO&timePeriod=WEEK&orderBy=PNL&limit=50
```
Категории: OVERALL, POLITICS, SPORTS, CRYPTO, CULTURE, и др.

### GET /v1/market-positions — Позиции по рынку
```
GET https://data-api.polymarket.com/v1/market-positions?market={conditionId}&status=OPEN&limit=500
```

---

## 8. WebSocket — Real-time данные

### Market Channel (публичный) ⚡ (нужно добавить)
```
wss://ws-subscriptions-clob.polymarket.com/ws/market
```

**Подписка:**
```json
{
  "assets_ids": ["token_id_1", "token_id_2"],
  "type": "market",
  "initial_dump": true,
  "level": 2,
  "custom_feature_enabled": true
}
```

**События:**
| Событие | Описание | Зачем нам |
|---------|----------|-----------|
| `book` | Обновление книги ордеров | Real-time спред без polling |
| `price_change` | Изменение цены | Мгновенная реакция на движение |
| `last_trade_price` | Последняя сделка | Отслеживание активности |
| `tick_size_change` | Изменение шага цены | Корректировка ордеров |
| `best_bid_ask` | Лучшие bid/ask | Точный спред |
| `new_market` | Новый рынок | Быстрый вход в новые рынки |
| `market_resolved` | Резолюция | Мгновенный трекинг outcome |

**Heartbeat:** отправлять `"PING"` каждые 10 сек, получать `"PONG"`

### User Channel (авторизованный)
```
wss://ws-subscriptions-clob.polymarket.com/ws/user
```

**Первое сообщение (авторизация):**
```json
{
  "auth": {
    "apiKey": "...",
    "secret": "...",
    "passphrase": "..."
  },
  "type": "user"
}
```

**Подписка на рынки:**
```json
{
  "markets": ["condition_id_1", "condition_id_2"],
  "type": "user"
}
```

**События:**
| Событие | Описание |
|---------|----------|
| `order` PLACEMENT | Ордер размещён |
| `order` UPDATE | Ордер частично исполнен |
| `order` CANCELLATION | Ордер отменён |
| `trade` MATCHED | Сделка матчнута |
| `trade` MINED | Транзакция в блоке |
| `trade` CONFIRMED | Финальное подтверждение |
| `trade` RETRYING | Повторная попытка |
| `trade` FAILED | Сделка не удалась |

### Sports Channel
```
wss://sports-api.polymarket.com/ws
```
Heartbeat: сервер шлёт `"ping"` каждые 5 сек, отвечать `"pong"` в течение 10 сек.

### RTDS (Real-Time Data Socket)
```
wss://ws-live-data.polymarket.com
```
Стримит комментарии и крипто-цены.

---

## 9. Rate Limits

### Общий лимит: 15,000 req / 10 сек

### Gamma API (за 10 секунд)
| Endpoint | Лимит |
|----------|-------|
| Общий | 4,000 |
| `/events` | 500 |
| `/markets` | **300** |
| `/comments` | 200 |
| `/tags` | 200 |
| `/public-search` | 350 |

### Data API (за 10 секунд)
| Endpoint | Лимит |
|----------|-------|
| Общий | 1,000 |
| `/trades` | 200 |
| `/positions` | 150 |
| `/closed-positions` | 150 |

### CLOB Market Data (за 10 секунд)
| Endpoint | Лимит |
|----------|-------|
| `/book` | 1,500 |
| `/books` (batch) | 500 |
| `/price` | 1,500 |
| `/prices` (batch) | 500 |
| `/midpoint` | 1,500 |
| `/midpoints` (batch) | 500 |
| `/prices-history` | 1,000 |
| Общий CLOB | 9,000 |

### CLOB Trading (burst / sustained per 10 min)
| Endpoint | Burst / 10s | Sustained / 10min |
|----------|-------------|-------------------|
| `POST /order` | 3,500 | 36,000 |
| `DELETE /order` | 3,000 | 30,000 |
| `POST /orders` (batch) | 1,000 | 15,000 |
| `DELETE /orders` (batch) | 1,000 | 15,000 |
| `DELETE /cancel-all` | 250 | 6,000 |

### Relayer
- `/submit`: 25 req/min

> **Важно:** Rate limits через Cloudflare — запросы ставятся в очередь (throttling), НЕ отклоняются (no 429). HTTP headers с лимитами не возвращаются.

---

## 10. Комиссии и Tick Sizes

### Комиссии
| Тип рынка | feeRate | exponent | Макс. комиссия (при p=50%) | Maker rebate |
|-----------|---------|----------|---------------------------|--------------|
| Большинство | **0** | — | **0%** | — |
| Crypto | 0.25 | 2 | 1.56% | 20% |
| Sports (NCAAB, Serie A) | 0.0175 | 1 | 0.44% | 25% |

**Формула:** `fee = C * p * feeRate * (p * (1-p))^exponent`

- Комиссии собираются в shares (покупка) или USDC (продажа)
- Минимум: 0.0001 USDC

### Tick Sizes (шаг цены)
| Значение | Используется |
|----------|-------------|
| `0.1` | Низколиквидные рынки |
| `0.01` | Стандартные рынки |
| `0.001` | Высоколиквидные |
| `0.0001` | Ультраликвидные |

**ВАЖНО:** Каждый ордер ОБЯЗАН включать `tick_size` и `neg_risk`! Получить из `minimum_tick_size` поля рынка или через `GET /tick-size`.

### Neg Risk рынки
Рынки с 3+ outcomes используют другой exchange контракт. Проверять через `neg_risk` поле.

---

## 11. Контракты (Polygon, chainId=137)

| Контракт | Адрес |
|----------|-------|
| CTF Exchange | `0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E` |
| Neg Risk CTF Exchange | `0xC5d563A36AE78145C45a50134d48A1215220f80a` |
| Neg Risk Adapter | `0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296` |
| Conditional Tokens | `0x4D97DCd97eC945f40cF65F87097ACe5EA0476045` |
| USDC.e (6 decimals) | `0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174` |
| UMA Adapter | `0x6A9D222616C90FcA5754cd1333cFD9b7fb6a4F74` |
| UMA Optimistic Oracle | `0xCB1822859cEF82Cd2Eb4E6276C7916e692995130` |
| Safe Factory | `0xaacfeea03eb1561c4e67d661e40682bd20e3541b` |
| Proxy Factory | `0xaB45c5A4B0c941a2F231C04C3f49182e1A254052` |

### CTF Операции (Split/Merge/Redeem)

**Split** — $1000 USDC.e → 1000 YES + 1000 NO tokens:
```
splitPosition(collateralToken, parentCollectionId=0x0, conditionId, partition=[1,2], amount)
```

**Merge** — 500 YES + 500 NO → $500 USDC.e:
```
mergePositions(collateralToken, parentCollectionId=0x0, conditionId, partition=[1,2], amount)
```

**Redeem** — Выкуп выигрышных токенов после resolution:
```
redeemPositions(collateralToken, parentCollectionId=0x0, conditionId, indexSets=[1,2])
```
- Сжигает ВСЕ токены, платит $1 за выигрышный
- Без дедлайна — redeemable бессрочно

### Resolution Process (UMA)
1. Propose outcome + ~$750 bond
2. 2-часовой challenge period
3. Если оспорено → новый proposal или 48-часовое DVM голосование
4. Без оспаривания: ~2 часа
5. С одним оспариванием: 4-6 дней

---

## 12. Matching Engine

- **Перезапуск:** каждый вторник 7:00 AM ET (~90 сек downtime)
- HTTP `425 Too Early` во время рестарта → exponential backoff
- Внеплановые рестарты возможны для critical updates
- Если spread > $0.10 → отображается last trade price вместо midpoint

### Error Codes

| Код | Описание | Что делать |
|-----|----------|-----------|
| `401` | Invalid API key / L1 headers | Проверить credentials |
| `425` | Matching engine restarting | Exponential backoff, retry через 5-30 сек |
| `429` | Rate limit exceeded | Замедлить запросы |
| `503` | Trading disabled / cancel-only | Не торговать, только отмена |

### Order Error Codes
| Ошибка | Описание |
|--------|----------|
| `INVALID_ORDER_MIN_TICK_SIZE` | Цена не кратна tick size |
| `INVALID_ORDER_MIN_SIZE` | Слишком маленький ордер |
| `INVALID_ORDER_NOT_ENOUGH_BALANCE` | Недостаточно средств |
| `FOK_ORDER_NOT_FILLED_ERROR` | FOK не может быть полностью исполнен |
| `INVALID_POST_ONLY_ORDER` | Post-only пересёк бы спред |
| `MARKET_NOT_READY` | Рынок не принимает ордера |

---

## 13. Гео-ограничения

**Полная блокировка (33 страны):** US, UK, France, Germany, Italy, Netherlands, Belgium, Australia, Russia, Iran, Cuba, North Korea и др.

**Close-only (4 страны):** Poland, Singapore, Thailand, Taiwan

**Региональные:** Ontario (Canada), Crimea/Donetsk/Luhansk (Ukraine)

**Проверка:**
```
GET https://polymarket.com/api/geoblock
→ {"blocked": true/false, "ip": "...", "country": "...", "region": "..."}
```

---

## 14. Rewards

### Holding Rewards
- **4.00% годовых** на total position value в eligible markets
- Распределяются ежедневно, sampling каждый час

### Liquidity Rewards (Maker Rewards)
- Квадратичная формула: `S(v,s) = ((v-s)/v)^2 * b`
  - v = max spread, s = actual spread, b = multiplier
- Two-sided quoting сильно поощряется (single-sided = 1/3 credit)
- 10,080 одноминутных samples за epoch
- Минимальная выплата: $1

### Builder Program
| Уровень | Relayer txns/day | Доступ |
|---------|-----------------|--------|
| Unverified | 100 | Сразу |
| Verified | 3,000 | Заявка на builder@polymarket.com |
| Partner | Unlimited | Highest rate limits, early features |

---

## 15. Текущие дыры нашего бота

### 🔴 КРИТИЧНЫЕ

**1. Нет реальной торговли** (`polymarket_client.py:340-345`)
- `place_bet()` → `raise NotImplementedError`
- Бот работает ТОЛЬКО в DRY_RUN

**2. Silent failures на API ошибках** (`polymarket_client.py:256-267, 273-286`)
- `get_price_change_1h()` возвращает 0.0 при любой ошибке
- `get_spread()` возвращает 1.0 при ошибке
- Невозможно отличить "нет данных" от "API упал"

**3. Нет таймаутов на API вызовы** (`main.py:80-85`)
- aiohttp default = 5 мин — может заблокировать весь цикл
- Один зависший запрос стопорит анализ всех 10 рынков

**4. Нет retry logic** (все файлы)
- Если Gamma API вернёт 500 — рынки не загрузятся, цикл пропущен
- Нет circuit breaker для rate-limited endpoints

### 🟡 ВАЖНЫЕ

**5. Неэффективная загрузка рынков** (`polymarket_client.py:152-176`)
- Загружает 500 рынков, потом фильтрует до 10
- Надо: использовать `volume_num_min`, `liquidity_num_min` в запросе

**6. Последовательные запросы** (`main.py:191-192`)
- `get_spread()` вызывается по одному для каждого рынка
- Надо: использовать `POST /spreads` batch endpoint

**7. CoinGecko запрос на каждый цикл** (`polymarket_client.py:297-316`)
- Даже для не-crypto рынков
- Нет кеширования crypto данных

**8. Книга ордеров не анализируется** (`polymarket_client.py:274-286`)
- Берётся только spread, но:
  - Нет volume-weighted midprice
  - Нет order flow imbalance (bid vs ask volume)
  - Нет depth analysis (ликвидность на разных уровнях)

**9. Kelly не учитывает корреляцию** (`strategy.py:177-178`)
- Формула: `1 / (1 + open_bets * 0.1)` — линейная
- Если 3 бета на crypto — все коррелированы, но бот этого не видит

**10. Claude промпт слабый** (`strategy.py:70-93`)
- Нет инструкций по весу volume/liquidity
- Fear & Greed Index guidance примитивное
- Нет обработки коррелированных рынков
- Правило "market <10% → max 25%" произвольное

**11. Resolution tracker неполный** (`outcome_tracker.py:232`)
- Только binary: `resolutionPrice >= 0.99` = YES
- Нет partial resolution
- Нет проверки "disputed" / "pending review" статуса
- 1-часовой cooldown слишком долгий

**12. База данных** (`database.py:40, 156-163`)
- Нет индексов на condition_id, timestamp
- `load_bets()` загружает ВСЕ ставки каждый цикл
- Нет retention policy — растёт бесконечно

**13. Нет трекинга точности Claude** (нигде)
- Не отслеживаем: Claude сказал 70%, рынок резолвнулся в YES/NO
- Нельзя динамически настроить MIN_EDGE по историческим данным

### 🟢 УЛУЧШЕНИЯ

**14. Нет WebSocket** — polling каждые 5 минут вместо real-time
**15. Нет /balance и /portfolio команд** в Telegram
**16. Нет per-category MIN_EDGE** (crypto vs politics могут требовать разный edge)
**17. Нет Sharpe ratio / max drawdown** метрик
**18. Новости не ранжируются по свежести** — старая новость = свежая
**19. Нет smart money анализа** (holders/leaderboard endpoints)

---

## 16. План улучшений

### Фаза 1: Стабильность и эффективность
| # | Задача | Файл | Что даст |
|---|--------|------|----------|
| 1 | Таймауты на все API вызовы (10-30 сек) | все | Бот не зависнет |
| 2 | Retry с exponential backoff | все | Устойчивость к сбоям API |
| 3 | Batch запросы (spreads, midpoints, books) | polymarket_client.py | Быстрее в 5-10x |
| 4 | Серверная фильтрация рынков (volume_num_min, etc.) | polymarket_client.py | Меньше трафика |
| 5 | Индексы в SQLite + pagination | database.py | Быстрые запросы |
| 6 | Кеширование CoinGecko (5 мин) | polymarket_client.py | Меньше API вызовов |

### Фаза 2: Реальная торговля
| # | Задача | Файл | Что даст |
|---|--------|------|----------|
| 7 | py-clob-client интеграция | polymarket_client.py | Реальные ордера |
| 8 | Аутентификация L1/L2 | config.py, polymarket_client.py | Доступ к торговле |
| 9 | Market order (FOK) для быстрого входа | polymarket_client.py | Гарантированное исполнение |
| 10 | Limit order (GTC/GTD) для лучшей цены | polymarket_client.py | Меньше slippage |
| 11 | Heartbeat loop (5 сек) | main.py | Ордера не отменятся |
| 12 | Balance/allowance проверка перед ордером | polymarket_client.py | Нет ошибок "not enough balance" |
| 13 | Fee calculation в Kelly formula | strategy.py | Точный sizing |

### Фаза 3: Data-driven стратегия
| # | Задача | Файл | Что даст |
|---|--------|------|----------|
| 14 | Order book depth analysis | polymarket_client.py | Volume-weighted signals |
| 15 | Open Interest как сигнал | polymarket_client.py | Дополнительный edge |
| 16 | Holders/smart money tracking | новый модуль | Copy-trading сигналы |
| 17 | Трекинг точности Claude | strategy.py, database.py | Динамический MIN_EDGE |
| 18 | Корреляционный анализ позиций | strategy.py | Лучший risk management |
| 19 | Per-category параметры | config.py, strategy.py | Оптимизация по категориям |
| 20 | Улучшенный Claude prompt | strategy.py | Лучше predictions |

### Фаза 4: Real-time + полный мониторинг
| # | Задача | Файл | Что даст |
|---|--------|------|----------|
| 21 | WebSocket market channel | новый модуль | Real-time цены |
| 22 | WebSocket user channel | новый модуль | Real-time статус ордеров |
| 23 | Позиции через Data API | polymarket_client.py | Реальный PnL трекинг |
| 24 | /balance, /portfolio Telegram | notifier.py | Мониторинг из телефона |
| 25 | Sharpe ratio, max drawdown | outcome_tracker.py | Полная аналитика |
| 26 | Автоматический redeem | polymarket_client.py | Не забыть забрать выигрыш |
| 27 | market_resolved WebSocket событие | новый модуль | Мгновенный outcome tracking |

### Фаза 5: Advanced
| # | Задача | Что даст |
|---|--------|----------|
| 28 | Leaderboard copy-trading | Следование за топ-трейдерами |
| 29 | Live volume monitoring | Детекция unusual activity |
| 30 | News freshness weighting | Свежие новости > старые |
| 31 | A/B тестирование промптов | Оптимизация Claude |
| 32 | Dynamic Kelly fraction | Адаптация к performance |
| 33 | Auto-deposit через Bridge API | Автопополнение баланса |

---

## GraphQL Subgraphs (для deep analytics)

| Subgraph | Endpoint |
|----------|----------|
| Positions | `https://api.goldsky.com/.../positions-subgraph/0.0.7/gn` |
| Orders | `https://api.goldsky.com/.../orderbook-subgraph/0.0.1/gn` |
| Activity | `https://api.goldsky.com/.../activity-subgraph/0.0.4/gn` |
| Open Interest | `https://api.goldsky.com/.../oi-subgraph/0.0.6/gn` |
| PNL | `https://api.goldsky.com/.../pnl-subgraph/0.0.14/gn` |

---

## Gasless Transactions (Builder/Relayer)

```bash
pip install py-builder-relayer-client py-builder-signing-sdk
```

RelayClient обрабатывает gas для: wallet deployment, token approvals, CTF split/merge/redeem, transfers.

**Статусы:** STATE_NEW → STATE_EXECUTED → STATE_MINED → STATE_CONFIRMED (или STATE_FAILED)

---

## Sports Markets (особенности)

- Outstanding limit orders **автоматически отменяются при начале игры**
- Marketable orders имеют **3-секундную задержку** при размещении
- Время начала игры может сдвинуться без предупреждения
