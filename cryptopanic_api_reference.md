# CryptoPanic API Reference

## 1. Base URL

**v2 (актуальная):**
```
https://cryptopanic.com/api/{API_PLAN}/v2/posts/
```
Где `{API_PLAN}` — slug плана: `free`, `developer`, `pro`, `enterprise`.

**v1 (legacy, используется в боте сейчас):**
```
https://cryptopanic.com/api/v1/posts/
```

## 2. Аутентификация

- Query parameter: `auth_token=YOUR_TOKEN`
- Получить токен: https://cryptopanic.com/developers/api/keys
- Регистрация бесплатная
- `public=true` — публичный (неперсонализированный) режим

## 3. Параметры `/posts/`

### Доступны на всех планах:

| Параметр | Тип | Значения | Описание |
|----------|-----|----------|----------|
| `auth_token` | string | (обязательный) | API токен |
| `public` | bool | `true`/`false` | Публичный режим |
| `currencies` | string | `BTC,ETH,SOL` | Фильтр по крипте (через запятую) |
| `regions` | string | `en,de,ru,es,fr,...` | Фильтр по языку |
| `filter` | string | `rising`, `hot`, `bullish`, `bearish`, `important`, `saved`, `lol` | Фильтр по категории |
| `kind` | string | `news`, `media`, `all` (default) | Тип контента |
| `format` | string | `rss` | RSS формат, макс 20 записей |

### Только ENTERPRISE:

| Параметр | Тип | Описание |
|----------|-----|----------|
| `search` | string | Поиск по ключевому слову |
| `size` | int (1-500) | Количество записей на страницу |
| `with_content` | bool | Включить полный текст статьи |
| `last_pull` | ISO 8601 | Только записи после этой даты |
| `panic_period` | `1h`, `6h`, `24h` | Включить panic score за период |
| `panic_sort` | `asc`, `desc` | Сортировка по panic score |

## 4. Формат ответа

**Верхний уровень:**
```json
{
  "next": "https://cryptopanic.com/api/.../posts/?cursor=...",
  "previous": null,
  "results": [...]
}
```

**Объект поста:**
```json
{
  "id": 12345678,
  "slug": "bitcoin-breaks-60k",
  "title": "Bitcoin Breaks $60k",
  "description": "Short summary",
  "published_at": "2026-03-21T10:30:00Z",
  "created_at": "2026-03-21T10:31:00Z",
  "kind": "news",
  "source": {
    "title": "CoinDesk",
    "region": "en",
    "domain": "coindesk.com",
    "type": "feed"
  },
  "original_url": "https://...",
  "url": "https://cryptopanic.com/news/...",
  "instruments": [
    {
      "code": "BTC",
      "title": "Bitcoin",
      "slug": "bitcoin",
      "market_cap_usd": 1200000000000.0,
      "price_in_usd": 62500.0,
      "market_rank": 1
    }
  ],
  "votes": {
    "negative": 3,
    "positive": 15,
    "important": 8,
    "liked": 12,
    "disliked": 2,
    "lol": 0,
    "toxic": 0,
    "saved": 5,
    "comments": 7
  },
  "panic_score": 72,
  "panic_score_1h": 85
}
```

**Source `type`:** `feed`, `blog`, `twitter`, `media`, `reddit`
**Post `kind`:** `news`, `media`, `blog`, `twitter`, `reddit`

## 5. Пагинация

- Курсорная (не по номерам страниц)
- `next` и `previous` в ответе — URL следующей/предыдущей страницы

## 6. Rate Limits

| План | Лимит |
|------|-------|
| Free | ~100 запросов/день |
| PRO | Выше (не документировано публично) |

HTTP `429` — rate limit. `403` — тоже может означать лимит.

## 7. Планы и цены

| План | Цена | Особенности |
|------|------|-------------|
| **Free** | $0 | Базовый доступ, стандартные фильтры |
| **PRO** | ~$29.99/мес | Больше запросов, `following` фильтр |
| **GROWTH** | Выше | Portfolio endpoint |
| **ENTERPRISE** | Custom | `search`, `size`, `with_content`, `panic_period`, до 500/страницу |

> CryptoPanic PRO (подписка сайта $9/мес) — **отдельно** от API планов.

## 8. HTTP коды ошибок

| Код | Значение |
|-----|----------|
| 200 | Успех |
| 401 | Невалидный `auth_token` |
| 403 | Запрещено / rate limit |
| 429 | Rate limit |
| 500 | Серверная ошибка |

## 9. v1 vs v2

- **v1:** `https://cryptopanic.com/api/v1/posts/` — используется в боте сейчас
- **v2:** `https://cryptopanic.com/api/{API_PLAN}/v2/posts/` — текущая версия, требует plan slug в URL
- v2 добавляет: `panic_score`, `panic_score_1h`, расширенные `instruments` (цены, market_rank), объект `content`
- v1 может быть deprecated

## 10. Языки (regions)

`en`, `de`, `nl`, `es`, `fr`, `it`, `pt`, `ru`, `tr`, `ar`, `zh`, `ja`, `ko`

## 11. Текущее использование в боте

Файл `core/news_monitor.py:14`:
```python
CRYPTOPANIC_BASE = "https://cryptopanic.com/api/v1/posts/"
```
Параметры: `auth_token`, `currencies`, `filter=rising`, `public=true`

Поля из ответа: `results[].title`, `results[].votes.positive`, `results[].votes.negative`, `results[].source.title`, `results[].published_at`

## 12. Что нужно пофиксить

API v1 возвращает 404. Нужно перейти на v2:
```python
CRYPTOPANIC_BASE = "https://cryptopanic.com/api/free/v2/posts/"
```
Или с планом:
```python
CRYPTOPANIC_BASE = f"https://cryptopanic.com/api/{PLAN}/v2/posts/"
```
