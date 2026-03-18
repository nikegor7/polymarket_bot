import asyncio
import re
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parents[2]))
import config
from core.polymarket_client import PolymarketClient

st.set_page_config(page_title="Markets", page_icon="🌍", layout="wide")
st.title("🌍 Market Browser")

CAT_LABELS = {
    "crypto":      "🪙 Крипто",
    "politics":    "🏛 Политика",
    "economics":   "📊 Экономика",
    "tech":        "💻 Технологии",
    "geopolitics": "🌐 Геополитика",
}


@st.cache_data(ttl=120, show_spinner=False)
def fetch_markets(daily: bool, category: str) -> list:
    keywords = config.CATEGORY_TOPICS.get(category, [])

    async def _run():
        async with PolymarketClient() as client:
            if daily:
                markets = await client.get_daily_markets(limit=200)
            else:
                markets = await client.get_markets(limit=200)
        return [
            m for m in markets
            if any(re.search(r'\b' + re.escape(kw.strip()) + r'\b', m["question"].lower()) for kw in keywords)
        ]
    return asyncio.run(_run())


# --- Настройки ---
daily_mode = st.toggle("Daily (закрываются в 24ч)", value=False)

st.markdown("**Выберите категорию:**")
cols = st.columns(len(CAT_LABELS))
for i, (cat, label) in enumerate(CAT_LABELS.items()):
    if cols[i].button(label, use_container_width=True):
        st.session_state["market_category"] = cat
        st.cache_data.clear()

# --- Загрузка только после выбора категории ---
if "market_category" not in st.session_state:
    st.info("Нажмите на категорию чтобы загрузить рынки.")
    st.stop()

category = st.session_state["market_category"]
st.caption(f"Категория: **{CAT_LABELS[category]}**")

with st.spinner(f"Загружаем {CAT_LABELS[category]}..."):
    try:
        markets = fetch_markets(daily_mode, category)
    except Exception as e:
        st.error(f"Ошибка загрузки: {e}")
        st.stop()

if not markets:
    st.warning("Рынков не найдено. Попробуй отключить Daily режим.")
    st.stop()

st.caption(f"Найдено {len(markets)} рынков")

rows = []
for m in markets:
    rows.append({
        "question": m["question"],
        "yes_price": m["yes_price"],
        "no_price": m["no_price"],
        "volume": m["volume"],
        "volume_24hr": m["volume_24hr"],
        "liquidity": m["liquidity"],
        "end_date": (m.get("end_date") or "")[:10],
    })

df = pd.DataFrame(rows)
df["yes_price"] = df["yes_price"].map("{:.0%}".format)
df["no_price"] = df["no_price"].map("{:.0%}".format)
df["volume"] = df["volume"].map("${:,.0f}".format)
df["volume_24hr"] = df["volume_24hr"].map("${:,.0f}".format)
df["liquidity"] = df["liquidity"].map("${:,.0f}".format)
df["question"] = df["question"].str[:70]

df.columns = ["Вопрос", "YES", "NO", "Объём", "Объём 24h", "Ликвидность", "Закрытие"]

st.dataframe(df, use_container_width=True, hide_index=True)
