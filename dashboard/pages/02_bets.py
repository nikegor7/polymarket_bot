import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parents[2]))
from core.database import init_db, load_bets, load_outcomes
from core.outcome_tracker import _detect_category

st.set_page_config(page_title="Bets History", page_icon="📋", layout="wide")
st.title("📋 Bets History")

init_db()
history = load_bets()
outcomes = load_outcomes()

if not history:
    st.warning("Нет данных. Запусти бота: `python main.py`")
    st.stop()

# Объединяем history + outcomes по condition_id
outcomes_map = {o["condition_id"]: o for o in outcomes}

rows = []
for r in history:
    cid = r.get("condition_id", "")
    outcome = outcomes_map.get(cid)
    if outcome:
        status = "won" if outcome["won"] else "lost"
        pnl = outcome["hypothetical_pnl"]
    else:
        status = "pending"
        pnl = None

    rows.append({
        "timestamp": r.get("timestamp", ""),
        "question": r.get("question", ""),
        "side": r.get("side", ""),
        "our_prob": r.get("our_prob", 0),
        "market_prob": r.get("market_prob", 0),
        "edge": r.get("edge", 0),
        "bet_amount": r.get("bet_amount", 0),
        "status": status,
        "pnl": pnl,
        "category": _detect_category(r.get("question", "")),
        "dry_run": r.get("dry_run", True),
    })

df = pd.DataFrame(rows)
df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")

# --- Фильтры ---
st.subheader("Фильтры")
fc1, fc2, fc3, fc4 = st.columns(4)

status_opts = ["все"] + sorted(df["status"].unique().tolist())
side_opts = ["все"] + sorted(df["side"].unique().tolist())
cat_opts = ["все"] + sorted(df["category"].unique().tolist())

sel_status = fc1.selectbox("Статус", status_opts)
sel_side = fc2.selectbox("Сторона", side_opts)
sel_cat = fc3.selectbox("Тема", cat_opts)

dates = df["timestamp"].dropna()
if not dates.empty:
    min_d, max_d = dates.min().date(), dates.max().date()
    sel_dates = fc4.date_input("Период", value=(min_d, max_d))
else:
    sel_dates = None

mask = pd.Series([True] * len(df))
if sel_status != "все":
    mask &= df["status"] == sel_status
if sel_side != "все":
    mask &= df["side"] == sel_side
if sel_cat != "все":
    mask &= df["category"] == sel_cat
if sel_dates and len(sel_dates) == 2:
    mask &= df["timestamp"].dt.date >= sel_dates[0]
    mask &= df["timestamp"].dt.date <= sel_dates[1]

filtered = df[mask].copy()

st.caption(f"Показано {len(filtered)} из {len(df)} ставок")

# --- Таблица ---
display = filtered[[
    "timestamp", "question", "side", "our_prob", "market_prob",
    "edge", "bet_amount", "status", "pnl", "category",
]].copy()

display["timestamp"] = display["timestamp"].dt.strftime("%Y-%m-%d %H:%M")
display["question"] = display["question"].str[:60]
display["our_prob"] = display["our_prob"].map("{:.0%}".format)
display["market_prob"] = display["market_prob"].map("{:.0%}".format)
display["edge"] = display["edge"].map("{:+.1%}".format)
display["bet_amount"] = display["bet_amount"].map("${:.2f}".format)
display["pnl"] = display["pnl"].apply(lambda x: f"${x:+.2f}" if x is not None else "—")

display.columns = ["Время", "Вопрос", "Сторона", "Наша prob", "Рынок prob", "Edge", "Ставка", "Статус", "P&L", "Тема"]

st.dataframe(display, use_container_width=True, hide_index=True)
