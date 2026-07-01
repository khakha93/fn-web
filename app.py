import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.graph_objects as px
from plotly.subplots import make_subplots
import div_yf as dyf

st.title("나의 고유 지표 주가 대시보드")

# 1. 자산 및 기간 선택 UI
ticker = st.text_input("티커 입력", "QCOM")

# 2. 데이터 가져오기
df_price = yf.download(ticker, period="max", auto_adjust=False)
df_close = df_price['Close'].copy()


# 단일 티커 검색 시 MultiIndex 컬럼일 경우 평탄화
if isinstance(df_price.columns, pd.MultiIndex):
    df_price.columns = df_price.columns.droplevel(1)

# 3. 배당수익률 지표 계산 로직
df_div = dyf.get_yf_dividend_history(ticker)
df_div_period = dyf.add_period_columns_by_div(df_div)
df_com = dyf.group_by_period_by_div(df_div_period)
dfs_agg, df_stat = dyf.merge_dividend_data(df_close, df_com)

# 4. 날짜 범위 선택 UI (동적 조절)
# 데이터의 날짜 범위 확인 (timezone 제거하여 일치시킴)
df_price.index = df_price.index.tz_localize(None)
df_stat['Date'] = pd.to_datetime(df_stat['Date']).dt.tz_localize(None)

min_date = df_price.index.min().to_pydatetime().date()
max_date = df_price.index.max().to_pydatetime().date()

# 기본 조회 범위를 2010년 10월 1일로 설정 (데이터 시작일이 그보다 늦은 경우 데이터 시작일로 대체)
default_start = max(min_date, pd.to_datetime("2010-01-01").date())

# Session State를 이용한 시작일/종료일 상태 초기화
if "start_date" not in st.session_state:
    st.session_state.start_date = default_start
if "end_date" not in st.session_state:
    st.session_state.end_date = max_date

# st.form을 사용하여 날짜 입력이 모두 완료되고 "조회" 버튼을 클릭할 때만 재로딩되도록 구성
with st.form(key="date_range_form"):
    col1, col2 = st.columns(2)
    with col1:
        start_input = st.date_input(
            "시작일 입력",
            value=st.session_state.start_date,
            min_value=min_date,
            max_value=max_date
        )
    with col2:
        end_input = st.date_input(
            "종료일 입력",
            value=st.session_state.end_date,
            min_value=min_date,
            max_value=max_date
        )
    
    # 폼 제출 버튼
    submitted = st.form_submit_button(label="기간 적용 및 조회")

# 제출 버튼이 눌렸을 때만 값을 검증하고 반영
if submitted:
    if start_input > end_input:
        st.error("시작일은 종료일보다 이전이어야 합니다.")
    else:
        st.session_state.start_date = start_input
        st.session_state.end_date = end_input

# 최종 차트에 적용할 필터링 날짜 설정
start_date = st.session_state.start_date
end_date = st.session_state.end_date

# 선택한 기간으로 데이터 필터링
df_filtered = df_price.loc[pd.to_datetime(start_date):pd.to_datetime(end_date)]
df_stat_filtered = df_stat[
    (df_stat['Date'] >= pd.to_datetime(start_date)) & 
    (df_stat['Date'] <= pd.to_datetime(end_date))
]

# 우측 버퍼(5%)를 데이터 자체에 강제로 공백 행(NaN)으로 주입하여 
# rangeselector(기간 선택 버튼) 및 double-click 리셋 기능과의 충돌(여백 초기화 현상)을 완벽히 해결합니다.
if not df_filtered.empty:
    start_dt = pd.to_datetime(start_date)
    end_dt = pd.to_datetime(end_date)
    time_buffer = (end_dt - start_dt) * 0.05
    buffer_date = end_dt + time_buffer
    
    # 주가 데이터프레임 복사 후 마지막에 NaN값을 가지는 버퍼 날짜 행 추가
    df_filtered_buffered = df_filtered.copy()
    df_filtered_buffered.loc[buffer_date] = [None] * len(df_filtered.columns)
    df_filtered_buffered = df_filtered_buffered.sort_index()
    
    # 배당 데이터프레임 복사 후 마지막에 Date만 채워진 행 추가 (.loc 사용으로 pd.concat 경고 우회)
    df_stat_filtered_buffered = df_stat_filtered.copy()
    next_idx = len(df_stat_filtered_buffered)
    df_stat_filtered_buffered.loc[next_idx] = [None] * len(df_stat_filtered_buffered.columns)
    df_stat_filtered_buffered.loc[next_idx, 'Date'] = buffer_date
    df_stat_filtered_buffered = df_stat_filtered_buffered.sort_values('Date').reset_index(drop=True)
else:
    df_filtered_buffered = df_filtered
    df_stat_filtered_buffered = df_stat_filtered

# 5. Plotly를 이용한 HTS식 레이어 차트 그리기 (2단 분할 차트)
fig = make_subplots(
    rows=2, cols=1,
    shared_xaxes=True,
    vertical_spacing=0.15,
    subplot_titles=(f"{ticker} 주가 (종가)", "배당수익률(DFS)"),
    row_heights=[0.5, 0.5]
)

# 기본 주가 캔들스틱 (또는 라인) 추가 (버퍼가 적용된 데이터 사용)
fig.add_trace(
    px.Scatter(x=df_filtered_buffered.index, y=df_filtered_buffered['Close'], name="주가 (종가)", line=dict(color='royalblue', width=1)),
    row=1, col=1
)

# 내가 만든 고유 지표를 하단 차트에 따로 그리기 (빨간 점선, 버퍼가 적용된 데이터 사용)
fig.add_trace(
    px.Scatter(x=df_stat_filtered_buffered.Date, y=df_stat_filtered_buffered['dfs'], name="배당수익률(DFS)", line=dict(color='firebrick', width=1, dash='solid')),
    row=2, col=1
)

# 6. 배당 지급일 및 배당 주기 변경일에 보조선(수직선) 추가
if not df_div_period.empty and 'Date' in df_div_period.columns and 'period' in df_div_period.columns:
    import plotly.io as pio
    try:
        template = pio.templates.get("plotly_dark")
        grid_color = None
        if template and hasattr(template, "layout"):
            yaxis = getattr(template.layout, "yaxis", None)
            if yaxis:
                grid_color = getattr(yaxis, "gridcolor", None)
        if not grid_color:
            grid_color = "#444"
    except Exception:
        grid_color = "#444"

    df_div_period_temp = df_div_period.copy()
    df_div_period_temp['Date'] = pd.to_datetime(df_div_period_temp['Date']).dt.tz_localize(None)
    
    # period 값의 변화 감지 (이전 행과 값이 다르면 변경된 것으로 판단, 최초 행도 변경된 것으로 봄)
    df_div_period_temp['period_changed'] = df_div_period_temp['period'] != df_div_period_temp['period'].shift()
    
    # 현재 조회 기간 내의 데이터만 필터링
    df_div_period_filtered = df_div_period_temp[
        (df_div_period_temp['Date'] >= pd.to_datetime(start_date)) & 
        (df_div_period_temp['Date'] <= pd.to_datetime(end_date))
    ]
    
    tick_vals = []
    for _, row in df_div_period_filtered.iterrows():
        date_val = row['Date']
        # Plotly가 날짜 축에서 올바르게 인식하도록 파이썬 내장 datetime 객체로 변환
        date_val_dt = pd.to_datetime(date_val).to_pydatetime()
        
        is_period_changed = row['period_changed']
        line_style = dict(
            width=1.5 if is_period_changed else 1,
            dash="solid" if is_period_changed else "dot",
            color="rgba(128, 128, 128, 0.4)"
        )
        
        # Row 1 (상단 주가 그래프) 세로선 추가 - y domain (0~1) 지정하여 공백 제거
        fig.add_shape(
            type="line",
            x0=date_val_dt, x1=date_val_dt,
            y0=0, y1=1,
            xref="x",
            yref="y domain",
            line=line_style,
            layer="below"
        )
        
        # Row 2 (하단 배당수익률 그래프) 세로선 추가 - y2 domain (0~1) 지정하여 공백 제거
        fig.add_shape(
            type="line",
            x0=date_val_dt, x1=date_val_dt,
            y0=0, y1=1,
            xref="x2",
            yref="y2 domain",
            line=line_style,
            layer="below"
        )
        
        if is_period_changed:
            # x축 눈금 표시를 위해 저장
            tick_vals.append(date_val_dt)

    # period 변경 지점들에 x축 눈금(ticks) 설정
    if tick_vals:
        # Plotly가 날짜 축에서 올바르게 직렬화하여 인식할 수 있도록 pandas Timestamp를 파이썬 내장 datetime 객체로 변환합니다.
        tick_vals_dt = [pd.to_datetime(d).to_pydatetime() for d in tick_vals]
        tick_text = [pd.to_datetime(d).strftime('%Y-%m') for d in tick_vals]
        fig.update_xaxes(
            tickmode="array",
            tickvals=tick_vals_dt,
            ticktext=tick_text,
            showticklabels=True
        )
    else:
        fig.update_xaxes(showticklabels=True)

# 차트 레이아웃 조정 (HTS 느낌 내기)
fig.update_layout(
    title=f"{ticker} 주가 및 배당수익률 트렌드",
    hovermode="x", # 마우스를 올리면 같은 날짜의 모든 지표를 한눈에 보여줌 (HTS 핵심 기능)
    template="plotly_dark", # 어두운 HTS 테마 느낌
    height=800, # 2단 분할이므로 높이를 충분히 확보
    legend=dict(
        orientation="h",      # 범례를 가로 방향으로 배치
        yanchor="bottom",
        y=1.02,               # 차트 위쪽 배치
        xanchor="right",
        x=1                   # 우측 정렬
    ),
    margin=dict(l=50, r=20, t=80, b=50) # 범례가 빠져나간 만큼 우측 여백을 최소화하여 차트 가로 너비를 극대화
)

# 각 서브플롯의 y축 및 x축 제목 설정 (y축 줌 고정 포함)
fig.update_yaxes(title_text="가격 ($)", fixedrange=True, row=1, col=1)
fig.update_yaxes(title_text="배당수익률(DFS)", fixedrange=True, row=2, col=1)
fig.update_xaxes(title_text="날짜", row=2, col=1)

# x축 및 y축 선을 표시하도록 설정 (테두리 박스가 아닌 단일 축 선으로 설정)
fig.update_xaxes(
    type="date",            # x축을 날짜 축으로 강제하여 rangeselector(기간 선택 버튼) 클릭 시 줌 기능이 실행되도록 함
    showline=True, 
    linewidth=1, 
    linecolor='rgba(255, 255, 255, 0.8)', 
    mirror=False,
    ticks="outside",        # 눈금 표시선(Tick mark)을 축 바깥쪽으로 표시
    ticklen=5,              # 눈금 표시선 길이
    tickwidth=1,            # 눈금 표시선 두께를 1로 글로벌 통일
    tickcolor="grey",       # 눈금 표시선 색상
    rangeslider=dict(visible=False) # 범위 선택기(Rangeselector)로 인해 자동으로 꽂힌 슬라이더 공백을 완전히 제거 (세로 보조선과 만나는 지점)
)
fig.update_yaxes(showline=True, linewidth=1, linecolor='rgba(255, 255, 255, 0.3)', mirror=False)

# 마우스 호버 시 수직/수평 십자 보조선(Spikeline) 활성화 (HTS 스타일)
fig.update_xaxes(
    showspikes=True,
    spikethickness=1,
    spikedash="dot",
    spikecolor="grey",
    spikemode="across",
    spikesnap="data",
    hoverformat="%Y-%m-%d"  # 날짜 표시 형식 설정 (예: 2025-03-15)
)

# 하단 메인 차트(row=2, col=1)의 X축에 범위 선택 버튼(Rangeselector) 추가 (matches="x2"에 따른 줌 동작 활성화)
fig.update_xaxes(
    rangeselector=dict(
        buttons=list([
            dict(count=3, label="3M", step="month", stepmode="backward"),
            dict(count=6, label="6M", step="month", stepmode="backward"),
            dict(count=1, label="1Y", step="year", stepmode="backward"),
            dict(count=5, label="5Y", step="year", stepmode="backward"),
            dict(step="all", label="ALL")
        ]),
        bgcolor="rgba(30, 30, 30, 0.8)",  # 다크 테마 배경에 조화되도록 설정
        activecolor="royalblue",
        font=dict(color="white", size=11),
        x=0,                            # 왼쪽 정렬
        y=1.02,                         # 범례(Legend)와 동일한 전체 차트 최상단 높이로 지정
        xanchor="left",
        yanchor="bottom"
    ),
    row=2, col=1
)
fig.update_yaxes(
    showspikes=True,
    spikethickness=1,
    spikedash="dot",
    spikecolor="grey",
    spikemode="across",
    spikesnap="data"
)

# 5. 웹 화면에 차트 띄우기
st.plotly_chart(fig, use_container_width=True, config={'scrollZoom': True})
