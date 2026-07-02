import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.graph_objects as px
from plotly.subplots import make_subplots
import div_yf as dyf

st.title("미국 배당주 모니터링")

# 1. 자산 및 기간 선택 UI
col1, col2 = st.columns([5, 1])
with col1:
    ticker = st.text_input("티커 입력", "").strip()
with col2:
    st.markdown("<div style='padding-top: 28px;'></div>", unsafe_allow_html=True)
    st.button("조회", use_container_width=True)

if not ticker:
    st.info("차트를 조회하려면 티커를 입력해주세요. (예: QCOM, AAPL, TSLA)")
    st.stop()

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
df_com_filtered = df_com[
    (df_com['start_date'] >= pd.to_datetime(start_date)) & 
    (df_com['start_date'] <= pd.to_datetime(end_date))
].copy()

# 우측 버퍼(5%)를 데이터 자체에 강제로 공백 행(NaN)으로 주입하여 
# rangeselector(기간 선택 버튼) 및 double-click 리셋 기능과의 충돌(여백 초기화 현상)을 완벽히 해결합니다.
if not df_filtered.empty:
    start_dt = pd.to_datetime(start_date)
    end_dt = pd.to_datetime(end_date)
    time_buffer = (end_dt - start_dt) * 0.05
    buffer_date = end_dt + time_buffer
    
    # 주가 데이터프레임 복사 후 마지막에 NaN값을 가지는 버퍼 날짜 행 추가
    last_row = df_filtered.tail(1).copy()
    last_row.index = [buffer_date]
    last_row.iloc[0] = None
    df_filtered_buffered = pd.concat([df_filtered, last_row]).sort_index()
    
    # 배당 데이터프레임 복사 후 마지막에 Date만 채워진 행 추가
    if not df_stat_filtered.empty:
        last_row_stat = df_stat_filtered.tail(1).copy()
        last_row_stat.index = [len(df_stat_filtered)]
        last_row_stat.iloc[0] = None
        last_row_stat.loc[last_row_stat.index[0], 'Date'] = buffer_date
        df_stat_filtered_buffered = pd.concat([df_stat_filtered, last_row_stat], ignore_index=True)
    else:
        new_row_stat = pd.DataFrame(columns=df_stat_filtered.columns, index=[0])
        new_row_stat.loc[0, 'Date'] = buffer_date
        df_stat_filtered_buffered = pd.concat([df_stat_filtered, new_row_stat], ignore_index=True)
        
    df_stat_filtered_buffered = df_stat_filtered_buffered.sort_values('Date').reset_index(drop=True)
else:
    df_filtered_buffered = df_filtered
    df_stat_filtered_buffered = df_stat_filtered

# 5. Plotly를 이용한 HTS식 레이어 차트 그리기 (5단 분할 차트)
fig = make_subplots(
    rows=5, cols=1,
    shared_xaxes=True,
    vertical_spacing=0.07,
    subplot_titles=(
        f"{ticker} 주가 (종가)", 
        "배당수익률(DFS)", 
        "배당금 (Adjusted Dividend)", 
        "배당 성장률 (Dividend Growth)",
        "주가 비교 (Close vs Adj Close)"
    ),
    row_heights=[0.2, 0.2, 0.2, 0.2, 0.2]
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

# 3단: 배당금 (Adjusted Dividend) - 단계형 선(step line)으로 구성
fig.add_trace(
    px.Scatter(
        x=df_stat_filtered_buffered.Date, 
        y=df_stat_filtered_buffered['adj_div'], 
        name="배당금 ($)", 
        line=dict(color='darkorange', width=1.5, shape='hv')
    ),
    row=3, col=1
)

# 4단: 배당 성장률 (div_change) - 단계형 영역 차트(Step Area Chart)로 구성
fig.add_trace(
    px.Scatter(
        x=df_stat_filtered_buffered.Date,
        y=df_stat_filtered_buffered['div_change'] * 100,
        name="배당 성장률 (%)",
        line=dict(color='rgba(46, 204, 113, 1)', width=1.5, shape='hv'),
        fill='tozeroy',
        fillcolor='rgba(46, 204, 113, 0.15)'  # 반투명 초록색으로 하단 영역 채움
    ),
    row=4, col=1
)

# 5단: 주가 비교 (Close vs Adj Close)
fig.add_trace(
    px.Scatter(
        x=df_filtered_buffered.index, 
        y=df_filtered_buffered['Close'], 
        name="Close", 
        line=dict(color='royalblue', width=1)
    ),
    row=5, col=1
)
fig.add_trace(
    px.Scatter(
        x=df_filtered_buffered.index, 
        y=df_filtered_buffered['Adj Close'], 
        name="Adj Close", 
        line=dict(color='limegreen', width=1)
    ),
    row=5, col=1
)

# 0% 기준선(점선) 추가
fig.add_hline(
    y=0,
    line_dash="dash",
    line_color="rgba(255, 255, 255, 0.3)",
    line_width=1,
    row=4,
    col=1
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
        
        # Row 1 세로선
        fig.add_shape(
            type="line",
            x0=date_val_dt, x1=date_val_dt,
            y0=0, y1=1,
            xref="x",
            yref="y domain",
            line=line_style,
            layer="below"
        )
        
        # Row 2 세로선
        fig.add_shape(
            type="line",
            x0=date_val_dt, x1=date_val_dt,
            y0=0, y1=1,
            xref="x2",
            yref="y2 domain",
            line=line_style,
            layer="below"
        )

        # Row 3 세로선
        fig.add_shape(
            type="line",
            x0=date_val_dt, x1=date_val_dt,
            y0=0, y1=1,
            xref="x3",
            yref="y3 domain",
            line=line_style,
            layer="below"
        )

        # Row 4 세로선
        fig.add_shape(
            type="line",
            x0=date_val_dt, x1=date_val_dt,
            y0=0, y1=1,
            xref="x4",
            yref="y4 domain",
            line=line_style,
            layer="below"
        )

        # Row 5 세로선
        fig.add_shape(
            type="line",
            x0=date_val_dt, x1=date_val_dt,
            y0=0, y1=1,
            xref="x5",
            yref="y5 domain",
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
    title=dict(
        text=f"{ticker} 주가 및 배당 트렌드 분석",
        x=0.5,
        xanchor="center",
        y=0.99,
        yanchor="top"
    ),
    hovermode="x", # 마우스를 올리면 같은 날짜의 모든 지표를 한눈에 보여줌 (HTS 핵심 기능)
    template="plotly_dark", # 어두운 HTS 테마 느낌
    height=2500, # 5단 분할이므로 높이를 충분히 확보
    showlegend=False, # 전체 범례를 숨기고 개별 그래프 내부에 표시
    margin=dict(l=50, r=20, t=110, b=50) # 여백을 조정하여 타이틀과 버튼 영역 확보
)

# 서브플롯 제목들의 폰트 크기 및 위치 조정 (위쪽 겹침을 방지하기 위해 조금만 위로 띄움)
for annotation in fig.layout.annotations:
    annotation.font.size = 12
    annotation.y = annotation.y + 0.007

# 7. 각 서브플롯 내부 좌측 상단에 개별 범례(Legend) 표시 (HTS 스타일)
fig.add_annotation(
    text="<span style='color:royalblue'>■</span> 주가 (종가)",
    xref="x domain", yref="y domain",
    x=0.01, y=0.95,
    showarrow=False,
    font=dict(size=11, color="white"),
    bgcolor="rgba(30, 30, 30, 0.75)",
    bordercolor="rgba(128, 128, 128, 0.3)",
    borderwidth=1,
    borderpad=4,
    xanchor="left", yanchor="top",
    row=1, col=1
)
fig.add_annotation(
    text="<span style='color:firebrick'>■</span> 배당수익률(DFS)",
    xref="x2 domain", yref="y2 domain",
    x=0.01, y=0.95,
    showarrow=False,
    font=dict(size=11, color="white"),
    bgcolor="rgba(30, 30, 30, 0.75)",
    bordercolor="rgba(128, 128, 128, 0.3)",
    borderwidth=1,
    borderpad=4,
    xanchor="left", yanchor="top",
    row=2, col=1
)
fig.add_annotation(
    text="<span style='color:darkorange'>■</span> 배당금 ($)",
    xref="x3 domain", yref="y3 domain",
    x=0.01, y=0.95,
    showarrow=False,
    font=dict(size=11, color="white"),
    bgcolor="rgba(30, 30, 30, 0.75)",
    bordercolor="rgba(128, 128, 128, 0.3)",
    borderwidth=1,
    borderpad=4,
    xanchor="left", yanchor="top",
    row=3, col=1
)
fig.add_annotation(
    text="<span style='color:rgba(46, 204, 113, 1)'>■</span> 배당 성장률 (%)",
    xref="x4 domain", yref="y4 domain",
    x=0.01, y=0.95,
    showarrow=False,
    font=dict(size=11, color="white"),
    bgcolor="rgba(30, 30, 30, 0.75)",
    bordercolor="rgba(128, 128, 128, 0.3)",
    borderwidth=1,
    borderpad=4,
    xanchor="left", yanchor="top",
    row=4, col=1
)
fig.add_annotation(
    text="<span style='color:royalblue'>■</span> Close &nbsp;&nbsp;&nbsp;&nbsp; <span style='color:limegreen'>■</span> Adj Close",
    xref="x5 domain", yref="y5 domain",
    x=0.01, y=0.95,
    showarrow=False,
    font=dict(size=11, color="white"),
    bgcolor="rgba(30, 30, 30, 0.75)",
    bordercolor="rgba(128, 128, 128, 0.3)",
    borderwidth=1,
    borderpad=4,
    xanchor="left", yanchor="top",
    row=5, col=1
)

# 각 서브플롯의 y축 및 x축 제목 설정 (y축 줌 고정 포함)
fig.update_yaxes(title_text="가격 ($)", fixedrange=True, row=1, col=1)
fig.update_yaxes(title_text="배당수익률(DFS)", fixedrange=True, row=2, col=1)
fig.update_yaxes(title_text="배당금 ($)", fixedrange=True, row=3, col=1)
fig.update_yaxes(title_text="배당 성장률 (%)", fixedrange=True, row=4, col=1)
fig.update_yaxes(title_text="주가 비교 ($)", fixedrange=True, row=5, col=1)
fig.update_xaxes(title_text="날짜", row=5, col=1)

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

# 하단 메인 차트(row=5, col=1)의 X축에 범위 선택 버튼(Rangeselector) 추가 (matches="x5"에 따른 줌 동작 활성화)
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
    row=5, col=1
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
