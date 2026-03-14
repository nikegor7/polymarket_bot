"""Точка входа: streamlit run dashboard/app.py"""
import streamlit as st

st.set_page_config(
    page_title="Polymarket Bot",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title("📊 Polymarket Prediction Bot")
st.markdown("Навигация по страницам в боковой панели слева.")

st.info(
    "**Страницы:**\n"
    "- **Overview** — KPI: win rate, P&L, calibration\n"
    "- **Bets History** — история ставок с фильтрами\n"
    "- **Markets** — браузер рынков в реальном времени\n"
    "- **Calibration** — график точности предсказаний"
)
