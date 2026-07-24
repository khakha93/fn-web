import importlib
import div_yf as dyf
importlib.reload(dyf)
import sheets_helper as sh
importlib.reload(sh)
import notion_helper as nh
importlib.reload(nh)
import streamlit as st
import yfinance as yf
import pandas as pd
import warnings
import plotly.graph_objects as px
from plotly.subplots import make_subplots
import os
import datetime

# Streamlit/yfinance 내부 expire_cache 관련 비동기 RuntimeWarning 무시
warnings.filterwarnings("ignore", category=RuntimeWarning, message=".*expire_cache.*")

# 앱 기동 시 구글 시트 테이블 초기화 (캐시 처리)
@st.cache_resource
def run_init_sheets():
    sh.init_sheets()

run_init_sheets()

# --- 1. 구글 시트 읽기 기능 캐싱 및 유틸리티 캐시 정의 ---
@st.cache_data(ttl=300)
def get_stocks_cached():
    return sh.get_stocks()

@st.cache_data(ttl=60)
def get_portfolio_cached():
    return sh.get_portfolio()

@st.cache_data(ttl=60)
def get_watchlist_cached():
    return sh.get_watchlist()

@st.cache_data(ttl=60)
def get_watchlist_details_cached():
    return sh.get_watchlist_details()

@st.cache_data(ttl=60)
def get_alerts_cached():
    return sh.get_alerts()

@st.cache_data(ttl=60)
def get_trading_history_cached():
    return sh.get_trading_history()

@st.cache_data(ttl=60)
def get_comment_cached(ticker):
    return sh.get_comment(ticker)

@st.cache_data(ttl=60)
def get_comments_list_cached(ticker):
    return sh.get_comments_list(ticker)

# 야후 파이낸스 실시간 주가 알림용 캐시 (체크 주기 30초)
@st.cache_data(ttl=30)
def get_alert_prices_cached(tickers_to_check):
    if not tickers_to_check:
        return pd.DataFrame()
    return yf.download(tickers_to_check, period="1d", interval="1m", progress=False)

@st.cache_data
def get_stock_data(ticker):
    # 캐시 폴더 생성
    os.makedirs("cache", exist_ok=True)
    price_cache_path = f"cache/{ticker}_price.csv"
    div_cache_path = f"cache/{ticker}_div.csv"

    session = dyf.get_yf_session()

    # 1. 주가 데이터 (df_price) 처리
    df_price = None
    if os.path.exists(price_cache_path):
        try:
            df_price = pd.read_csv(price_cache_path, index_col=0, parse_dates=True)
            if isinstance(df_price.columns, pd.MultiIndex):
                df_price.columns = df_price.columns.droplevel(1)
            
            # 타임존 제거
            df_price.index = df_price.index.tz_localize(None)
            
            # 마지막 캐시 날짜 확인
            last_cached_date = df_price.index.max()
            today = datetime.date.today()
            
            # 하루 이상 차이가 날 경우, 최근 5일치 데이터를 다운로드하여 병합
            if (today - last_cached_date.date()).days >= 1:
                df_recent = yf.download(ticker, period="5d", auto_adjust=False, session=session)
                if not df_recent.empty:
                    if isinstance(df_recent.columns, pd.MultiIndex):
                        df_recent.columns = df_recent.columns.droplevel(1)
                    df_recent.index = df_recent.index.tz_localize(None)
                    
                    df_price = pd.concat([df_price, df_recent])
                    df_price = df_price[~df_price.index.duplicated(keep='last')].sort_index()
                    df_price.to_csv(price_cache_path)
        except Exception:
            df_price = None

    if df_price is None or df_price.empty:
        df_price = yf.download(ticker, period="max", auto_adjust=False, session=session)
        if isinstance(df_price.columns, pd.MultiIndex):
            df_price.columns = df_price.columns.droplevel(1)
        df_price.index = df_price.index.tz_localize(None)
        df_price.to_csv(price_cache_path)

    df_close = df_price['Close'].copy()

    # 2. 배당 데이터 (df_div) 처리
    df_div = None
    if os.path.exists(div_cache_path):
        try:
            file_mtime = datetime.datetime.fromtimestamp(os.path.getmtime(div_cache_path))
            # 배당 데이터는 자주 변하지 않으므로 3일간 캐시 유효
            if datetime.datetime.now() - file_mtime < datetime.timedelta(days=3):
                df_div = pd.read_csv(div_cache_path, parse_dates=['Date'])
        except Exception:
            df_div = None

    if df_div is None or df_div.empty:
        df_div = dyf.get_yf_dividend_history(ticker, session=session)
        df_div.to_csv(div_cache_path, index=False)

    # 배당수익률 지표 계산 로직
    df_div_period = dyf.add_period_columns_by_div(df_div)
    df_com = dyf.group_by_period_by_div(df_div_period)
    _, df_stat = dyf.merge_dividend_data(df_close, df_com)
    
    # 데이터의 날짜 범위 확인 (timezone 제거하여 일치시킴)
    df_stat['Date'] = pd.to_datetime(df_stat['Date']).dt.tz_localize(None)
    
    return df_price, df_stat, df_div_period, df_com


# --- 2. 알림 조건 검사 및 알림 표출 기능 ---
def check_price_alerts():
    """설정된 조건부 타겟 가격 알림 중, 도달한 것이 있는지 yfinance 최신 주가와 비교하여 toast로 띄웁니다."""
    if "alerts_checked" not in st.session_state:
        st.session_state.alerts_checked = {}

    try:
        alerts_df = get_alerts_cached()
    except Exception:
        return

    if alerts_df.empty:
        return

    active_alerts = alerts_df[alerts_df["is_triggered"] == False]
    if active_alerts.empty:
        return

    tickers_to_check = active_alerts["symbol"].unique().tolist()

    try:
        # 캐시된 야후 파이낸스 다운로더 사용 (30초 TTL)
        price_data = get_alert_prices_cached(tickers_to_check)
        if price_data.empty:
            return

        current_prices = {}
        if len(tickers_to_check) == 1:
            ticker = tickers_to_check[0]
            if "Close" in price_data.columns:
                current_prices[ticker] = float(price_data["Close"].squeeze().iloc[-1])
        else:
            for t in tickers_to_check:
                try:
                    if "Close" in price_data.columns and t in price_data["Close"].columns:
                        current_prices[t] = float(price_data["Close"][t].iloc[-1])
                except Exception:
                    pass
    except Exception:
        return

    for _, row in active_alerts.iterrows():
        sym = row["symbol"]
        target = float(row["target_price"])
        cond = str(row["condition_type"]).strip()
        
        curr_p = current_prices.get(sym, 0.0)
        if curr_p == 0.0:
            continue

        alert_key = f"{sym}_{target}_{cond}"

        if st.session_state.alerts_checked.get(alert_key):
            continue

        # 연산자 파싱을 통한 조건부 타겟 도달 여부 판별
        triggered = False
        if cond == "above" or cond == ">=":
            triggered = (curr_p >= target)
        elif cond == ">":
            triggered = (curr_p > target)
        elif cond == "below" or cond == "<=":
            triggered = (curr_p <= target)
        elif cond == "<":
            triggered = (curr_p < target)
        elif cond == "==":
            triggered = (abs(curr_p - target) < 0.001)

        if triggered:
            if cond in [">=", "above"]:
                cond_str = "상승 돌파 (>=)"
            elif cond == ">":
                cond_str = "상승 돌파 (>)"
            elif cond in ["<=", "below"]:
                cond_str = "하락 돌파 (<=)"
            elif cond == "<":
                cond_str = "하락 돌파 (<)"
            elif cond == "==":
                cond_str = "가격 일치 (==)"
            else:
                cond_str = cond

            st.toast(f"🔔 **[조건부 타겟 도달]** {sym}의 가격이 ${target:.2f}을 {cond_str}했습니다! (현재가: ${curr_p:.2f})", icon="🎯")
            sh.set_alert_triggered(sym, cond, True)
            st.session_state.alerts_checked[alert_key] = True
            st.cache_data.clear()


check_price_alerts()


# --- 3. 세션 상태 및 사이드바 내비게이션 구성 ---
# Session State를 이용한 메뉴 및 기본 티커 상태 초기화
if "menu" not in st.session_state:
    st.session_state.menu = "💼 내 투자 관리"
if "ticker" not in st.session_state:
    st.session_state.ticker = ""

# 사이드바 네비게이션 구성
st.sidebar.title("💰 메뉴 내비게이션")
menu_options = ["💼 내 투자 관리", "📋 전체 종목 리스트", "📊 개별 종목 분석"]
default_menu_index = menu_options.index(st.session_state.menu) if st.session_state.menu in menu_options else 0

selected_menu = st.sidebar.radio(
    "메뉴 선택",
    menu_options,
    index=default_menu_index
)

# 사용자 클릭으로 메뉴가 바뀐 경우 동기화
if selected_menu != st.session_state.menu:
    st.session_state.menu = selected_menu
    st.rerun()

# --- 메인 상단 공통 헤더 및 신속 조회 검색 바 ---
# 실시간 대문자 변환 및 데스크탑 풀와이드 스틱키 헤더 CSS 주입
st.markdown("""
<style>
/* Streamlit 기본 헤더 바를 투명하게 만들어 관리 도구(우측 상단 툴바) 및 토글 버튼들이 정상적으로 작동하도록 함 */
header[data-testid="stHeader"] {
    background-color: transparent !important;
}

/* 사이드바 접힘 상태의 열기 버튼 컨테이너(collapsedSidebarCodegen)가 풀와이드 헤더 위에 둥둥 떠서 항상 노출되도록 보정 */
[data-testid="collapsedSidebarCodegen"] {
    z-index: 999999 !important;
}
[data-testid="stSidebarCollapseButton"] {
    z-index: 999999 !important;
}
[data-testid="stSidebarCollapseButton"] button {
    color: #38bdf8 !important; /* 사이언 네온 단추 색상 적용 */
}

div[data-testid="stTextInput"] input {
    text-transform: uppercase !important;
}

/* 탑 네비게이션 바 스타일링 (st.container key="top_header_container" 연동) */
div.st-key-top_header_container {
    position: sticky !important;
    top: -6rem !important;
    background: linear-gradient(90deg, #0f172a 0%, #1e293b 100%) !important;
    border-bottom: 2px solid #38bdf8 !important; /* 사이언 네온 아웃라인 하단 배치 */
    border-radius: 0px !important;
    z-index: 99 !important; /* 사이드바 아래 레이어에 둠 */
    box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.4) !important;
    width: auto !important;
    
    /* 기본 모바일/패드 반응형 여백 초기화 */
    margin-left: -1rem !important;
    margin-right: -1rem !important;
    margin-top: -6rem !important;
    padding: 1.2rem 1.5rem !important;
}

/* 데스크탑 화면 패딩 오프셋 보정 (Streamlit 기본 block-container 패딩 상쇄) */
@media (min-width: 768px) {
    div.st-key-top_header_container {
        margin-left: -5rem !important;
        margin-right: -5rem !important;
        margin-top: -6rem !important;
        padding-left: 5rem !important;
        padding-right: 5rem !important;
    }
}

div.st-key-top_header_container h2 {
    color: #38bdf8 !important; /* 타이틀 사이언 블루 강조 */
    font-weight: 800 !important;
    margin: 0 !important;
    line-height: 40px !important;
}
</style>
""", unsafe_allow_html=True)

def make_hdr_ticker_uppercase():
    if "hdr_ticker_input" in st.session_state:
        st.session_state.hdr_ticker_input = st.session_state.hdr_ticker_input.strip().upper()

with st.container(border=True, key="top_header_container"):
    col_hdr_title, col_hdr_search = st.columns([5, 3])
    with col_hdr_title:
        st.markdown("<h2 style='margin:0; padding:0; line-height: 40px;'>💰 배당 모니터링 시스템</h2>", unsafe_allow_html=True)
    with col_hdr_search:
        col_inp, col_btn = st.columns([3, 1])
        with col_inp:
            hdr_ticker_input = st.text_input(
                "🔍 종목 신속 조회 (티커 입력)", 
                value=st.session_state.ticker, 
                label_visibility="collapsed", 
                key="hdr_ticker_input",
                on_change=make_hdr_ticker_uppercase
            ).strip().upper()
        with col_btn:
            hdr_query_btn = st.button("조회", use_container_width=True, key="hdr_query_btn")

if hdr_query_btn or (hdr_ticker_input and hdr_ticker_input != st.session_state.ticker):
    st.session_state.ticker = hdr_ticker_input
    st.session_state.menu = "📊 개별 종목 분석"
    st.cache_data.clear()
    st.rerun()

st.divider()

# 1. 개별 종목 분석 페이지
if st.session_state.menu == "📊 개별 종목 분석":
    ticker = st.session_state.ticker

    if not ticker:
        st.info("사이드바의 '🔍 종목 신속 조회' 입력창에 분석할 티커를 입력해 주세요. (예: QCOM, KO, PG)")
        st.stop()

    with st.spinner("데이터 로딩 및 차트 작성 중..."):
        try:
            df_price, df_stat, df_div_period, df_com = get_stock_data(ticker)
        except Exception as e:
            st.error(f"데이터 조회에 실패했습니다. 올바른 티커명이거나 배당 내역이 존재하는지 확인해 주세요. 에러: {e}")
            st.stop()

    min_date = df_price.index.min().to_pydatetime().date()
    max_date = df_price.index.max().to_pydatetime().date()

    # 기본 조회 범위를 2010년 1월 1일로 설정
    default_start = max(min_date, pd.to_datetime("2010-01-01").date())

    # 세션 상태가 현재 티커의 날짜 경계를 벗어나지 않도록 보정 (티커 변경 시 날짜 범위 오류 예방)
    if "start_date" in st.session_state:
        st.session_state.start_date = max(min_date, min(st.session_state.start_date, max_date))
    else:
        st.session_state.start_date = default_start

    if "end_date" in st.session_state:
        st.session_state.end_date = max(min_date, min(st.session_state.end_date, max_date))
    else:
        st.session_state.end_date = max_date

    # 상세 기간 설정용 expander 추가 (모바일 화면 최적화)
    with st.expander("📅 상세 기간 직접 설정 (날짜 지정)", expanded=False):
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
            submitted = st.form_submit_button(label="기간 적용 및 조회", width="stretch")

    if submitted:
        if start_input > end_input:
            st.error("시작일은 종료일보다 이전이어야 합니다.")
        else:
            st.session_state.start_date = start_input
            st.session_state.end_date = end_input

    start_date = st.session_state.start_date
    end_date = st.session_state.end_date

    def conditional_fragment(func):
        if hasattr(st, "fragment"):
            return st.fragment()(func)
        return func

    @conditional_fragment
    def render_chart_section(ticker, df_price, df_stat, df_div_period, df_com, start_date, end_date):
        tab1, tab2 = st.tabs(["📊 분석 차트", "📜 배당 상세 내역"])
        with tab1:
            period_options = {
                "전체": None,
                "5년": pd.Timedelta(days=365 * 5),
                "1년": pd.Timedelta(days=365),
                "6개월": pd.Timedelta(days=180),
                "3개월": pd.Timedelta(days=90)
            }

            selected_label = st.radio(
                "조회 기간 (Quick Selector)",
                options=list(period_options.keys()),
                index=0,
                horizontal=True
            )

            actual_end_date = pd.to_datetime(end_date)
            if period_options[selected_label] is not None:
                actual_start_date = actual_end_date - period_options[selected_label]
                actual_start_date = max(actual_start_date, df_price.index.min())
            else:
                actual_start_date = pd.to_datetime(start_date)

            df_filtered = df_price.loc[actual_start_date:actual_end_date]
            df_stat_filtered = df_stat[
                (df_stat['Date'] >= actual_start_date) & 
                (df_stat['Date'] <= actual_end_date)
            ]
            df_com_filtered = df_com[
                (df_com['start_date'] >= actual_start_date) & 
                (df_com['start_date'] <= actual_end_date)
            ].copy()

            if not df_filtered.empty:
                time_buffer = (actual_end_date - actual_start_date) * 0.05
                buffer_date = actual_end_date + time_buffer

                last_row = df_filtered.tail(1).copy()
                last_row.index = [buffer_date]
                last_row.iloc[0] = None
                df_filtered_buffered = pd.concat([df_filtered, last_row]).sort_index()

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

            fig.add_trace(
                px.Scatter(x=df_filtered_buffered.index, y=df_filtered_buffered['Close'], name="주가 (종가)", line=dict(color='royalblue', width=1), hovertemplate="%{y}<extra></extra>"),
                row=1, col=1
            )

            fig.add_trace(
                px.Scatter(x=df_stat_filtered_buffered.Date, y=df_stat_filtered_buffered['dfs'], name="배당수익률(DFS)", line=dict(color='firebrick', width=1, dash='solid'), hovertemplate="%{y}<extra></extra>"),
                row=2, col=1
            )

            fig.add_trace(
                px.Scatter(
                    x=df_stat_filtered_buffered.Date, 
                    y=df_stat_filtered_buffered['adj_div'], 
                    name="배당금 ($)", 
                    line=dict(color='darkorange', width=1.5, shape='hv'),
                    hovertemplate="%{y}<extra></extra>"
                ),
                row=3, col=1
            )

            fig.add_trace(
                px.Scatter(
                    x=df_stat_filtered_buffered.Date,
                    y=df_stat_filtered_buffered['div_change'] * 100,
                    name="배당 성장률 (%)",
                    line=dict(color='rgba(46, 204, 113, 1)', width=1.5, shape='hv'),
                    fill='tozeroy',
                    fillcolor='rgba(46, 204, 113, 0.15)',
                    hovertemplate="%{y}<extra></extra>"
                ),
                row=4, col=1
            )

            fig.add_trace(
                px.Scatter(
                    x=df_filtered_buffered.index, 
                    y=df_filtered_buffered['Close'], 
                    name="Close", 
                    line=dict(color='royalblue', width=1),
                    hovertemplate="%{y}<extra></extra>"
                ),
                row=5, col=1
            )
            fig.add_trace(
                px.Scatter(
                    x=df_filtered_buffered.index, 
                    y=df_filtered_buffered['Adj Close'], 
                    name="Adj Close", 
                    line=dict(color='limegreen', width=1),
                    hovertemplate="%{y}<extra></extra>"
                ),
                row=5, col=1
            )

            fig.add_hline(
                y=0,
                line_dash="dash",
                line_color="rgba(255, 255, 255, 0.3)",
                line_width=1,
                row=4,
                col=1
            )

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
                df_div_period_temp['period_changed'] = df_div_period_temp['period'] != df_div_period_temp['period'].shift()

                df_div_period_filtered = df_div_period_temp[
                    (df_div_period_temp['Date'] >= pd.to_datetime(actual_start_date)) & 
                    (df_div_period_temp['Date'] <= pd.to_datetime(actual_end_date))
                ]

                tick_vals = []
                for _, row in df_div_period_filtered.iterrows():
                    date_val = row['Date']
                    date_val_dt = pd.to_datetime(date_val).to_pydatetime()
                    is_period_changed = row['period_changed']
                    line_style = dict(
                        width=1.5 if is_period_changed else 1,
                        dash="solid" if is_period_changed else "dot",
                        color="rgba(128, 128, 128, 0.4)"
                    )

                    for r in range(1, 6):
                        fig.add_shape(
                            type="line",
                            x0=date_val_dt, x1=date_val_dt,
                            y0=0, y1=1,
                            xref=f"x{r}" if r > 1 else "x",
                            yref=f"y{r} domain" if r > 1 else "y domain",
                            line=line_style,
                            layer="below"
                        )

                    if is_period_changed:
                        tick_vals.append(date_val_dt)

                if tick_vals:
                    tick_vals_dt = [pd.to_datetime(d).to_pydatetime() for d in tick_vals]
                    tick_text = [pd.to_datetime(d).strftime('%Y-%m') for d in tick_vals]
                    fig.update_xaxes(
                        tickmode="array",
                        tickvals=tick_vals_dt,
                        ticktext=tick_text,
                        showticklabels=True,
                        tickangle=-45
                    )
                else:
                    fig.update_xaxes(showticklabels=True, tickangle=-45)

            fig.update_layout(
                title=dict(
                    text=f"{ticker} 주가 및 배당 분석",
                    x=0.5,
                    xanchor="center",
                    y=0.965,
                    yanchor="top"
                ),
                hovermode="x unified",
                template="plotly_dark",
                height=2000,
                showlegend=False,
                margin=dict(l=50, r=20, t=120, b=50),
                hoverlabel=dict(
                    bgcolor="rgba(33, 37, 41, 0.3)",
                    font_color="white",
                    font_size=11,
                    bordercolor="rgba(255, 255, 255, 0.1)"
                )
            )

            for annotation in fig.layout.annotations:
                annotation.font.size = 12
                annotation.y = annotation.y + 0.007

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

            fig.update_yaxes(
                fixedrange=True,
                showline=True,
                linewidth=1,
                linecolor='rgba(255, 255, 255, 0.3)',
                mirror=False
            )

            fig.update_xaxes(
                type="date",
                showline=True, 
                linewidth=1, 
                linecolor='rgba(255, 255, 255, 0.8)', 
                mirror=False,
                ticks="outside",
                ticklen=5,
                tickwidth=1,
                tickcolor="grey",
                rangeslider=dict(visible=False)
            )

            fig.update_xaxes(
                showspikes=True,
                spikethickness=1,
                spikedash="dot",
                spikecolor="grey",
                spikemode="across",
                spikesnap="data",
                hoverformat="%Y-%m-%d"
            )

            fig.update_yaxes(
                showspikes=True,
                spikethickness=1,
                spikedash="dot",
                spikecolor="grey",
                spikemode="across",
                spikesnap="data"
            )

            st.plotly_chart(fig, width="stretch", config={'scrollZoom': True, 'doubleClick': 'reset'})

        with tab2:
            st.markdown("### 📜 배당 변동 주기별 상세 내역")
            st.markdown("각 배당금 지급 주기별 주요 통계 및 배당성장률 요약표입니다. (최신 주기 순 정렬)")
            
            if not df_com_filtered.empty:
                latest_row = df_com_filtered.iloc[-1]
                latest_div = latest_row['adj_div']
                latest_growth = latest_row['div_change'] * 100 if pd.notnull(latest_row['div_change']) else 0
                avg_growth = df_com_filtered['div_change'].mean() * 100 if df_com_filtered['div_change'].notnull().any() else 0
                
                m1, m2, m3 = st.columns(3)
                m1.metric("현재 연간 배당금 (환산)", f"${latest_div:.4f}")
                m2.metric("최근 배당 성장률", f"{latest_growth:+.2f}%" if pd.notnull(latest_row['div_change']) else "-")
                m3.metric("평균 배당 성장률", f"{avg_growth:+.2f}%")
                
                st.divider()
                
                df_display = df_com_filtered.copy()
                df_display = df_display.sort_values('period', ascending=False)
                
                df_display = df_display.rename(columns={
                    'period': '주기 ID',
                    'start_date': '시작일',
                    'end_date': '종료일',
                    'count': '지급 횟수',
                    'dividend_mean': '주당 배당금 (평균)',
                    'adj_div': '연간 환산 배당금',
                    'div_change': '배당 성장률'
                })
                
                cols_to_show = ['시작일', '종료일', '주당 배당금 (평균)', '지급 횟수', '연간 환산 배당금', '배당 성장률']
                df_display = df_display[cols_to_show]
                df_display['배당 성장률'] = df_display['배당 성장률'] * 100
                
                st.dataframe(
                    df_display,
                    width="stretch",
                    hide_index=True,
                    column_config={
                        "시작일": st.column_config.DateColumn("시작일", format="YYYY-MM-DD"),
                        "종료일": st.column_config.DateColumn("종료일", format="YYYY-MM-DD"),
                        "주당 배당금 (평균)": st.column_config.NumberColumn("주당 배당금 (평균)", format="$%.4f"),
                        "지급 횟수": st.column_config.NumberColumn("지급 횟수", format="%d회"),
                        "연간 환산 배당금": st.column_config.NumberColumn("연간 환산 배당금", format="$%.4f"),
                        "배당 성장률": st.column_config.NumberColumn("배당 성장률", format="%.2f%%")
                    }
                )
            else:
                st.info("선택한 기간 동안의 배당 변동 데이터가 없습니다.")

    # --- 1단계: 기본 종목 정보 지표 계산 ---
    try:
        current_price = float(df_price['Close'].iloc[-1])
        latest_div = float(df_com.iloc[-1]['adj_div']) if not df_com.empty else 0.0
        current_yield = latest_div / current_price if current_price > 0 else 0.0
    except Exception:
        current_price = 0.0
        current_yield = 0.0

    # 현재가와 배당수익률 정보를 타이틀 아래에 메트릭으로 노출
    m1, m2 = st.columns(2)
    m1.metric("현재 주가", f"${current_price:,.2f}")
    m2.metric("예상 연간 배당수익률", f"{current_yield * 100:.2f}%")

    st.divider()
    st.subheader(f"🛠️ {ticker} 통합 액션 패널")

    col_wl, col_al, col_pf = st.columns(3)

    # 1. 관심 종목 관리 (다중 그룹 소속 지원)
    with col_wl:
        st.markdown("##### ⭐ 관심 종목 설정")
        wl_details = get_watchlist_details_cached()
        
        # 현재 종목이 속한 모든 관심 그룹 조회
        my_groups = wl_details[wl_details['symbol'] == ticker]['group_name'].tolist() if not wl_details.empty else []
        
        if my_groups:
            st.markdown(f"**현재 소속 그룹**: " + ", ".join([f"`{g}`" for g in my_groups]))
        else:
            st.caption("현재 관심 종목에 등록되어 있지 않습니다.")
            
        all_groups = sorted(wl_details['group_name'].dropna().unique().tolist()) if not wl_details.empty else []
        if "기본 그룹" not in all_groups:
            all_groups.insert(0, "기본 그룹")
            
        group_sel = st.selectbox("추가할 관심 그룹 선택", all_groups + ["+ 새 그룹 추가..."], key="quick_wl_group")
        
        if group_sel == "+ 새 그룹 추가...":
            new_group = st.text_input("새 그룹명 입력", "", key="quick_wl_new_group").strip()
            group_to_save = new_group
        else:
            group_to_save = group_sel

        c_wl_btn1, c_wl_btn2 = st.columns(2)
        with c_wl_btn1:
            if st.button("⭐ 관심 그룹 추가", width="stretch", key="wl_save_btn", type="primary"):
                if group_sel == "+ 새 그룹 추가..." and not group_to_save:
                    st.error("그룹명을 입력해주세요.")
                elif group_to_save in my_groups:
                    st.warning("⚠️ 이미 해당 관심 그룹에 속해 있습니다.")
                else:
                    sh.add_to_watchlist(ticker, group_to_save)
                    if "quick_wl_group" in st.session_state:
                        del st.session_state["quick_wl_group"]
                    if "quick_wl_new_group" in st.session_state:
                        del st.session_state["quick_wl_new_group"]
                    st.cache_data.clear()
                    st.success(f"관심 그룹 '{group_to_save}'에 추가되었습니다!")
                    st.rerun()
        with c_wl_btn2:
            if my_groups:
                # 삭제할 소속 그룹 선택
                del_group_sel = st.selectbox("제거할 그룹 선택", my_groups, key="quick_wl_del_group")
                if st.button("🗑️ 그룹에서 해제", width="stretch", key="wl_del_btn"):
                    sh.remove_from_watchlist(ticker, del_group_sel)
                    st.cache_data.clear()
                    st.success(f"'{del_group_sel}' 그룹에서 해제 완료!")
                    st.rerun()
            else:
                st.button("🗑️ 그룹에서 해제", width="stretch", disabled=True, key="wl_del_btn_dis")

    # 2. 조건부 타겟 관리 (비교 연산자 직접 입력 복구)
    with col_al:
        st.markdown("##### 🎯 조건부 타겟 설정")
        alerts_df = get_alerts_cached()
        my_alerts = alerts_df[alerts_df['symbol'] == ticker]
        if not my_alerts.empty:
            alert_items = []
            for _, a_row in my_alerts.iterrows():
                op = a_row['condition_type']
                if op == "above":
                    cond_str = "상승 돌파 (above)"
                elif op == "below":
                    cond_str = "하락 돌파 (below)"
                else:
                    cond_str = op
                trig_str = "(도달완료)" if a_row['is_triggered'] else "(대기중)"
                alert_items.append(f"${a_row['target_price']:.2f} {cond_str} {trig_str}")
            st.caption("감시 중: " + ", ".join(alert_items))
        else:
            st.caption("설정된 타겟 가격이 없습니다.")

        cond_input = st.text_input("조건식 입력 (예: >= 150 또는 <= 50)", value=f">= {current_price:.2f}", key="quick_al_cond_text")

        c_al_btn1, c_al_btn2 = st.columns(2)
        with c_al_btn1:
            if st.button("🎯 타겟 등록", width="stretch", key="al_save_btn", type="primary"):
                import re
                cond_input = cond_input.strip()
                match = re.match(r"^([><]=?|==)\s*([0-9.]+)", cond_input)
                if match:
                    operator = match.group(1)
                    target_val = float(match.group(2))
                else:
                    try:
                        target_val = float(cond_input)
                        operator = ">=" if target_val >= current_price else "<="
                    except ValueError:
                        st.error("올바른 형식의 조건식을 입력해 주세요. (예: >= 150)")
                        st.stop()
                sh.save_alert(ticker, target_val, operator)
                if "quick_al_cond_text" in st.session_state:
                    del st.session_state["quick_al_cond_text"]
                st.cache_data.clear()
                st.success(f"타겟({operator} {target_val}) 저장 완료!")
                st.rerun()
        with c_al_btn2:
            if not my_alerts.empty:
                if st.button("🗑️ 전체 삭제", width="stretch", key="al_del_btn"):
                    for _, a_row in my_alerts.iterrows():
                        sh.remove_alert(ticker, a_row['condition_type'])
                    st.cache_data.clear()
                    st.success("타겟 조건 삭제 완료!")
                    st.rerun()
            else:
                st.button("🗑️ 전체 삭제", width="stretch", disabled=True, key="al_del_btn_dis")

    # 3. 포트폴리오 관리 (정수 단위 step=1.0 및 추가매수/청산 분할 지원)
    with col_pf:
        st.markdown("##### 💼 포트폴리오 관리")
        portfolio_df = get_portfolio_cached()
        in_portfolio = ticker in portfolio_df['symbol'].values
        p_shares = 0.0
        p_price = 0.0
        p_entry_reason = ""
        p_pos_type = "LONG"
        if in_portfolio:
            p_row = portfolio_df[portfolio_df['symbol'] == ticker].iloc[0]
            p_shares = float(p_row['shares'])
            p_price = float(p_row['purchase_price'])
            p_entry_reason = str(p_row['entry_reason']) if pd.notna(p_row['entry_reason']) else ""
            p_pos_type = str(p_row.get('position_type', 'LONG')).upper()
            st.caption(f"보유 중: {p_shares}주 (평단 ${p_price:.2f}, {p_pos_type})")
        else:
            st.caption("현재 미보유 상태입니다.")

        with st.popover("💼 보유 자산 정보 수정 / 청산", width="stretch"):
            tab_buy, tab_sell = st.tabs(["➕ 포지션 추가/수정", "🗑️ 포지션 청산 (매도)"])
            
            with tab_buy:
                with st.form("pf_edit_form", clear_on_submit=False):
                    pos_in = st.selectbox("포지션 구분", ["LONG", "SHORT"], index=0 if p_pos_type == "LONG" else 1, key="quick_pf_pos")
                    shares_in = st.number_input("보유 수량 (주)", min_value=0.0, value=p_shares, step=1.0, key="quick_pf_shares")
                    price_in = st.number_input("평균 매수 단가 ($)", min_value=0.0, value=p_price if p_price > 0 else current_price, step=0.01, key="quick_pf_price")
                    
                    st.markdown("**📊 전략 및 정성적 피드백 태그 (Notion 연동)**")
                    col_tag1, col_tag2 = st.columns(2)
                    with col_tag1:
                        market_regime = st.selectbox("시장 환경", ["강세장(상승)", "약세장(하락)", "박스권(횡보)", "변동성 장세"], key="quick_pf_market")
                    with col_tag2:
                        emotion_in = st.multiselect("진입 심리 상태", ["차분함", "조급함", "FOMO", "복수매매", "확증편향", "탐욕"], default=["차분함"], key="quick_pf_emotion")
                    
                    reason_in = st.text_area("상세 진입 근거 및 메모", value=p_entry_reason, height=80, key="quick_pf_reason")
                    pf_submit = st.form_submit_button("포지션 저장", width="stretch")
                    if pf_submit:
                        if shares_in > 0:
                            sh.save_portfolio(ticker, shares_in, price_in, reason_in, pos_in)
                            
                            # --- Notion 연동 ---
                            try:
                                page_id = nh.get_active_position(ticker)
                                if not page_id:
                                    page_id = nh.create_position_journal(ticker, price_in, reason_in)
                                
                                if page_id:
                                    if not in_portfolio:
                                        # 최초 진입
                                        nh.add_order_to_journal(page_id, pos_in, shares_in, price_in, reason_in)
                                    elif shares_in > p_shares:
                                        # 추가 매수
                                        added_shares = shares_in - p_shares
                                        nh.add_order_to_journal(page_id, pos_in, added_shares, price_in, reason_in)
                                    
                                    # 포지션 속성 업데이트 (시장 환경, 심리 상태 포함)
                                    nh.update_position_properties(
                                        page_id, avg_price=price_in, shares=shares_in, status="진입중",
                                        market_regime=market_regime, emotion=emotion_in
                                    )
                            except Exception as ne:
                                st.warning(f"노션 저널 연동 실패: {ne}")
                                
                            st.cache_data.clear()
                            st.success("포트폴리오 정보가 정상 등록/수정되었습니다.")
                            st.rerun()
                        else:
                            if in_portfolio:
                                # 노션 저널 청산 완료 처리 (수동 삭제 대응)
                                try:
                                    page_id = nh.get_active_position(ticker)
                                    if page_id:
                                        nh.close_position_journal(page_id, return_rate=0.0, return_val=0.0, feedback="사용자가 보유 자산 정보를 0으로 수정하여 포지션 강제 삭제함")
                                except Exception as ne:
                                    pass
                                sh.remove_from_portfolio(ticker)
                                st.cache_data.clear()
                                st.success("포트폴리오에서 삭제되었습니다.")
                                st.rerun()
                                
            with tab_sell:
                if in_portfolio:
                      with st.form("pf_liq_form", clear_on_submit=False):
                          sell_shares = st.number_input("청산할 수량 (주)", min_value=0.0, max_value=p_shares, value=p_shares, step=1.0, key="quick_pf_sell_shares")
                          sell_price = st.number_input("매도 청산 단가 ($)", min_value=0.0, value=current_price, step=0.01, key="quick_pf_sell_price")
                          
                          st.markdown("**🏁 원칙 평가 및 복기 태그 (Notion 연동)**")
                          adherence = st.selectbox("원칙 준수 여부", ["원칙 준수", "조기 익절", "뇌동 매매", "물타기 실수", "손절선 미준수"], key="quick_pf_adherence")
                          
                          exit_reason = st.text_area("청산 사유", value="", height=80, key="quick_pf_exit_reason")
                          liq_submit = st.form_submit_button("청산 실행 (매도 완료)", width="stretch")
                          if liq_submit:
                              if sell_shares > 0:
                                  sh.liquidate_portfolio(ticker, sell_shares, sell_price, exit_reason)
                                  
                                  # --- Notion 연동 ---
                                  try:
                                      page_id = nh.get_active_position(ticker)
                                      if page_id:
                                          # 매도 거래 내역을 노션 본문에 추가
                                          nh.add_order_to_journal(page_id, "SHORT", sell_shares, sell_price, exit_reason)
                                          
                                          if sell_shares >= p_shares:
                                              # 완청 처리
                                              ret_rate = 0.0
                                              if p_price > 0:
                                                  ret_rate = ((sell_price - p_price) / p_price) * 100
                                              ret_val = (sell_price - p_price) * sell_shares
                                              
                                              nh.close_position_journal(page_id, return_rate=ret_rate, return_val=ret_val, feedback=exit_reason, adherence=adherence)
                                          else:
                                              # 일부 청산
                                              nh.update_position_properties(page_id, avg_price=p_price, shares=(p_shares - sell_shares), status="진입중")
                                  except Exception as ne:
                                      st.warning(f"노션 저널 청산 연동 실패: {ne}")
                                      
                                  st.cache_data.clear()
                                  st.success(f"{ticker} 포지션 {sell_shares}주 청산 완료!")
                                  st.rerun()
                else:
                    st.info("현재 보유 중인 포지션이 없어 청산할 수 없습니다.")

    if "active_edit_row" not in st.session_state:
        st.session_state.active_edit_row = None

    st.markdown("##### ✍️ 투자 메모 및 코멘트")
    comments_history = get_comments_list_cached(ticker)
    if comments_history:
        with st.expander(f"💬 {ticker} 코멘트 히스토리 ({len(comments_history)}건)", expanded=True):
            for i, c in enumerate(comments_history):
                row_num = c['row_num']
                is_editing = (st.session_state.active_edit_row == row_num)

                # 작성일 및 수정일 정보를 포함하여 배치
                h_col1, h_col2 = st.columns([5, 2])
                with h_col1:
                    created_val = c.get('created_at', '')
                    updated_val = c.get('updated_at', '')
                    if created_val == updated_val or not updated_val:
                        st.markdown(f"🗓️ **{created_val}**")
                    else:
                        st.markdown(f"🗓️ **{created_val}** *(수정됨: {updated_val})*")
                with h_col2:
                    btn_col1, btn_col2 = st.columns(2)
                    if is_editing:
                        with btn_col1:
                            if st.button("💾 완료", key=f"done_btn_{row_num}", use_container_width=True):
                                new_val = st.session_state.get(f"edit_cmt_txt_{row_num}", "").strip()
                                if new_val:
                                    sh.update_comment_by_row(row_num, new_val)
                                    st.session_state.active_edit_row = None
                                    st.cache_data.clear()
                                    st.success("코멘트가 성공적으로 수정되었습니다.")
                                    st.rerun()
                                else:
                                    st.warning("코멘트 내용을 입력해주세요.")
                        with btn_col2:
                            if st.button("❌ 취소", key=f"cancel_btn_{row_num}", use_container_width=True):
                                st.session_state.active_edit_row = None
                                st.rerun()
                    else:
                        with btn_col1:
                            if st.button("✏️ 수정", key=f"edit_btn_{row_num}", use_container_width=True):
                                st.session_state.active_edit_row = row_num
                                st.rerun()
                        with btn_col2:
                            if st.button("🗑️ 삭제", key=f"del_btn_{row_num}", use_container_width=True):
                                sh.delete_comment_by_row(row_num)
                                st.cache_data.clear()
                                st.success("코멘트가 성공적으로 삭제되었습니다.")
                                st.rerun()
                
                # 코멘트 본문 영역 (편집 여부에 따라 분기)
                if is_editing:
                    st.text_area(
                        "코멘트 수정 입력창",
                        value=c['content'],
                        height=100,
                        key=f"edit_cmt_txt_{row_num}",
                        label_visibility="collapsed"
                    )
                else:
                    st.write(c['content'])
                
                if i < len(comments_history) - 1:
                    st.divider()
    else:
        st.caption("아직 기록된 코멘트가 없습니다. 아래에 새 코멘트를 추가해 보세요.")
    
    # 기본값을 비워두어 새로운 코멘트 작성을 용이하게 하고, 세션 키를 지정하여 등록 완료 시 초기화가 가능하도록 함
    comment_in = st.text_area(
        "이 종목에 대한 분석이나 매수 근거 등의 기록을 남겨보세요.", 
        value="", 
        height=120, 
        key="quick_comment_input"
    )

    col_btn1, col_btn2 = st.columns(2)
    with col_btn1:
        if st.button("➕ 구글 시트에 새 코멘트 추가 저장", width="stretch", type="primary"):
            if not comment_in.strip():
                st.warning("추가할 코멘트 내용을 입력해주세요.")
            else:
                sh.save_comment(ticker, comment_in)
                if "quick_comment_input" in st.session_state:
                    del st.session_state["quick_comment_input"]
                st.cache_data.clear()
                st.success("새 코멘트가 성공적으로 구글 시트에 추가 저장되었습니다.")
                st.rerun()
            
    with col_btn2:
        if st.button("📝 노션에 투자일지 기록", width="stretch"):
            import notion_helper as nh
            with st.spinner("노션 API 전송 중..."):
                success = nh.send_journal_to_notion(ticker, current_price, current_yield, comment_in)
            if success:
                st.success(f"🎉 {ticker} 투자일지가 노션에 성공적으로 기록되었습니다!")
            else:
                st.error("노션 기록에 실패했습니다. secrets.toml의 토큰과 DB ID 설정을 확인해 주세요.")

    # --- 2단계: 메인 차트 및 배당 변동 주기 상세 내역 (최하단 배치) ---
    st.divider()
    render_chart_section(ticker, df_price, df_stat, df_div_period, df_com, start_date, end_date)

# 2. 전체 종목 리스트 페이지
elif st.session_state.menu == "📋 전체 종목 리스트":
    with st.spinner("종목 리스트 불러오는 중..."):
        stocks_df = get_stocks_cached().copy()

    st.header("📋 전체 배당 종목 리스트")
    st.markdown("<style>input { text-transform: uppercase; }</style>", unsafe_allow_html=True)
    
    # 각 그룹별 동기화 시각 추출 (하단 동기화 패널용)
    group_times = {g: "-" for g in ["S&P", "Nasdaq", "SCHD", "VIG", "DGRO"]}
    
    if not stocks_df.empty:
        # 임시로 그룹 데이터 전처리를 수행하여 정확한 매칭을 보장
        if "group" in stocks_df.columns and "updated_at" in stocks_df.columns:
            temp_groups = stocks_df["group"].fillna("").astype(str).str.strip()
            for g in group_times.keys():
                g_df_times = stocks_df[temp_groups == g]["updated_at"]
                if not g_df_times.empty:
                    raw_time = g_df_times.max()
                    group_times[g] = str(raw_time) if pd.notna(raw_time) else "-"
    
    st.markdown("구글 시트와 연동된 주요 배당주 목록입니다.")
    
    if stocks_df.empty:
        st.warning("구글 시트에 저장된 종목 데이터가 없습니다. 아래 업데이트 버튼을 눌러 데이터를 수집해 주세요.")
    else:
        if "group" not in stocks_df.columns:
            stocks_df["group"] = ""
        if "weight" not in stocks_df.columns:
            stocks_df["weight"] = pd.NA
        if "marketCap" not in stocks_df.columns:
            stocks_df["marketCap"] = pd.NA
        if "dividendYield" not in stocks_df.columns:
            stocks_df["dividendYield"] = pd.NA

        stocks_df["group"] = stocks_df["group"].fillna("").astype(str).str.strip()
        stocks_df["weight"] = pd.to_numeric(stocks_df["weight"], errors="coerce")
        stocks_df["marketCap"] = pd.to_numeric(stocks_df["marketCap"], errors="coerce")
        stocks_df["dividendYield"] = pd.to_numeric(stocks_df["dividendYield"], errors="coerce")

        # 1. 상단 KPI 대시보드 카드 배치
        total_tracked = stocks_df["symbol"].nunique()
        sp500_count = stocks_df[stocks_df["group"] == "S&P"]["symbol"].nunique()
        nasdaq_count = stocks_df[stocks_df["group"] == "Nasdaq"]["symbol"].nunique()

        c1, c2, c3 = st.columns(3)
        c1.metric("총 수집 배당자산", f"{total_tracked}개")
        c2.metric("S&P 500 배당주", f"{sp500_count}개")
        c3.metric("Nasdaq 100 배당주", f"{nasdaq_count}개")
        st.markdown("<div style='padding-top: 15px;'></div>", unsafe_allow_html=True)

        # 2. 검색 및 필터 UI
        col_search, col_group, col_sort = st.columns([3, 2, 2])
        with col_search:
            search_query = st.text_input("🔍 종목 검색 (티커 또는 회사명)", "").strip().upper()
        with col_group:
            preferred = ["S&P", "Nasdaq", "SCHD", "VIG", "DGRO"]
            groups = [g for g in stocks_df["group"].dropna().unique().tolist() if str(g).strip()]
            group_ordered = [g for g in preferred if g in groups] + sorted([g for g in groups if g not in preferred])
            group_filter = st.selectbox("그룹 필터", ["전체"] + group_ordered)

        etf_groups = ["SCHD", "VIG", "DGRO"]

        # 정렬 기준 옵션 동적 구성 (ETF 선택 시 비중 순 추가)
        sort_options = ["시가총액 순", "배당률 순", "티커 순"]
        if group_filter in etf_groups:
            sort_options.insert(2, "비중 순")

        with col_sort:
            sort_by = st.selectbox("정렬 기준", sort_options)

        filtered_df = stocks_df.copy()

        # 필터 적용
        if group_filter != "전체":
            filtered_df = filtered_df[filtered_df["group"] == group_filter]

        if search_query:
            filtered_df = filtered_df[
                filtered_df["symbol"].astype(str).str.contains(search_query, case=False, na=False)
                | filtered_df["companyName"].astype(str).str.contains(search_query, case=False, na=False)
            ]

        # 정렬 규칙 적용 (1단계: 임시 정렬 처리 - 대표 그룹화 이후 2단계 최종 정렬 수행)

        # CSS Style Inject: 상태 전환(미선택/선택)과 무관하게 동일한 인스펙터 셸 스타일 유지
        st.markdown("""
<style>
/* 표식이 있는 가장 안쪽 상자 스타일 */
div[data-testid="stVerticalBlock"]:has(span.inspector-marker):not(:has(div[data-testid="stVerticalBlock"] span.inspector-marker)) {
    background-color: #f0f9ff !important;
    border: 1px solid #bae6fd !important;
    border-radius: 12px !important;
    padding: 20px 24px !important;
    box-shadow: 0 1px 3px 0 rgba(0, 0, 0, 0.05) !important;
    margin-bottom: 20px !important;
    gap: 0px !important;
    
    /* 상자 자체도 너비를 제한 */
    box-sizing: border-box !important;
    width: 100% !important;
    min-width: 0 !important;
    overflow: hidden !important;
}

/* 폼(Form) 요소의 기본 하단 마진을 제거하여 하단 패딩 확보 */
div[data-testid="stVerticalBlock"]:has(span.inspector-marker):not(:has(div[data-testid="stVerticalBlock"] span.inspector-marker)) form {
    margin-bottom: 0 !important;
}

/* 인스펙터 열(Column) 높이 균등화 및 하단 정렬 */

/* 1단계: 열 자체를 flex column 컨테이너로 전환 */
div[data-testid="stVerticalBlock"]:has(span.inspector-marker):not(:has(div[data-testid="stVerticalBlock"] span.inspector-marker)) [data-testid="column"] {
    display: flex !important;
    flex-direction: column !important;
}

/* 2단계: 열의 직계 자식(래퍼 div)을 열 전체 높이로 늘림 — 핵심 수정 */
div[data-testid="stVerticalBlock"]:has(span.inspector-marker):not(:has(div[data-testid="stVerticalBlock"] span.inspector-marker)) [data-testid="column"] > * {
    flex: 1 !important;
    display: flex !important;
    flex-direction: column !important;
}

/* 3단계: 래퍼 안의 stVerticalBlock도 동일하게 늘림 */
div[data-testid="stVerticalBlock"]:has(span.inspector-marker):not(:has(div[data-testid="stVerticalBlock"] span.inspector-marker)) [data-testid="column"] div[data-testid="stVerticalBlock"] {
    flex: 1 !important;
    display: flex !important;
    flex-direction: column !important;
}

/* 4단계: 각 열의 마지막 자식을 바닥으로 밀어서 정렬 (특이성 강화) */
div[data-testid="stVerticalBlock"]:has(span.inspector-marker):not(:has(div[data-testid="stVerticalBlock"] span.inspector-marker)) [data-testid="column"] div[data-testid="stVerticalBlock"] > *:last-child {
    margin-top: auto !important;
}

/* 숨겨진 inspector-marker를 감싸는 래퍼 element-container의 공간을 완전히 제거 */
div[data-testid="stVerticalBlock"]:has(span.inspector-marker):not(:has(div[data-testid="stVerticalBlock"] span.inspector-marker)) > div:has(span.inspector-marker) {
    height: 0 !important;
    min-height: 0 !important;
    margin: 0 !important;
    padding: 0 !important;
    overflow: hidden !important;
}

/* 컨테이너 내부의 모든 하위 래퍼 요소가 부모 패딩 영역을 초과하지 않도록 강제 */
div[data-testid="stVerticalBlock"]:has(span.inspector-marker):not(:has(div[data-testid="stVerticalBlock"] span.inspector-marker)) > div,
div[data-testid="stVerticalBlock"]:has(span.inspector-marker):not(:has(div[data-testid="stVerticalBlock"] span.inspector-marker)) div[data-testid="element-container"],
div[data-testid="stVerticalBlock"]:has(span.inspector-marker):not(:has(div[data-testid="stVerticalBlock"] span.inspector-marker)) div[data-testid="stMarkdownContainer"],
div[data-testid="stVerticalBlock"]:has(span.inspector-marker):not(:has(div[data-testid="stVerticalBlock"] span.inspector-marker)) div[data-testid="stMarkdown"],
div[data-testid="stVerticalBlock"]:has(span.inspector-marker):not(:has(div[data-testid="stVerticalBlock"] span.inspector-marker)) .stMarkdown {
    max-width: 100% !important;
    min-width: 0 !important;
    margin: 0 !important;
    box-sizing: border-box !important;
}

/* 텍스트 폰트 색상 및 크기 지정 */
.inspector-guide-text {
    color: #0c4a6e !important;
    font-size: 0.95rem !important;
    font-weight: 500 !important;
    margin: 0 !important;
    max-width: 100% !important;
    box-sizing: border-box !important;
}
</style>
        """, unsafe_allow_html=True)

        # 3. 인스펙터 패널용 빈 컨테이너 생성 (테이블 위에 렌더링되도록 플레이스홀더 지정)
        inspector_placeholder = st.empty()
        st.markdown("<div style='padding-top: 5px;'></div>", unsafe_allow_html=True)

        # 4. 테이블 영역 렌더링 (가로 100% 사용, 자체 스크롤바 높이 고정)
        st.markdown(f"**🔍 조건에 맞는 {len(filtered_df)}개의 배당 자산이 조회되었습니다.**")
        
        # 우선순위 정의 (낮을수록 우선순위 높음)
        PRIORITY = {"SCHD": 1, "VIG": 2, "DGRO": 3, "S&P": 4, "Nasdaq": 5}

        if group_filter == "전체":
            # 중복 데이터 제거 및 그룹 병합 (시가총액, 배당률도 max로 보존)
            grouped = filtered_df.groupby("symbol").agg({
                "companyName": "first",
                "lastDividend": "max",
                "marketCap": "max",
                "dividendYield": "max",
                "weight": "max",
                "group": lambda x: list(set(str(val).strip() for val in x if str(val).strip()))
            }).reset_index()

            def format_group_display(g_list):
                if not g_list:
                    return "-"
                sorted_g = sorted(g_list, key=lambda x: PRIORITY.get(x, 99))
                rep = sorted_g[0]
                if len(sorted_g) > 1:
                    return f"{rep} 외 {len(sorted_g) - 1}"
                return rep

            grouped["group_full"] = grouped["group"].apply(lambda x: ", ".join(sorted(x, key=lambda val: PRIORITY.get(val, 99))))
            grouped["group"] = grouped["group"].apply(format_group_display)
            display_df = grouped
        else:
            display_df = filtered_df.copy()
            display_df["group_full"] = display_df["group"]

        # 정렬 기준(sort_by)에 따라 정렬 실행
        if sort_by == "시가총액 순":
            display_df = display_df.sort_values(by="marketCap", ascending=False, na_position="last")
        elif sort_by == "배당률 순":
            display_df = display_df.sort_values(by="dividendYield", ascending=False, na_position="last")
        elif sort_by == "비중 순":
            display_df = display_df.sort_values(by="weight", ascending=False, na_position="last")
        elif sort_by == "티커 순":
            display_df = display_df.sort_values(by="symbol", ascending=True)

        # 시가총액 단위 포맷팅 함수 정의 및 변환 적용
        def format_market_cap(val):
            if pd.isna(val) or val <= 0:
                return "-"
            if val >= 1e12:
                return f"${val / 1e12:.2f}T"
            if val >= 1e9:
                return f"${val / 1e9:.2f}B"
            if val >= 1e6:
                return f"${val / 1e6:.2f}M"
            return f"${val:,.0f}"

        # 배당률 퍼센트 포맷팅 함수 정의 및 변환 적용
        def format_dividend_yield(val):
            if pd.isna(val) or val <= 0:
                return "-"
            return f"{val:.2f}%"

        display_df["formatted_cap"] = display_df["marketCap"].apply(format_market_cap)
        display_df["formatted_yield"] = display_df["dividendYield"].apply(format_dividend_yield)

        display_df.insert(0, "선택", False)
        
        display_df = display_df.rename(columns={
            "symbol": "티커",
            "companyName": "회사명",
            "group": "그룹",
            "formatted_cap": "시가총액",
            "formatted_yield": "배당률",
            "weight": "비중(%)",
        })


        columns_to_show = ["선택", "티커", "회사명", "그룹", "시가총액", "배당률"]
        if group_filter in etf_groups:
            columns_to_show.insert(6, "비중(%)")

        # st.data_editor로 렌더링 (자체 스크롤바 부여)
        edited_df = st.data_editor(
            display_df[columns_to_show],
            width="stretch",
            hide_index=True,
            height=320,  # 스크롤 방지를 위해 높이 제한 고정
            column_config={
                "선택": st.column_config.CheckboxColumn("", width=40, default=False),
                "티커": st.column_config.TextColumn("티커", width=60),
                "회사명": st.column_config.TextColumn("회사명", width="medium"), # medium 고정으로 가로 스크롤 방지
                "그룹": st.column_config.TextColumn("그룹", width=100),
                "시가총액": st.column_config.TextColumn("시가총액", width=90),
                "배당률": st.column_config.TextColumn("배당률", width=80),
                "비중(%)": st.column_config.NumberColumn("비중(%)", format="%.4f%%", width=70),
            },
            disabled=["티커", "회사명", "그룹", "시가총액", "배당률", "비중(%)"],
            key="stocks_list_editor"
        )

        # 5. 테이블의 선택 이벤트 감지 후 플레이스홀더에 동적으로 인스펙터 렌더링
        selected_rows = edited_df[edited_df["선택"] == True]
        
        with inspector_placeholder.container():
            # 단일 셸 컨테이너를 유지하고 내부 콘텐츠만 상태별로 전환
            st.markdown('<span class="inspector-marker" style="display:none;">m</span>', unsafe_allow_html=True)
            if not selected_rows.empty:
                st.markdown("<h4 style='font-size: 1.05rem; font-weight: 700; margin: 0 0 10px 0; color: #1e3a8a;'>🔍 선택 종목 상세 제어 패널 (Inspector)</h4>", unsafe_allow_html=True)
                
                selected_stock = selected_rows.iloc[-1]
                sel_ticker = selected_stock["티커"]
                sel_name = selected_stock["회사명"]
                
                # group_full 정보 추출 및 개별 그룹 파싱
                # 만약 group_full이 없으면 기존 그룹을 가져옴
                sel_group_full = selected_stock["group_full"] if "group_full" in selected_stock else selected_stock["그룹"]
                sel_groups = [g.strip() for g in str(sel_group_full).split(",") if g.strip()]

                # 그룹별 색상 뱃지 스타일 정의
                BADGE_STYLES = {
                    "SCHD": "background-color: #e0f2fe; color: #0369a1; border: 1px solid #bae6fd;",
                    "VIG": "background-color: #f3e8ff; color: #6b21a8; border: 1px solid #e9d5ff;",
                    "DGRO": "background-color: #ecfdf5; color: #065f46; border: 1px solid #a7f3d0;",
                    "S&P": "background-color: #ffedd5; color: #9a3412; border: 1px solid #fed7aa;",
                    "Nasdaq": "background-color: #f1f5f9; color: #334155; border: 1px solid #e2e8f0;"
                }

                badge_htmls = []
                for g in sel_groups:
                    style = BADGE_STYLES.get(g, "background-color: #f1f5f9; color: #334155; border: 1px solid #e2e8f0;")
                    badge_htmls.append(f'<span style="{style} padding: 2px 10px; border-radius: 20px; font-size: 0.68rem; font-weight: 700; margin-right: 4px; margin-bottom: 4px; display: inline-block;">{g}</span>')
                badges_combined = "".join(badge_htmls)

                sel_market_cap = selected_stock["시가총액"]
                sel_yield = selected_stock["배당률"]

                c_ins1, c_ins2, c_ins3 = st.columns([1, 1, 1])
                
                with c_ins1:
                    st.caption("🏷️ 종목 요약 프로필")
                    st.markdown(f"""
                    <div style="text-align: left; padding: 0; margin: 0;">
                        <h3 style="margin: 0; padding: 0; color: #0f172a; font-weight: 800; font-size: 1.5rem; letter-spacing: -0.01em; line-height: 1.1;">{sel_ticker}</h3>
                        <p style="margin: 5px 0 6px 0; font-size: 0.8rem; font-weight: 500; color: #475569; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; height: 32px; line-height: 1.25;">{sel_name}</p>
                        <div style="font-size: 0.72rem; font-weight: 600; color: #64748b; margin-bottom: 2px;">🏛️ 시가총액: <span style="color: #0f172a; font-weight: 700;">{sel_market_cap}</span></div>
                        <div style="font-size: 0.72rem; font-weight: 600; color: #64748b; margin-bottom: 8px;">💰 배당수익률: <span style="color: #16a34a; font-weight: 700;">{sel_yield}</span></div>
                        <div style="display: flex; flex-wrap: wrap; gap: 4px; align-items: center; margin: 0; padding: 0;">
                            {badges_combined}
                        </div>
                    </div>
                    """, unsafe_allow_html=True)
                    
                with c_ins2:
                    st.caption("📝 신속 제어 및 분석")
                    st.markdown("<div style='padding-top: 5px;'></div>", unsafe_allow_html=True)
                    
                    # 1. 상세 차트 분석 이동
                    if st.button("📊 상세 차트 분석 이동", width="stretch", type="primary"):
                        st.session_state.ticker = sel_ticker
                        st.session_state.menu = "📊 개별 종목 분석"
                        st.cache_data.clear()
                        st.rerun()
                    
                    # 2. 관심종목 토글
                    watchlist = get_watchlist_cached()
                    is_in_wl = sel_ticker in watchlist
                    if is_in_wl:
                        if st.button("⭐ 관심 종목 해제", width="stretch"):
                            sh.remove_from_watchlist(sel_ticker)
                            st.cache_data.clear()
                            st.toast(f"⭐ {sel_ticker} 관심 종목 해제 완료!")
                            st.rerun()
                    else:
                        if st.button("⭐ 관심 종목 등록", width="stretch"):
                            sh.add_to_watchlist(sel_ticker)
                            st.cache_data.clear()
                            st.toast(f"⭐ {sel_ticker} 관심 종목 등록 완료!")
                            st.rerun()
                            
                with c_ins3:
                    # 3열: 포트폴리오 관리 (caption 헤더로 통일하여 베이스라인을 일치시킴)
                    portfolio_df = get_portfolio_cached()
                    in_portfolio = sel_ticker in portfolio_df['symbol'].values
                    p_shares = 0.0
                    p_price = 0.0
                    
                    if in_portfolio:
                        row = portfolio_df[portfolio_df['symbol'] == sel_ticker].iloc[0]
                        p_shares = float(row['shares'])
                        p_price = float(row['purchase_price'])
                        status_tag = f"<span style='font-size:0.72rem;color:#059669;font-weight:700;'>(보유: {p_shares}주)</span>"
                    else:
                        status_tag = "<span style='font-size:0.72rem;color:#64748b;font-weight:700;'>(미보유)</span>"
                    
                    st.markdown(f"<div style='font-size: 0.8rem; color: #475569; margin-bottom: 2px;'>💼 포트폴리오 자산 {status_tag}</div>", unsafe_allow_html=True)
                    
                    with st.form("quick_pf_form", border=False):
                        col_form1, col_form2 = st.columns(2)
                        with col_form1:
                            shares_in = st.number_input("수량", min_value=0.0, value=p_shares, step=0.1)
                        with col_form2:
                            price_in = st.number_input("평단 ($)", min_value=0.0, value=p_price, step=0.01)
                        pf_submit = st.form_submit_button("💼 포트폴리오 저장/수정", width="stretch")
                        if pf_submit:
                            if shares_in > 0:
                                sh.save_portfolio(sel_ticker, shares_in, price_in)
                                
                                # --- Notion 연동 ---
                                try:
                                    page_id = nh.get_active_position(sel_ticker)
                                    if not page_id:
                                        page_id = nh.create_position_journal(sel_ticker, price_in, "대시보드 간편 등록")
                                    
                                    if page_id:
                                        if not in_portfolio:
                                            nh.add_order_to_journal(page_id, "LONG", shares_in, price_in, "대시보드 간편 등록")
                                        elif shares_in > p_shares:
                                            added_shares = shares_in - p_shares
                                            nh.add_order_to_journal(page_id, "LONG", added_shares, price_in, "대시보드 추가매수")
                                        
                                        nh.update_position_properties(page_id, avg_price=price_in, shares=shares_in, status="진입중")
                                except Exception as ne:
                                    pass
                                      
                                st.cache_data.clear()
                                st.toast(f"💼 {sel_ticker} {shares_in}주 저장 완료!")
                                st.rerun()
                            else:
                                if in_portfolio:
                                    # --- Notion 연동 ---
                                    try:
                                        page_id = nh.get_active_position(sel_ticker)
                                        if page_id:
                                            nh.close_position_journal(page_id, return_rate=0.0, return_val=0.0, feedback="대시보드 간편 포트폴리오 삭제")
                                    except Exception as ne:
                                        pass
                                    sh.remove_from_portfolio(sel_ticker)
                                    st.cache_data.clear()
                                    st.toast(f"💼 {sel_ticker} 포트폴리오에서 삭제됨")
                                    st.rerun()
            else:
                st.markdown('''
                <div class="inspector-guide-text" style="width: 100% !important; max-width: 100% !important; white-space: normal !important; word-break: break-all !important; overflow-wrap: break-word !important; box-sizing: border-box !important;">
                    💡 아래 표에서 종목의 '선택' 체크박스를 누르시면 이곳에 상세 분석 및 제어 패널(차트이동, 관심종목 토글, 자산 입력)이 즉시 펼쳐집니다.
                </div>
                ''', unsafe_allow_html=True)

    st.divider()
    st.subheader("🔄 데이터 실시간 강제 동기화")
    st.markdown("Wikipedia와 무료 소스를 참조하여 S&P 500, 나스닥 100, SCHD/VIG/DGRO 구성종목 데이터를 동기화합니다.")
    
    # 5. 동기화 옵션 체크박스 그리드 및 Form 구성 (API 429 방지 및 시간정보 제공)
    with st.form("sync_form", clear_on_submit=False):
        st.markdown("##### ⚙️ 동기화 대상 그룹 선택")
        c_sync1, c_sync2, c_sync3 = st.columns(3)
        with c_sync1:
            sync_sp = st.checkbox("S&P 500 지수", value=True, help="약 500개 종목, 약 2분 소요")
            st.caption(f"🕒 최근 동기화: {group_times.get('S&P', '-')}")
            st.markdown("<div style='padding-top: 10px;'></div>", unsafe_allow_html=True)
            sync_nas = st.checkbox("Nasdaq 100 지수", value=True, help="약 100개 종목, 약 30초 소요")
            st.caption(f"🕒 최근 동기화: {group_times.get('Nasdaq', '-')}")
        with c_sync2:
            sync_schd = st.checkbox("SCHD ETF", value=True, help="약 100개 종목, 약 30초 소요")
            st.caption(f"🕒 최근 동기화: {group_times.get('SCHD', '-')}")
            st.markdown("<div style='padding-top: 10px;'></div>", unsafe_allow_html=True)
            sync_vig = st.checkbox("VIG ETF", value=True, help="약 300개 종목, 약 1분 소요")
            st.caption(f"🕒 최근 동기화: {group_times.get('VIG', '-')}")
        with c_sync3:
            sync_dgro = st.checkbox("DGRO ETF", value=True, help="약 400개 종목, 약 1분 소요")
            st.caption(f"🕒 최근 동기화: {group_times.get('DGRO', '-')}")
            
        submit_sync = st.form_submit_button("🔄 선택된 그룹 배당 정보 수동 업데이트", width="stretch")

    if submit_sync:
        selected_sync_groups = []
        if sync_sp: selected_sync_groups.append("S&P")
        if sync_nas: selected_sync_groups.append("Nasdaq")
        if sync_schd: selected_sync_groups.append("SCHD")
        if sync_vig: selected_sync_groups.append("VIG")
        if sync_dgro: selected_sync_groups.append("DGRO")

        if not selected_sync_groups:
            st.warning("동기화할 그룹을 하나 이상 선택해 주세요.")
        else:
            with st.spinner("웹 스크레이퍼 및 yfinance를 실행하여 구글 시트 데이터를 동기화하는 중..."):
                try:
                    import fetch_dividend_stocks as fds
                    fds.run_update(target_groups=selected_sync_groups)

                    success_groups = getattr(fds, "LAST_SUCCESS_GROUPS", [])
                    failed_groups = getattr(fds, "LAST_FAILED_GROUPS", [])

                    if success_groups:
                        st.info(f"성공 그룹: {', '.join(success_groups)}")
                    if failed_groups:
                        st.warning(f"실패 그룹: {', '.join(failed_groups)}")

                    if success_groups:
                        st.cache_data.clear()
                        st.success("구글 시트 동기화가 성공적으로 완료되었습니다!")
                        st.rerun()
                    else:
                        st.error("모든 선택 그룹의 동기화에 실패했습니다.")
                except Exception as e:
                    st.error(f"동기화 중 오류 발생: {e}")

# 3. 내 투자 관리 페이지
elif st.session_state.menu == "💼 내 투자 관리":
    st.header("💼 내 투자 관리 (My Investment Hub)")
    
    tab_pf, tab_wl, tab_al, tab_th = st.tabs([
        "💼 내 포트폴리오", 
        "⭐ 관심 종목 & 그룹", 
        "🎯 조건부 타겟", 
        "📝 매매 기록"
    ])
    
    # 캐시된 종목 마스터 리스트 로딩
    stocks_df = get_stocks_cached()
    
    # ------------------ Tab 1: 내 포트폴리오 ------------------
    with tab_pf:
        st.subheader("보유 자산 현황")
        portfolio_df = get_portfolio_cached()
        
        if portfolio_df.empty:
            st.info("포트폴리오가 현재 비어 있습니다. 사이드바 검색창이나 관심 종목 탭에서 자산을 추가해 주세요.")
        else:
            pf_tickers = portfolio_df['symbol'].tolist()
            close_prices = {}
            
            with st.spinner("보유 종목의 최신 주가 정보를 조회 중..."):
                try:
                    price_data = yf.download(pf_tickers, period="5d", interval="1d")
                    if len(pf_tickers) == 1:
                        close_prices[pf_tickers[0]] = float(price_data['Close'].squeeze().iloc[-1])
                    else:
                        for t in pf_tickers:
                            try:
                                close_prices[t] = float(price_data['Close'][t].iloc[-1])
                            except Exception:
                                close_prices[t] = 0.0
                except Exception as e:
                    st.error(f"실시간 주가 로딩 실패 (이전 평단가로 대체): {e}")
                    close_prices = {t: 0.0 for t in pf_tickers}
            
            total_invested = 0.0
            total_current_val = 0.0
            total_annual_div = 0.0
            rows = []
            
            for _, row in portfolio_df.iterrows():
                sym = row['symbol']
                shares = float(row['shares'])
                avg_cost = float(row['purchase_price'])
                entry_reason = row['entry_reason'] if pd.notna(row['entry_reason']) else ""
                pos_type = str(row.get('position_type', 'LONG')).upper()
                
                curr_price = close_prices.get(sym, 0.0)
                if curr_price == 0.0:
                    curr_price = avg_cost
                    
                stock_info = stocks_df[stocks_df['symbol'] == sym]
                name = stock_info.iloc[0]['companyName'] if not stock_info.empty else sym
                
                # FMP/yfinance의 single payout 배당금에 4(분기 배당 가정)를 곱해 연간 배당금으로 환산
                last_div = float(stock_info.iloc[0]['lastDividend']) if not stock_info.empty else 0.0
                annual_div_per_share = last_div * 4
                
                cost = shares * avg_cost
                
                # 포지션(LONG/SHORT)에 따른 평가금액 및 손익 계산
                if pos_type == "SHORT":
                    gain_loss = shares * (avg_cost - curr_price)
                    val = cost + gain_loss
                    annual_div = -shares * annual_div_per_share # 숏 포지션은 배당을 지불해야 함
                else:
                    gain_loss = shares * (curr_price - avg_cost)
                    val = shares * curr_price
                    annual_div = shares * annual_div_per_share
                
                total_invested += cost
                total_current_val += val
                total_annual_div += annual_div
                
                gain_loss_pct = (gain_loss / cost * 100) if cost > 0 else 0.0
                
                rows.append({
                    '티커': sym,
                    '포지션': pos_type,
                    '종목명': name,
                    '보유 수량': shares,
                    '평균 매수가 ($)': avg_cost,
                    '현재 주가 ($)': curr_price,
                    '투자 원금 ($)': cost,
                    '현재 평가액 ($)': val,
                    '수익률 (%)': gain_loss_pct,
                    '예상 연간 배당금 ($)': annual_div,
                    '배당수익률(평단 기준)': (annual_div_per_share / avg_cost * 100) if avg_cost > 0 else 0.0
                })
                
            # 포트폴리오 요약 메트릭 표시
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("총 투자원금", f"${total_invested:,.2f}")
            
            total_gain = total_current_val - total_invested
            total_gain_pct = (total_gain / total_invested * 100) if total_invested > 0 else 0.0
            m2.metric("총 평가금액", f"${total_current_val:,.2f}", f"{total_gain_pct:+.2f}%")
            
            m3.metric("예상 세전 연배당금", f"${total_annual_div:,.2f}")
            
            avg_yield = (total_annual_div / total_current_val * 100) if total_current_val > 0 else 0.0
            m4.metric("평균 배당수익률 (현재가)", f"{avg_yield:.2f}%")
            
            st.divider()
            
            # 보유 종목 상세 내역 테이블 (단일 행 선택 활성화)
            pf_display_df = pd.DataFrame(rows)
            event_pf = st.dataframe(
                pf_display_df,
                width="stretch",
                hide_index=True,
                column_config={
                    "평균 매수가 ($)": st.column_config.NumberColumn("평균 매수가", format="$%.2f"),
                    "현재 주가 ($)": st.column_config.NumberColumn("현재 주가", format="$%.2f"),
                    "투자 원금 ($)": st.column_config.NumberColumn("투자 원금", format="$%.2f"),
                    "현재 평가액 ($)": st.column_config.NumberColumn("현재 평가액", format="$%.2f"),
                    "수익률 (%)": st.column_config.NumberColumn("수익률 (%)", format="%+.2f%%"),
                    "예상 연간 배당금 ($)": st.column_config.NumberColumn("예상 연간 배당금", format="$%.2f"),
                    "배당수익률(평단 기준)": st.column_config.NumberColumn("배당수익률(평단)", format="%.2f%%")
                },
                selection_mode="single-row",
                on_select="rerun",
                key="pf_dataframe"
            )
            
            # 선택된 자산에 대한 제어 패널
            selected_rows = event_pf.selection.rows
            if selected_rows:
                selected_idx = selected_rows[0]
                if selected_idx < len(pf_display_df):
                    sel_ticker = pf_display_df.iloc[selected_idx]['티커']
                    sel_row = portfolio_df[portfolio_df['symbol'] == sel_ticker].iloc[0]
                    sel_shares = float(sel_row['shares'])
                    sel_price = float(sel_row['purchase_price'])
                    sel_reason = str(sel_row['entry_reason']) if pd.notna(sel_row['entry_reason']) else ""
                    sel_pos = str(sel_row.get('position_type', 'LONG')).upper()
                    
                    st.subheader(f"⚙️ 선택된 포지션 제어: {sel_ticker} ({sel_pos})")
                    if sel_reason:
                        st.info(f"💬 **진입 근거 (메모)**: {sel_reason}")
                
                c_act1, c_act2, c_act3 = st.columns(3)
                with c_act1:
                    if st.button("📊 상세 분석 차트로 이동", use_container_width=True, key="pf_goto_chart_btn", type="primary"):
                        st.session_state.ticker = sel_ticker
                        st.session_state.menu = "📊 개별 종목 분석"
                        st.rerun()
                with c_act2:
                    with st.popover("➕ 포지션 추가 매수 / 수정", use_container_width=True):
                        with st.form("pf_tab_buy_form", clear_on_submit=True):
                            pos_in = st.selectbox("포지션 구분", ["LONG", "SHORT"], index=0 if sel_pos == "LONG" else 1, key="pf_tab_pos_sel")
                            shares_in = st.number_input("조정 후 총 수량 (주)", min_value=0.0, value=sel_shares, step=1.0)
                            price_in = st.number_input("수정된 평단가 ($)", min_value=0.0, value=sel_price, step=0.01)
                            reason_in = st.text_area("메모 / 진입 근거", value=sel_reason, height=80)
                            buy_submit = st.form_submit_button("포지션 업데이트", width="stretch")
                            if buy_submit:
                                if shares_in > 0:
                                    sh.save_portfolio(sel_ticker, shares_in, price_in, reason_in, pos_in)
                                    
                                    # --- Notion 연동 ---
                                    try:
                                        page_id = nh.get_active_position(sel_ticker)
                                        if not page_id:
                                            page_id = nh.create_position_journal(sel_ticker, price_in, reason_in)
                                        
                                        if page_id:
                                            # 수량이 늘어났을 때만 주문 기입
                                            if shares_in > sel_shares:
                                                added_shares = shares_in - sel_shares
                                                nh.add_order_to_journal(page_id, pos_in, added_shares, price_in, reason_in)
                                            
                                            nh.update_position_properties(page_id, avg_price=price_in, shares=shares_in, status="진입중")
                                    except Exception as ne:
                                        pass
                                        
                                    st.cache_data.clear()
                                    st.success("수정이 완료되었습니다!")
                                    st.rerun()
                with c_act3:
                    with st.popover("🗑️ 포지션 청산 (매도)", use_container_width=True):
                        with st.form("pf_tab_sell_form", clear_on_submit=True):
                            sell_shares = st.number_input("청산 수량 (주)", min_value=0.0, max_value=sel_shares, value=sel_shares, step=1.0)
                            sell_price = st.number_input("매도 청산 단가 ($)", min_value=0.0, value=close_prices.get(sel_ticker, sel_price), step=0.01)
                            exit_reason = st.text_area("청산 사유 / 기록", value="", height=80)
                            sell_submit = st.form_submit_button("청산 실행", width="stretch")
                            if sell_submit:
                                if sell_shares > 0:
                                    sh.liquidate_portfolio(sel_ticker, sell_shares, sell_price, exit_reason)
                                    
                                    # --- Notion 연동 ---
                                    try:
                                        page_id = nh.get_active_position(sel_ticker)
                                        if page_id:
                                            nh.add_order_to_journal(page_id, "SHORT", sell_shares, sell_price, exit_reason)
                                            
                                            if sell_shares >= sel_shares:
                                                # 완청 처리
                                                ret_rate = 0.0
                                                if sel_price > 0:
                                                    ret_rate = ((sell_price - sel_price) / sel_price) * 100
                                                ret_val = (sell_price - sel_price) * sell_shares
                                                nh.close_position_journal(page_id, return_rate=ret_rate, return_val=ret_val, feedback=exit_reason)
                                            else:
                                                # 일부 청산
                                                nh.update_position_properties(page_id, avg_price=sel_price, shares=(sel_shares - sell_shares), status="진입중")
                                    except Exception as ne:
                                        pass
                                        
                                    st.cache_data.clear()
                                    st.success("포지션 청산이 실행되었습니다.")
                                    st.rerun()
            else:
                st.info("💡 위의 포트폴리오 표에서 자산 행을 클릭하시면 즉시 상세 차트 분석 이동 및 추가 매수/청산 처리를 할 수 있는 제어 패널이 나타납니다.")

    # ------------------ Tab 2: 관심 종목 & 그룹 ------------------
    with tab_wl:
        st.subheader("⭐ 내 관심 종목 목록")
        watchlist_details = get_watchlist_details_cached()
        
        if watchlist_details.empty:
            st.info("관심 등록된 종목이 없습니다. 사이드바 검색창이나 우측 그룹 관리를 통해 추가해 주세요.")
        else:
            # stocks_df의 중복 티커 제거하여 조인 시 행이 폭발적으로 늘어나는 버그 해결
            stocks_unique_df = stocks_df.drop_duplicates(subset=['symbol'])
            
            # 관심 목록 상세 테이블 생성 (마스터 정보 조인)
            wl_display_df = watchlist_details.merge(stocks_unique_df, on='symbol', how='left')
            wl_display_df['companyName'] = wl_display_df['companyName'].fillna("").astype(str)
            wl_display_df['lastDividend'] = pd.to_numeric(wl_display_df['lastDividend'], errors='coerce').fillna(0.0)
            wl_display_df['stock_type'] = wl_display_df['stock_type'].fillna("STOCK").astype(str).str.upper()
            
            # 관심 그룹 필터
            all_wl_groups = sorted(wl_display_df['group_name'].dropna().unique().tolist())
            wl_group_filter = st.selectbox("관심 그룹별 필터", ["전체"] + all_wl_groups, key="wl_group_filter_box")
            
            if wl_group_filter != "전체":
                wl_filtered_df = wl_display_df[wl_display_df['group_name'] == wl_group_filter]
            else:
                wl_filtered_df = wl_display_df
                
            wl_rows = []
            for _, row in wl_filtered_df.iterrows():
                sym = row['symbol']
                grp = row['group_name']
                name = row['companyName'] if row['companyName'] else sym
                last_div = float(row['lastDividend'])
                annual_div_per_share = last_div * 4
                asset_type = row['stock_type']
                
                wl_rows.append({
                    '티커': sym,
                    '관심 그룹': grp,
                    '회사명': name,
                    '최근 주당 배당금 ($)': last_div,
                    '예상 연배당금 ($)': annual_div_per_share,
                    '자산 분류': asset_type
                })
            
            wl_table_df = pd.DataFrame(wl_rows)
            
            # 관심 목록 데이터프레임 렌더링 (단일 행 선택 모드)
            event_wl = st.dataframe(
                wl_table_df,
                width="stretch",
                hide_index=True,
                column_config={
                    "최근 주당 배당금 ($)": st.column_config.NumberColumn("주당 배당금 (분기)", format="$%.4f"),
                    "예상 연배당금 ($)": st.column_config.NumberColumn("연 환산 배당금", format="$%.4f")
                },
                selection_mode="single-row",
                on_select="rerun",
                key="wl_dataframe_table"
            )
            
            selected_wl_rows = event_wl.selection.rows
            if selected_wl_rows:
                selected_idx = selected_wl_rows[0]
                if selected_idx < len(wl_table_df):
                    sel_ticker = wl_table_df.iloc[selected_idx]['티커']
                    sel_group = wl_table_df.iloc[selected_idx]['관심 그룹']
                    
                    st.subheader(f"⚙️ 선택된 관심종목 제어: {sel_ticker}")
                    wl_comment = get_comment_cached(sel_ticker)
                    if wl_comment:
                        st.info(f"💬 **관심 종목 코멘트**: {wl_comment}")
                
                c_wl_act1, c_wl_act2, c_wl_act3, c_wl_act4 = st.columns(4)
                
                with c_wl_act1:
                    if st.button("📊 상세 차트 분석 이동", use_container_width=True, key="wl_tab_goto_chart", type="primary"):
                        st.session_state.ticker = sel_ticker
                        st.session_state.menu = "📊 개별 종목 분석"
                        st.rerun()
                        
                with c_wl_act2:
                    with st.popover("🎯 조건부 타겟 설정", use_container_width=True):
                        with st.form("wl_tab_alert_form", clear_on_submit=True):
                            st.write(f"🎯 {sel_ticker} 타겟 가격 알림 설정")
                            cond_in = st.text_input("조건식 입력 (예: >= 150)", value=">= 100.0")
                            al_submit = st.form_submit_button("알림 추가")
                            if al_submit:
                                import re
                                cond_in = cond_in.strip()
                                match = re.match(r"^([><]=?|==)\s*([0-9.]+)", cond_in)
                                if match:
                                    operator = match.group(1)
                                    target_val = float(match.group(2))
                                    sh.save_alert(sel_ticker, target_val, operator)
                                    st.cache_data.clear()
                                    st.success(f"{sel_ticker} 타겟 알림 설정 완료!")
                                    st.rerun()
                                else:
                                    st.error("형식이 올바르지 않습니다. (예: >= 150)")
                                    
                with c_wl_act3:
                    with st.popover("💼 포트폴리오 등록 (매수)", use_container_width=True):
                        with st.form("wl_tab_pf_form", clear_on_submit=True):
                            st.write(f"💼 {sel_ticker} 포트폴리오 등록")
                            pos_in = st.selectbox("포지션", ["LONG", "SHORT"], key="wl_tab_pos_sel")
                            shares_in = st.number_input("매수 수량 (주)", min_value=0.0, value=10.0, step=1.0)
                            price_in = st.number_input("평균 매수가 ($)", min_value=0.0, value=100.0, step=0.01)
                            reason_in = st.text_area("매수 사유", value="", height=80)
                            pf_add_submit = st.form_submit_button("포트폴리오에 자산 추가")
                            if pf_add_submit:
                                if shares_in > 0:
                                    sh.save_portfolio(sel_ticker, shares_in, price_in, reason_in, pos_in)
                                    
                                    # --- Notion 연동 ---
                                    try:
                                        page_id = nh.get_active_position(sel_ticker)
                                        if not page_id:
                                            page_id = nh.create_position_journal(sel_ticker, price_in, reason_in)
                                        
                                        if page_id:
                                            nh.add_order_to_journal(page_id, pos_in, shares_in, price_in, reason_in)
                                            nh.update_position_properties(page_id, avg_price=price_in, shares=shares_in, status="진입중")
                                    except Exception as ne:
                                        pass
                                        
                                    st.cache_data.clear()
                                    st.success(f"{sel_ticker} 포트폴리오 추가 완료!")
                                    st.rerun()
                                    
                with c_wl_act4:
                    if st.button("🗑️ 관심 해제", use_container_width=True, key="wl_tab_remove_btn"):
                        sh.remove_from_watchlist(sel_ticker, sel_group)
                        st.cache_data.clear()
                        st.success(f"{sel_ticker} 관심 해제 완료 (그룹: {sel_group})!")
                        st.rerun()
                        
                # 관심 그룹 이동/변경 위젯
                with st.expander("📁 관심 그룹 이동/변경 및 신규 추가"):
                    with st.form("wl_group_change_form", clear_on_submit=True):
                        new_grp_select = st.selectbox("이동할 그룹 선택", all_wl_groups + ["+ 새 그룹 추가..."])
                        new_grp_text = ""
                        if new_grp_select == "+ 새 그룹 추가...":
                            new_grp_text = st.text_input("새 그룹 이름 입력", "").strip()
                        
                        grp_change_submit = st.form_submit_button("관심 그룹 변경 적용")
                        if grp_change_submit:
                            target_group = new_grp_text if new_grp_select == "+ 새 그룹 추가..." else new_grp_select
                            if target_group:
                                sh.remove_from_watchlist(sel_ticker, sel_group)
                                sh.add_to_watchlist(sel_ticker, target_group)
                                st.cache_data.clear()
                                st.success(f"{sel_ticker}의 관심 그룹이 '{sel_group}'에서 '{target_group}'으로 변경되었습니다.")
                                st.rerun()
                            else:
                                st.error("그룹 이름을 입력해 주세요.")
            else:
                st.info("💡 위의 관심 종목 표에서 종목 행을 클릭하시면 차트 이동, 알림 등록, 자산 매수(포폴 등록), 관심 해제 등의 단축 연동 제어가 가능합니다.")

        # 관심 그룹 추가 및 관리
        with st.expander("📁 관심 그룹 신규 생성 및 정리"):
            with st.form("wl_new_group_form"):
                st.markdown("**새 관심 그룹 및 종목 동시 생성**")
                g_ticker = st.text_input("그룹에 최초 등록할 종목 티커 입력 (예: APPL)", "").strip().upper()
                g_name = st.text_input("새로 생성할 그룹 이름 입력", "").strip()
                g_submit = st.form_submit_button("그룹 생성 및 종목 배정")
                if g_submit:
                    if g_ticker and g_name:
                        sh.add_to_watchlist(g_ticker, g_name)
                        st.cache_data.clear()
                        st.success(f"새 관심 그룹 '{g_name}'에 '{g_ticker}' 등록 완료!")
                        st.rerun()
                    else:
                        st.error("티커와 그룹 이름을 모두 입력해 주세요.")

    # ------------------ Tab 3: 조건부 타겟 ------------------
    with tab_al:
        st.subheader("🎯 조건부 타겟 감시 현황")
        alerts_df = get_alerts_cached()
        if alerts_df.empty:
            st.info("감시 중인 조건부 타겟 가격이 없습니다. 사이드바 '종목 신속 조회' 후 개별 분석 페이지에서 등록해 주세요.")
        else:
            # 화면 표출용 컬럼 리네임 및 파싱
            al_display = alerts_df.copy().rename(columns={
                'symbol': '티커',
                'target_price': '목표가 ($)',
                'condition_type': '조건 설정',
                'is_triggered': '도달 여부',
                'created_at': '등록 일시'
            })
            al_display['도달 여부'] = al_display['도달 여부'].map(lambda x: "🎯 도달완료" if x else "⏳ 대기중")
            al_display['조건 설정'] = al_display['조건 설정'].map(lambda x: "상승 돌파 (above)" if x == "above" else ("하락 돌파 (below)" if x == "below" else x))
            
            event_al = st.dataframe(
                al_display[['티커', '목표가 ($)', '조건 설정', '도달 여부', '등록 일시']],
                width="stretch",
                hide_index=True,
                selection_mode="single-row",
                on_select="rerun",
                key="al_dataframe_table"
            )
            
            # 선택된 알림 제어 패널
            selected_al_rows = event_al.selection.rows
            if selected_al_rows:
                sel_idx = selected_al_rows[0]
                if sel_idx < len(al_display):
                    sel_ticker = al_display.iloc[sel_idx]['티커']
                    sel_cond = alerts_df.iloc[sel_idx]['condition_type']
                
                c_al_act1, c_al_act2 = st.columns(2)
                with c_al_act1:
                    if st.button("📊 상세 분석 이동", key="al_tab_goto_chart", use_container_width=True):
                        st.session_state.ticker = sel_ticker
                        st.session_state.menu = "📊 개별 종목 분석"
                        st.rerun()
                with c_al_act2:
                    if st.button("🗑️ 선택된 알림 삭제", key="al_tab_delete_btn", type="primary", use_container_width=True):
                        sh.remove_alert(sel_ticker, sel_cond)
                        st.cache_data.clear()
                        st.success(f"{sel_ticker} 알림 삭제 완료!")
                        st.rerun()
            else:
                st.info("💡 위의 표에서 알림 행을 선택하시면 즉시 상세 분석으로 이동하거나 개별 삭제 처리를 할 수 있습니다.")

    # ------------------ Tab 4: 매매 기록 ------------------
    with tab_th:
        st.subheader("📝 청산 및 매매 완료 기록")
        history_df = get_trading_history_cached()
        if history_df.empty:
            st.info("완료된 포지션 청산(매매) 기록이 아직 없습니다. 포트폴리오 탭이나 개별 종목 탭에서 포지션 청산을 실행해 주세요.")
        else:
            # 청산 완료 실현손익 및 수익률 연산
            rows_hist = []
            total_profit = 0.0
            for _, row in history_df.iterrows():
                sym = row['symbol']
                shares = float(row['shares'])
                p_price = float(row['purchase_price'])
                s_price = float(row['sell_price'])
                p_type = str(row.get('position_type', 'LONG')).upper()
                entry_reason = row['entry_reason'] if pd.notna(row['entry_reason']) else ""
                exit_reason = row['exit_reason'] if pd.notna(row['exit_reason']) else ""
                trade_date = row['trade_date']
                
                if p_type == "SHORT":
                    profit = shares * (p_price - s_price)
                else:
                    profit = shares * (s_price - p_price)
                    
                profit_pct = (profit / (shares * p_price) * 100) if p_price > 0 else 0.0
                total_profit += profit
                
                rows_hist.append({
                    '티커': sym,
                    '포지션': p_type,
                    '수량': shares,
                    '평균 매수가 ($)': p_price,
                    '매도 청산가 ($)': s_price,
                    '실현 손익 ($)': profit,
                    '수익률 (%)': profit_pct,
                    '진입 근거': entry_reason,
                    '청산 사유': exit_reason,
                    '거래일': trade_date
                })
                
            hist_display_df = pd.DataFrame(rows_hist)
            
            # 요약 메트릭
            st.metric("총 누적 실현손익", f"${total_profit:,.2f}", delta=f"{total_profit:+.2f}")
            st.divider()
            
            st.dataframe(
                hist_display_df,
                width="stretch",
                hide_index=True,
                column_config={
                    "평균 매수가 ($)": st.column_config.NumberColumn("평균 매수가", format="$%.2f"),
                    "매도 청산가 ($)": st.column_config.NumberColumn("청산 단가", format="$%.2f"),
                    "실현 손익 ($)": st.column_config.NumberColumn("실현 손익", format="$%.2f"),
                    "수익률 (%)": st.column_config.NumberColumn("수익률 (%)", format="%+.2f%%")
                }
            )

