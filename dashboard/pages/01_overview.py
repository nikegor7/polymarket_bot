import sys
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

sys.path.insert(0, str(Path(__file__).parents[2]))
from core.database import init_db, load_bets, load_outcomes
from core.outcome_tracker import calibration_score, hypothetical_roi

st.set_page_config(page_title="Overview", page_icon="📈", layout="wide")
st.title("📈 Overview")

init_db()
history = load_bets()
outcomes = load_outcomes()

if not history:
    st.warning("Нет данных. Запусти бота: `python main.py`")
    st.stop()

roi = hypothetical_roi(outcomes) if outcomes else {}
cal = calibration_score(outcomes) if outcomes else {}

# --- KPI карточки ---
col1, col2, col3, col4 = st.columns(4)
col1.metric("Всего ставок", len(history))
col2.metric(
    "Win rate",
    f"{roi.get('win_rate', 0):.0%}" if roi else "—",
    f"{roi.get('wins', 0)}/{roi.get('total', 0)}" if roi else None,
)
col3.metric(
    "Гипотет. P&L",
    f"${roi.get('total_pnl', 0):+.2f}" if roi else "—",
    f"ROI {roi.get('roi_pct', 0):+.1f}%" if roi else None,
)
col4.metric(
    "Brier score",
    f"{cal.get('brier_score', 0):.4f}" if cal else "—",
    "цель < 0.05",
)

st.divider()

# --- P&L по времени ---
if outcomes:
    df_out = pd.DataFrame(outcomes)
    df_out["resolved_at"] = pd.to_datetime(df_out["resolved_at"], errors="coerce")
    df_out = df_out.sort_values("resolved_at")
    df_out["cumulative_pnl"] = df_out["hypothetical_pnl"].cumsum()

    fig = px.line(
        df_out,
        x="resolved_at",
        y="cumulative_pnl",
        title="Гипотетический P&L по времени",
        labels={"resolved_at": "Дата", "cumulative_pnl": "P&L ($)"},
        markers=True,
    )
    fig.add_hline(y=0, line_dash="dash", line_color="gray")
    st.plotly_chart(fig, use_container_width=True)

    st.divider()

    # --- Топ-5 лучших и худших ---
    col_best, col_worst = st.columns(2)

    with col_best:
        st.subheader("Топ-5 лучших")
        best = df_out.nlargest(5, "hypothetical_pnl")[["question", "our_side", "our_prob", "hypothetical_pnl"]]
        best.columns = ["Вопрос", "Сторона", "Prob", "P&L"]
        best["Вопрос"] = best["Вопрос"].str[:50]
        best["Prob"] = best["Prob"].map("{:.0%}".format)
        best["P&L"] = best["P&L"].map("${:+.2f}".format)
        st.dataframe(best, use_container_width=True, hide_index=True)

    with col_worst:
        st.subheader("Топ-5 худших")
        worst = df_out.nsmallest(5, "hypothetical_pnl")[["question", "our_side", "our_prob", "hypothetical_pnl"]]
        worst.columns = ["Вопрос", "Сторона", "Prob", "P&L"]
        worst["Вопрос"] = worst["Вопрос"].str[:50]
        worst["Prob"] = worst["Prob"].map("{:.0%}".format)
        worst["P&L"] = worst["P&L"].map("${:+.2f}".format)
        st.dataframe(worst, use_container_width=True, hide_index=True)
else:
    st.info("Нет разрешённых рынков для P&L графика. Данные появятся после закрытия первых ставок.")
