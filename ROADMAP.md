# Polymarket Prediction Bot — ROADMAP

> Цель: построить аналитический движок предсказаний с измеримой точностью.
> Торговля заблокирована по гео — фокус на calibration и анализе.
> Когда ограничение снимется — execution layer уже готов.
>
> Прогресс → PROGRESS.txt

---

## Миссия

Систематически предсказывать исходы рынков предсказаний точнее толпы.
Измерять точность через калибровку. Масштабировать то что работает.

**Текущее ограничение:** геоблок Polymarket → только анализ и симуляция.
**Ценность всё равно есть:** доказанный edge = основа для монетизации в будущем.

---

## Архитектура проекта

### Структура файлов

```
polymarket_bot/
├── core/                        # бизнес-логика
│   ├── polymarket_client.py     # Polymarket API (рынки, цены, CLOB)
│   ├── news_monitor.py          # новости + кэш
│   ├── strategy.py              # Claude анализ + Kelly criterion
│   ├── logger.py                # запись решений
│   └── outcome_tracker.py      # [Phase 3] отслеживание исходов
│
├── dashboard/                   # [Phase 4] Streamlit UI
│   ├── app.py                   # точка входа
│   └── pages/
│       ├── 01_overview.py       # KPI: win rate, P&L, calibration
│       ├── 02_bets.py           # таблица ставок + фильтры
│       ├── 03_markets.py        # браузер рынков с ценами
│       └── 04_calibration.py    # график: наша prob vs фактический исход
│
├── data/                        # данные (в .gitignore)
│   ├── bet_history.json         # история решений бота
│   ├── news_cache.json          # кэш новостей
│   └── outcomes.json            # [Phase 3] фактические исходы
│
├── config.py                    # все настройки
├── main.py                      # главный цикл бота
├── requirements.txt
├── .env                         # секреты — не коммитить
├── .gitignore
├── ROADMAP.md                   # этот файл
└── PROGRESS.txt                 # лог выполненного
```

### Поток данных

```
Polymarket API                GNews / News API
      │                              │
      ▼                              ▼
polymarket_client.py ──────► news_monitor.py
  get_markets()                get_news()
  get_daily_markets()
  get_price_change_1h()              │
  get_spread()                       ▼
      │                        strategy.py
      │                     evaluate(market, news)
      │                       → probability
      │                       → edge YES/NO
      │                       → kelly bet
      │                              │
      ▼                              ▼
   main.py ◄──────────────── logger.py
  run_cycle()                log_decision()
      │
      ▼                    outcome_tracker.py [Phase 3]
  DRY RUN log              check_resolved()
  (LIVE: CLOB order)       → outcomes.json
                           → calibration metrics
                                     │
                                     ▼
                            dashboard/ [Phase 4]
                           Streamlit визуализация
```

---

## API и лимиты

| Сервис | Базовый URL | Лимит (free) | Используется для |
|---|---|---|---|
| Gamma API | gamma-api.polymarket.com | без лимита | рынки, события, теги |
| CLOB API | clob.polymarket.com | без лимита | цены, ордербук, история |
| GNews | gnews.io/api/v4 | 100 req/day | новости по теме |
| Anthropic | api.anthropic.com | pay-per-use | анализ вероятности |
| **Polymarket trading** | **— геоблок —** | **недоступно** | **ставки (заморожено)** |

---

## Фазы разработки

---

### ✅ Phase 1 — Foundation
*Базовый рабочий бот в DRY RUN режиме*

- [x] Окружение: venv, requirements.txt, .env, .gitignore
- [x] config.py — все настройки централизованы
- [x] polymarket_client.py — загрузка рынков через Gamma API
- [x] news_monitor.py — GNews + кэш 4 часа
- [x] strategy.py — Claude Haiku + Kelly criterion
- [x] logger.py — bet_history.json
- [x] main.py — полный цикл DRY RUN

---

### ✅ Phase 2 — Daily Markets & Analysis
*Фокус на рынки с быстрым исходом + улучшение анализа*

- [x] Daily mode: рынки закрывающиеся в 24ч (`end_date_min/max`)
- [x] DAILY_MODE флаг, DAILY_POLL_INTERVAL=60s, кэш 2 мин
- [x] NO side betting: бот выбирает лучший edge между YES и NO
- [x] Kelly работает симметрично для обеих сторон
- [x] Price history: движение цены за 1ч → передаётся в Claude-промпт
- [x] CLOB spread filter: широкий спред → пропуск в LIVE (предупреждение в DRY)
- [x] Фикс Unicode regex в news_monitor (Sébastien, León работают)
- [x] Фикс Windows UTF-8 консоль
- [x] `_parse_market()` — переиспользуемый хелпер
- [x] Дата закрытия рынка в консольном выводе

**Наблюдение:** daily рынки зависят от дня. 14.03.2026 — только испанские
выборы, GNews не покрывает. При крипто/US политике — бот работает полностью.

---

### 🔄 Phase 3 — Outcome Tracking & Calibration
*Измерить точность предсказаний. Без этого улучшения вслепую.*

**Почему это критично:**
Бот делает ставки но никогда не узнаёт выиграл или проиграл.
Нельзя улучшать то что не измеряешь.

**Что делать:**

#### 3.1 Реструктуризация файлов
- [ ] Создать директории `core/`, `dashboard/`, `data/`
- [ ] Перенести `polymarket_client.py`, `news_monitor.py`, `strategy.py`, `logger.py` → `core/`
- [ ] Перенести `bet_history.json`, `news_cache.json` → `data/`
- [ ] Обновить импорты в `main.py` и `config.py`
- [ ] Добавить `data/` в .gitignore

#### 3.2 outcome_tracker.py
- [ ] `check_resolved_markets()` — находит рынки из bet_history где end_date прошёл
- [ ] Запрашивает фактический исход через Gamma API (`resolved`, `resolutionPrice`)
- [ ] Записывает результат в `data/outcomes.json`:
  ```json
  {
    "condition_id": "0x...",
    "question": "Will BTC exceed $90k?",
    "our_side": "YES",
    "our_prob": 0.72,
    "market_prob": 0.61,
    "bet_amount": 3.20,
    "resolved_yes": true,
    "won": true,
    "hypothetical_pnl": 1.93,
    "resolved_at": "2026-03-15T23:00:00"
  }
  ```
- [ ] Запускается автоматически в конце каждого цикла main.py

#### 3.3 Calibration metrics
- [ ] `calibration_score()` — считает точность по диапазонам вероятности:
  ```
  prob 60-70% → выиграли X из Y раз (должно быть ~65%)
  prob 70-80% → выиграли X из Y раз (должно быть ~75%)
  ```
- [ ] `hypothetical_roi()` — гипотетический P&L если бы ставки были реальными
- [ ] `win_rate_by_category()` — точность по темам (крипто vs политика vs ...)
- [ ] Выводить метрики в конце каждого цикла (рядом со статистикой)

---

### 📋 Phase 4 — Streamlit Dashboard
*Визуальный интерфейс для анализа работы бота*

**Технологии:** Streamlit, Plotly, Pandas

**Страницы:**

#### Overview (`01_overview.py`)
- KPI карточки: всего ставок / win rate / гипотетический P&L / calibration score
- График P&L по времени
- Топ-5 лучших и худших предсказаний

#### Bets History (`02_bets.py`)
- Таблица всех ставок с фильтрами:
  - статус (pending / won / lost)
  - сторона (YES / NO)
  - дата диапазон
  - тема рынка
- Колонки: вопрос, наша prob, рыночная prob, edge, ставка, исход, P&L

#### Market Browser (`03_markets.py`)
- Текущие доступные рынки (daily + normal)
- YES/NO цены в реальном времени
- Объём, ликвидность, дата закрытия
- Кнопка "Analyze" → запускает Claude анализ вручную

#### Calibration (`04_calibration.py`)
- График: ось X = наша предсказанная вероятность, ось Y = фактический win rate
- Идеальная линия y=x (идеальная калибровка)
- Наши точки по диапазонам (60-70%, 70-80%, 80-90%, 90%+)
- Если наши точки выше линии — мы недооцениваем вероятности (edge есть)

---

### 📋 Phase 5 — Signal Improvement
*Улучшить качество анализа. Делать только после накопления данных из Phase 3.*

- [ ] Заменить GNews на реальное время (Twitter API / Perplexity / Tavily)
- [ ] Для крипто: добавить on-chain сигналы (Binance WS + CoinGecko)
- [ ] Специализация: фокус на одну нишу где есть доказанный edge
- [ ] Async Claude (AsyncAnthropic) — убрать блокировку event loop
- [ ] Параллельный анализ рынков через asyncio.gather()

---

### 📋 Phase 6 — Execution (геоблок снят)
*Только после снятия ограничения и доказанного edge в Phase 3*

- [ ] py-clob-client: реальные ставки через CLOB
- [ ] Portfolio-level Kelly (учёт корреляций между позициями)
- [ ] Daily loss limit: -20% бюджета = стоп на день
- [ ] Максимум открытых позиций одновременно
- [ ] Защита от дублирования: не ставить на один рынок дважды
- [ ] WebSocket для real-time цен

---

## Конфигурация (.env)

```env
# API ключи
ANTHROPIC_API_KEY=sk-ant-...
GNEWS_API_KEY=...
POLY_PRIVATE_KEY=          # оставь пустым до Phase 6

# Бюджет
BUDGET=100
MAX_BET=5

# Режимы
DRY_RUN=true               # false только в Phase 6
DAILY_MODE=true            # true = рынки закрывающиеся в 24ч

# Стратегия
MIN_EDGE=0.05
KELLY_FRACTION=0.25
MAX_SPREAD=0.05

# Опционально
MAX_DAYS_TO_CLOSE=0        # 0 = без ограничения
```

---

## Метрики успеха

| Метрика | Цель | Как измеряем |
|---|---|---|
| Calibration score | < 0.05 Brier score | outcome_tracker.py |
| Win rate при edge > 5% | > 55% | outcomes.json |
| Гипотетический ROI | > 0 за 30 дней | hypothetical_pnl |
| Покрытие рынков | > 5 анализов/день | bet_history.json |

---

## Ключевые решения

| Решение | Почему |
|---|---|
| Фокус на daily рынки | Быстрый цикл обратной связи (результат в тот же день) |
| Dual YES/NO edge | Упущенные возможности на стороне NO |
| DRY RUN приоритет | Нельзя торговать по гео — строим данные |
| Outcome tracking сейчас | Без данных невозможно улучшать стратегию |
| Streamlit dashboard | Делает анализ наглядным, помогает видеть паттерны |
| Специализация в будущем | Генерализм слабее экспертизы в одной нише |

---

> Текущий приоритет: Phase 3 (Outcome Tracking) → Phase 4 (Dashboard)
> Execution — только после снятия геоблока и доказанного edge.
