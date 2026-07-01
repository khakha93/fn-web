import streamlit as st
import yfinance as yf
import plotly.graph_objects as go

st.title("HTS급 인터랙티브 주가 차트")

ticker = st.text_input("티커 입력", "AAPL")
df = yf.download(ticker, start="2025-01-01")

# 내가 직접 추출한 고유 지표 (예시: 종가와 시가의 평균)
df['my_indicator'] = (df['Close'] + df['Open']) / 2

# 차트 객체 생성
fig = go.Figure()

# 1. 기본 캔들스틱 차트 추가 (마우스 올리면 시/고/저/종가 기본 표시)
fig.add_trace(go.Candlestick(
    x=df.index,
    open=df['Open'], high=df['High'],
    low=df['Low'], close=df['Close'],
    name="주가 (Candle)"
))

# 2. 내가 만든 고유 지표 선 추가 (마우스 올리면 이 값도 같이 표시됨)
fig.add_trace(go.Scatter(
    x=df.index, y=df['my_indicator'],
    name="나의 고유 지표",
    line=dict(color='orange', width=2)
))

# 3. 레이아웃 및 HTS 기능 설정
fig.update_layout(
    template="plotly_dark",
    hovermode="x unified",  # 마우스 좌표의 모든 값을 하나로 묶어 표시 (HTS 방식)
    xaxis_rangeslider_visible=False  # 하단 미니맵 제거 (깔끔한 화면을 위해)
)

# 4. Streamlit에 차트 띄우기 (config 인자로 마우스 휠 줌 기능 활성화)
st.plotly_chart(fig, use_container_width=True, config={'scrollZoom': True})