"""SQLite хранилище для бота. Заменяет JSON файлы.

Таблицы:
  bets     — история решений бота (бывший bet_history.json)
  outcomes — результаты разрешённых рынков (бывший outcomes.json)

Миграция: при первом запуске импортирует данные из JSON если они есть.
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

DB_PATH = Path("data/bot.db")
_LEGACY_BETS = Path("data/bet_history.json")
_LEGACY_OUTCOMES = Path("data/outcomes.json")

_CREATE_BETS = """
CREATE TABLE IF NOT EXISTS bets (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp     TEXT NOT NULL,
    question      TEXT NOT NULL,
    condition_id  TEXT NOT NULL,
    end_date      TEXT,
    our_prob      REAL NOT NULL,
    market_prob   REAL NOT NULL,
    edge          REAL NOT NULL,
    confidence    TEXT NOT NULL,
    side          TEXT NOT NULL,
    bet_amount    REAL NOT NULL,
    dry_run       INTEGER NOT NULL DEFAULT 1,
    reasoning     TEXT
)
"""

_CREATE_OUTCOMES = """
CREATE TABLE IF NOT EXISTS outcomes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    condition_id    TEXT NOT NULL UNIQUE,
    question        TEXT NOT NULL,
    our_side        TEXT NOT NULL,
    our_prob        REAL NOT NULL,
    market_prob     REAL NOT NULL,
    bet_amount      REAL NOT NULL,
    resolved_yes    INTEGER NOT NULL,
    won             INTEGER NOT NULL,
    hypothetical_pnl REAL NOT NULL,
    resolved_at     TEXT
)
"""


def get_connection() -> sqlite3.Connection:
    """Возвращает соединение с WAL mode и row_factory."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _ensure_columns(conn: sqlite3.Connection) -> None:
    """Добавляет колонки, которых может не быть в старых БД."""
    cursor = conn.execute("PRAGMA table_info(bets)")
    columns = {row[1] for row in cursor.fetchall()}
    if "prompt_text" not in columns:
        conn.execute("ALTER TABLE bets ADD COLUMN prompt_text TEXT DEFAULT ''")
    if "raw_response" not in columns:
        conn.execute("ALTER TABLE bets ADD COLUMN raw_response TEXT DEFAULT ''")
    conn.commit()


def init_db() -> None:
    """Создаёт таблицы, индексы и мигрирует данные из JSON при первом запуске."""
    conn = get_connection()
    try:
        conn.execute(_CREATE_BETS)
        conn.execute(_CREATE_OUTCOMES)
        # Индексы для быстрых запросов
        conn.execute("CREATE INDEX IF NOT EXISTS idx_bets_condition_ts ON bets(condition_id, timestamp)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_bets_timestamp ON bets(timestamp)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_outcomes_condition ON outcomes(condition_id)")
        conn.commit()
        _ensure_columns(conn)
        _migrate_json(conn)
    finally:
        conn.close()


def _migrate_json(conn: sqlite3.Connection) -> None:
    """Импортирует данные из legacy JSON файлов (один раз)."""
    # Bets
    if _LEGACY_BETS.exists():
        cursor = conn.execute("SELECT COUNT(*) FROM bets")
        if cursor.fetchone()[0] == 0:
            try:
                records = json.loads(_LEGACY_BETS.read_text(encoding="utf-8"))
                for r in records:
                    conn.execute(
                        "INSERT INTO bets (timestamp, question, condition_id, end_date, "
                        "our_prob, market_prob, edge, confidence, side, bet_amount, dry_run, reasoning) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (r["timestamp"], r["question"], r.get("condition_id", ""),
                         r.get("end_date", ""), r["our_prob"], r["market_prob"],
                         r["edge"], r["confidence"], r["side"], r["bet_amount"],
                         1 if r.get("dry_run", True) else 0, r.get("reasoning", "")),
                    )
                conn.commit()
                print(f"[DB] Мигрировано {len(records)} записей из bet_history.json")
            except (json.JSONDecodeError, KeyError) as e:
                print(f"[DB] Ошибка миграции bets: {e}")

    # Outcomes
    if _LEGACY_OUTCOMES.exists():
        cursor = conn.execute("SELECT COUNT(*) FROM outcomes")
        if cursor.fetchone()[0] == 0:
            try:
                records = json.loads(_LEGACY_OUTCOMES.read_text(encoding="utf-8"))
                for r in records:
                    conn.execute(
                        "INSERT OR IGNORE INTO outcomes (condition_id, question, our_side, our_prob, "
                        "market_prob, bet_amount, resolved_yes, won, hypothetical_pnl, resolved_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (r["condition_id"], r["question"], r["our_side"], r["our_prob"],
                         r["market_prob"], r["bet_amount"],
                         1 if r["resolved_yes"] else 0, 1 if r["won"] else 0,
                         r["hypothetical_pnl"], r.get("resolved_at", "")),
                    )
                conn.commit()
                print(f"[DB] Мигрировано {len(records)} исходов из outcomes.json")
            except (json.JSONDecodeError, KeyError) as e:
                print(f"[DB] Ошибка миграции outcomes: {e}")


# ─── Bets API ──────────────────────────────────────────────

def insert_bet(record: dict) -> None:
    """Записывает одно решение бота."""
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO bets (timestamp, question, condition_id, end_date, "
            "our_prob, market_prob, edge, confidence, side, bet_amount, dry_run, reasoning, "
            "prompt_text, raw_response) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (record["timestamp"], record["question"], record.get("condition_id", ""),
             record.get("end_date", ""), record["our_prob"], record["market_prob"],
             record["edge"], record["confidence"], record["side"], record["bet_amount"],
             1 if record.get("dry_run", True) else 0, record.get("reasoning", ""),
             record.get("prompt_text", ""), record.get("raw_response", "")),
        )
        conn.commit()
    finally:
        conn.close()


def load_bets(limit: int = 0) -> list[dict]:
    """Загружает ставки. limit=0 — все (для outcome tracking), limit>0 — последние N."""
    conn = get_connection()
    try:
        if limit > 0:
            rows = conn.execute(
                "SELECT * FROM bets ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in reversed(rows)]
        rows = conn.execute("SELECT * FROM bets ORDER BY id").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def count_bets() -> int:
    """Быстрый подсчёт общего количества ставок (без загрузки всех строк)."""
    conn = get_connection()
    try:
        return conn.execute("SELECT COUNT(*) FROM bets").fetchone()[0]
    finally:
        conn.close()


# ─── Outcomes API ──────────────────────────────────────────

def insert_outcome(outcome: dict) -> None:
    """Записывает результат разрешённого рынка."""
    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO outcomes (condition_id, question, our_side, our_prob, "
            "market_prob, bet_amount, resolved_yes, won, hypothetical_pnl, resolved_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (outcome["condition_id"], outcome["question"], outcome["our_side"],
             outcome["our_prob"], outcome["market_prob"], outcome["bet_amount"],
             1 if outcome["resolved_yes"] else 0, 1 if outcome["won"] else 0,
             outcome["hypothetical_pnl"], outcome.get("resolved_at", "")),
        )
        conn.commit()
    finally:
        conn.close()


def load_outcomes() -> list[dict]:
    """Загружает все исходы."""
    conn = get_connection()
    try:
        rows = conn.execute("SELECT * FROM outcomes ORDER BY id").fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["resolved_yes"] = bool(d["resolved_yes"])
            d["won"] = bool(d["won"])
            result.append(d)
        return result
    finally:
        conn.close()


def get_tracked_condition_ids() -> set[str]:
    """Возвращает set condition_id уже отслеженных исходов."""
    conn = get_connection()
    try:
        rows = conn.execute("SELECT condition_id FROM outcomes").fetchall()
        return {r["condition_id"] for r in rows}
    finally:
        conn.close()


# ─── Dedup & Limits ──────────────────────────────────────

def has_recent_bet(condition_id: str, hours: int = 24) -> bool:
    """True если на этот condition_id уже ставили за последние N часов."""
    conn = get_connection()
    try:
        cutoff = (datetime.now() - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
        row = conn.execute(
            "SELECT COUNT(*) FROM bets WHERE condition_id = ? AND timestamp > ?",
            (condition_id, cutoff),
        ).fetchone()
        return row[0] > 0
    finally:
        conn.close()


def total_bet_amount_today() -> float:
    """Сумма ставок за сегодня."""
    conn = get_connection()
    try:
        today_start = datetime.now().strftime("%Y-%m-%d 00:00:00")
        row = conn.execute(
            "SELECT COALESCE(SUM(bet_amount), 0) FROM bets WHERE timestamp >= ?",
            (today_start,),
        ).fetchone()
        return row[0]
    finally:
        conn.close()


def count_open_bets() -> int:
    """Количество ставок без отслеженного результата."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM bets b WHERE NOT EXISTS "
            "(SELECT 1 FROM outcomes o WHERE o.condition_id = b.condition_id)"
        ).fetchone()
        return row[0]
    finally:
        conn.close()


# Категории для корреляционного Kelly
_BET_CATEGORIES = {
    "crypto": ["bitcoin", "ethereum", "crypto", "btc", "eth", "solana",
               "coinbase", "binance", "defi", "nft", "blockchain", "xrp",
               "cardano", "dogecoin", "polygon", "avalanche", "chainlink",
               "polkadot", "litecoin", "uniswap", "toncoin", "pepe"],
    "politics": ["trump", "president", "congress", "senate", "election",
                 "white house", "tariff", "sanction", "republican", "democrat"],
    "economics": ["inflation", "recession", "gdp", "interest rate", "dollar",
                  "stock market", "s&p", "nasdaq", "oil", "gold"],
    "tech": ["openai", "gpt", "artificial intelligence", "apple",
             "tesla", "spacex", "elon", "google", "microsoft", "nvidia"],
    "geopolitics": ["ukraine", "russia", "china", "iran", "war", "ceasefire",
                    "nato", "israel", "gaza", "taiwan"],
}


def _detect_bet_category(question: str) -> str:
    q = question.lower()
    for category, keywords in _BET_CATEGORIES.items():
        if any(re.search(r'\b' + re.escape(kw) + r'\b', q) for kw in keywords):
            return category
    return "other"


def count_open_bets_by_category() -> dict[str, int]:
    """Количество открытых ставок по категориям (для корреляционного Kelly)."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT question FROM bets b WHERE NOT EXISTS "
            "(SELECT 1 FROM outcomes o WHERE o.condition_id = b.condition_id)"
        ).fetchall()
        cats: dict[str, int] = {}
        for r in rows:
            cat = _detect_bet_category(r["question"])
            cats[cat] = cats.get(cat, 0) + 1
        return cats
    finally:
        conn.close()
