import json
import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, str(Path(__file__).parents[2]))
from core.outcome_tracker import calibration_score, hypothetical_roi, win_rate_by_category

OUTCOMES_FILE = Path("data/outcomes.json")

st.set_page_config(page_title="Calibration", page_icon="🎯", layout="wide")
st.title("🎯 Calibration")


def load_outcomes() -> list:
    if OUTCOMES_FILE.exists():
        try:
            return json.loads(OUTCOMES_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


outcomes = load_outcomes()

if not outcomes:
    st.info("Нет разрешённых рынков. Данные появятся после закрытия первых ставок.")
    st.stop()

cal = calibration_score(outcomes)
roi = hypothetical_roi(outcomes)
cats = win_rate_by_category(outcomes)

# --- Метрики ---
c1, c2, c3 = st.columns(3)
c1.metric("Brier score", f"{cal['brier_score']:.4f}", "цель < 0.05")
c2.metric("Win rate", f"{roi['win_rate']:.0%}", f"{roi['wins']}/{roi['total']}")
c3.metric("ROI", f"{roi['roi_pct']:+.1f}%", f"P&L ${roi['total_pnl']:+.2f}")

st.divider()

# --- Calibration plot ---
buckets = cal.get("buckets", {})
if buckets:
    points_x, points_y, labels = [], [], []
    for key, data in sorted(buckets.items()):
        low = int(key.split("-")[0]) / 100
        mid = low + 0.05
        wr = data["wins"] / data["total"] if data["total"] else 0
        points_x.append(mid)
        points_y.append(wr)
        labels.append(f"{key}<br>{data['wins']}/{data['total']}")

    fig = go.Figure()

    # Идеальная линия
    fig.add_trace(go.Scatter(
        x=[0, 1], y=[0, 1],
        mode="lines",
        line=dict(dash="dash", color="gray", width=1),
        name="Идеальная калибровка",
    ))

    # Наши точки
    fig.add_trace(go.Scatter(
        x=points_x,
        y=points_y,
        mode="markers+text",
        marker=dict(size=14, color="royalblue"),
        text=labels,
        textposition="top center",
        name="Наши предсказания",
    ))

    fig.update_layout(
        title="Калибровочный график (наша prob vs реальный win rate)",
        xaxis=dict(title="Предсказанная вероятность", range=[0, 1], tickformat=".0%"),
        yaxis=dict(title="Фактический win rate", range=[0, 1], tickformat=".0%"),
        height=500,
    )
    st.plotly_chart(fig, use_container_width=True)
    st.caption("Точки выше линии → мы недооцениваем вероятности (есть edge). Ниже → переоцениваем.")

st.divider()

# --- Win rate по темам ---
if cats:
    st.subheader("Win rate по темам")
    cat_rows = []
    for cat, data in sorted(cats.items(), key=lambda x: -x[1]["total"]):
        wr = data["wins"] / data["total"] if data["total"] else 0
        cat_rows.append({
            "Тема": cat,
            "Win rate": f"{wr:.0%}",
            "Выиграно": f"{data['wins']}/{data['total']}",
            "P&L": f"${data['pnl']:+.2f}",
        })

    st.dataframe(pd.DataFrame(cat_rows), use_container_width=True, hide_index=True)
