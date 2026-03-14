import asyncio
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parents[2]))
import config
from core.polymarket_client import PolymarketClient

st.set_page_config(page_title="Markets", page_icon="🌍", layout="wide")
st.title("🌍 Market Browser")


@st.cache_data(ttl=120)
def fetch_markets(daily: bool) -> list:
    async def _run():
        async with PolymarketClient() as client:
            if daily:
                return await client.get_daily_markets(limit=50)
            else:
                return await client.get_markets(limit=50)
    return asyncio.run(_run())


col_mode, col_refresh = st.columns([3, 1])
daily_mode = col_mode.toggle("Daily (закрываются в 24ч)", value=True)
if col_refresh.button("Обновить"):
    st.cache_data.clear()

with st.spinner("Загружаем рынки..."):
    try:
        markets = fetch_markets(daily_mode)
    except Exception as e:
        st.error(f"Ошибка загрузки: {e}")
        st.stop()

if not markets:
    st.warning("Рынков не найдено по текущим фильтрам.")
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
