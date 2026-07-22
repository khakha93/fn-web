import div_yf as dyf
import streamlit as st
import yfinance as yf
import pandas as pd
import warnings
import plotly.graph_objects as px
from plotly.subplots import make_subplots
import sheets_helper as sh

# Streamlit/yfinance 내부 expire_cache 관련 비동기 RuntimeWarning 무시
warnings.filterwarnings("ignore", category=RuntimeWarning, message=".*expire_cache.*")

# 앱 기동 시 구글 시트 테이블 초기화 (캐시 처리)
@st.cache_resource
def run_init_sheets():
    sh.init_sheets()

run_init_sheets()


def check_price_alerts():
    """설정된 조건부 타겟 가격 알림 중, 도달한 것이 있는지 yfinance 최신 주가와 비교하여 toast로 띄웁니다."""
    if "alerts_checked" not in st.session_state:
        st.session_state.alerts_checked = {}

    try:
        alerts_df = sh.get_alerts()
    except Exception:
        return

    if alerts_df.empty:
        return

    active_alerts = alerts_df[alerts_df["is_triggered"] == False]
    if active_alerts.empty:
        return

    tickers_to_check = active_alerts["symbol"].unique().tolist()

    try:
        price_data = yf.download(tickers_to_check, period="1d", interval="1m", progress=False)
        if price_data.empty:
            return

        current_prices = {}
        if len(tickers_to_check) == 1:
            ticker = tickers_to_check[0]
            if "Close" in price_data.columns:
                current_prices[ticker] = float(price_data["Close"].iloc[-1])
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

        triggered = False
        if cond == "above" and curr_p >= target:
            triggered = True
        elif cond == "below" and curr_p <= target:
            triggered = True

        if triggered:
            cond_str = "상승 돌파" if cond == "above" else "하락 돌파"
            st.toast(f"🔔 **[조건부 타겟 도달]** {sym}의 가격이 ${target:.2f}을 {cond_str}했습니다! (현재가: ${curr_p:.2f})", icon="🎯")
            sh.set_alert_triggered(sym, cond, True)
            st.session_state.alerts_checked[alert_key] = True
            st.cache_data.clear()


check_price_alerts()

# 구글 시트 읽기 기능 캐싱
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

# Session State를 이용한 메뉴 및 기본 티커 상태 초기화
if "menu" not in st.session_state:
    st.session_state.menu = "📊 개별 종목 분석"
if "ticker" not in st.session_state:
    st.session_state.ticker = ""  # 기본값으로 코카콜라(KO) 설정

# 사이드바 네비게이션 구성
st.sidebar.title("💰 배당 모니터링 시스템")
menu_options = ["📊 개별 종목 분석", "📋 전체 종목 리스트", "💼 내 투자 관리"]
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

# 1. 개별 종목 분석 페이지
if st.session_state.menu == "📊 개별 종목 분석":
    st.markdown("<style>input { text-transform: uppercase; }</style>", unsafe_allow_html=True)
    
    col1, col2 = st.columns([5, 1])
    with col1:
        # 입력창에 session_state.ticker 자동 연계
        ticker_input = st.text_input("티커 입력", value=st.session_state.ticker).strip().upper()
    with col2:
        st.markdown("<div style='padding-top: 28px;'></div>", unsafe_allow_html=True)
        query_btn = st.button("조회", width="stretch")
        
    # 엔터를 치거나(입력 변경 감지) 조회 버튼을 클릭했을 때 모두 세션 상태 업데이트 및 새로고침
    if query_btn or (ticker_input != st.session_state.ticker):
        st.session_state.ticker = ticker_input
        st.rerun()

    ticker = st.session_state.ticker

    if not ticker:
        st.info("차트를 조회하려면 티커를 입력해주세요. (예: QCOM, KO, PG)")
        st.stop()

    @st.cache_data
    def get_stock_data(ticker):
        import os
        import datetime

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
                today = datetime.datetime.now().date()
                
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

    if "start_date" not in st.session_state:
        st.session_state.start_date = default_start
    if "end_date" not in st.session_state:
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

    render_chart_section(ticker, df_price, df_stat, df_div_period, df_com, start_date, end_date)

    # --- 통합 액션 패널 (종목 관리) 추가 ---
    try:
        current_price = float(df_price['Close'].iloc[-1])
        latest_div = float(df_com.iloc[-1]['adj_div']) if not df_com.empty else 0.0
        current_yield = latest_div / current_price if current_price > 0 else 0.0
    except Exception:
        current_price = 0.0
        current_yield = 0.0

    st.divider()
    st.subheader(f"🛠️ {ticker} 통합 액션 패널")

    col_wl, col_al, col_pf = st.columns(3)

    # 1. 관심 종목 관리
    with col_wl:
        st.markdown("##### ⭐ 관심 종목 설정")
        wl_details = get_watchlist_details_cached()
        is_in_wl = ticker in wl_details['symbol'].values
        current_group = "기본 그룹"
        if is_in_wl:
            current_group = wl_details[wl_details['symbol'] == ticker].iloc[0]['group_name']
            
        all_groups = sorted(wl_details['group_name'].dropna().unique().tolist()) if not wl_details.empty else []
        if "기본 그룹" not in all_groups:
            all_groups.insert(0, "기본 그룹")
            
        group_sel = st.selectbox("관심 그룹 선택", all_groups + ["+ 새 그룹 추가..."], index=all_groups.index(current_group) if current_group in all_groups else 0, key="quick_wl_group")
        
        if group_sel == "+ 새 그룹 추가...":
            new_group = st.text_input("새 그룹명 입력", "").strip()
            group_to_save = new_group
        else:
            group_to_save = group_sel

        c_wl_btn1, c_wl_btn2 = st.columns(2)
        with c_wl_btn1:
            if st.button("⭐ 등록/수정", width="stretch", key="wl_save_btn", type="primary"):
                if group_sel == "+ 새 그룹 추가..." and not group_to_save:
                    st.error("그룹명을 입력해주세요.")
                else:
                    sh.add_to_watchlist(ticker, group_to_save)
                    st.cache_data.clear()
                    st.success("관심 종목 등록/수정 완료!")
                    st.rerun()
        with c_wl_btn2:
            if is_in_wl:
                if st.button("🗑️ 관심 해제", width="stretch", key="wl_del_btn"):
                    sh.remove_from_watchlist(ticker)
                    st.cache_data.clear()
                    st.success("관심 해제 완료!")
                    st.rerun()
            else:
                st.button("🗑️ 관심 해제", width="stretch", disabled=True, key="wl_del_btn_dis")

    # 2. 조건부 타겟 관리
    with col_al:
        st.markdown("##### 🎯 조건부 타겟 설정")
        alerts_df = get_alerts_cached()
        my_alerts = alerts_df[alerts_df['symbol'] == ticker]
        if not my_alerts.empty:
            alert_items = []
            for _, a_row in my_alerts.iterrows():
                cond_str = "상승 돌파" if a_row['condition_type'] == "above" else "하락 돌파"
                trig_str = "(도달완료)" if a_row['is_triggered'] else "(대기중)"
                alert_items.append(f"${a_row['target_price']:.2f} {cond_str} {trig_str}")
            st.caption("감시 중: " + ", ".join(alert_items))
        else:
            st.caption("설정된 타겟 가격이 없습니다.")

        target_p_in = st.number_input("목표 주가 ($)", min_value=0.0, value=current_price, step=0.01, key="quick_al_price")
        cond_type_in = st.selectbox("조건 설정", ["above", "below"], format_func=lambda x: "📈 상승 돌파 시" if x == "above" else "📉 하락 돌파 시", key="quick_al_cond")

        c_al_btn1, c_al_btn2 = st.columns(2)
        with c_al_btn1:
            if st.button("🎯 타겟 등록", width="stretch", key="al_save_btn", type="primary"):
                sh.save_alert(ticker, target_p_in, cond_type_in)
                st.cache_data.clear()
                st.success("조건부 타겟이 저장되었습니다.")
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

    # 3. 포트폴리오 관리
    with col_pf:
        st.markdown("##### 💼 포트폴리오 관리")
        portfolio_df = get_portfolio_cached()
        in_portfolio = ticker in portfolio_df['symbol'].values
        p_shares = 0.0
        p_price = 0.0
        p_entry_reason = ""
        if in_portfolio:
            p_row = portfolio_df[portfolio_df['symbol'] == ticker].iloc[0]
            p_shares = float(p_row['shares'])
            p_price = float(p_row['purchase_price'])
            p_entry_reason = str(p_row['entry_reason']) if pd.notna(p_row['entry_reason']) else ""
            st.caption(f"보유 중: {p_shares}주 (평단 ${p_price:.2f})")
        else:
            st.caption("현재 미보유 상태입니다.")

        with st.popover("💼 보유 자산 정보 수정", width="stretch"):
            with st.form("pf_edit_form", clear_on_submit=False):
                shares_in = st.number_input("보유 수량 (주)", min_value=0.0, value=p_shares, step=0.1)
                price_in = st.number_input("평균 매수 단가 ($)", min_value=0.0, value=p_price, step=0.01)
                reason_in = st.text_area("진입 근거", value=p_entry_reason, height=80)
                pf_submit = st.form_submit_button("저장하기", width="stretch")
                if pf_submit:
                    if shares_in > 0:
                        sh.save_portfolio(ticker, shares_in, price_in, reason_in)
                        st.cache_data.clear()
                        st.success("포트폴리오가 정상 저장되었습니다.")
                        st.rerun()
                    else:
                        if in_portfolio:
                            sh.remove_from_portfolio(ticker)
                            st.cache_data.clear()
                            st.success("포트폴리오에서 삭제되었습니다.")
                            st.rerun()

    st.markdown("##### ✍️ 투자 메모 및 코멘트")
    saved_comment = get_comment_cached(ticker)
    comment_in = st.text_area("이 종목에 대한 분석이나 매수 근거 등의 기록을 남겨보세요.", value=saved_comment, height=120)

    col_btn1, col_btn2 = st.columns(2)
    with col_btn1:
        if st.button("📝 구글 시트에 코멘트 저장", width="stretch"):
            sh.save_comment(ticker, comment_in)
            st.cache_data.clear()
            st.success("코멘트가 성공적으로 구글 시트에 저장되었습니다.")
            st.rerun()
            
    with col_btn2:
        if st.button("📝 노션에 투자일지 기록", width="stretch", type="primary"):
            import notion_helper as nh
            with st.spinner("노션 API 전송 중..."):
                success = nh.send_journal_to_notion(ticker, current_price, current_yield, comment_in)
            if success:
                st.success(f"🎉 {ticker} 투자일지가 노션에 성공적으로 기록되었습니다!")
            else:
                st.error("노션 기록에 실패했습니다. secrets.toml의 토큰과 DB ID 설정을 확인해 주세요.")

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
                                st.cache_data.clear()
                                st.toast(f"💼 {sel_ticker} {shares_in}주 저장 완료!")
                                st.rerun()
                            else:
                                if in_portfolio:
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
            st.info("포트폴리오가 현재 비어 있습니다. '📊 개별 종목 분석' 페이지에서 자산을 추가해 주세요.")
        else:
            pf_tickers = portfolio_df['symbol'].tolist()
            close_prices = {}
            
            with st.spinner("보유 종목의 최신 주가 정보를 조회 중..."):
                try:
                    price_data = yf.download(pf_tickers, period="5d", interval="1d")
                    if len(pf_tickers) == 1:
                        close_prices[pf_tickers[0]] = float(price_data['Close'].iloc[-1])
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
                
                curr_price = close_prices.get(sym, 0.0)
                if curr_price == 0.0:
                    curr_price = avg_cost
                    
                stock_info = stocks_df[stocks_df['symbol'] == sym]
                name = stock_info.iloc[0]['companyName'] if not stock_info.empty else sym
                
                # FMP/yfinance의 single payout 배당금에 4(분기 배당 가정)를 곱해 연간 배당금으로 환산
                last_div = float(stock_info.iloc[0]['lastDividend']) if not stock_info.empty else 0.0
                annual_div_per_share = last_div * 4
                
                cost = shares * avg_cost
                val = shares * curr_price
                annual_div = shares * annual_div_per_share
                
                total_invested += cost
                total_current_val += val
                total_annual_div += annual_div
                
                gain_loss = val - cost
                gain_loss_pct = (gain_loss / cost * 100) if cost > 0 else 0.0
                
                rows.append({
                    '티커': sym,
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
            m4.metric("평균 배당수익률 (현재가 기준)", f"{avg_yield:.2f}%")
            
            st.divider()
            
            # 보유 종목 상세 내역 테이블
            pf_display_df = pd.DataFrame(rows)
            st.dataframe(
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
                }
            )
            
            # 관리 및 연계
            st.subheader("⚙️ 포트폴리오 자산 개별 제어")
            col_sel, col_del = st.columns([3, 1])
            with col_sel:
                sel_ticker = st.selectbox("분석 차트로 이동할 자산 선택", ["선택 안 함"] + pf_tickers)
                if sel_ticker != "선택 안 함":
                    st.session_state.ticker = sel_ticker
                    st.session_state.menu = "📊 개별 종목 분석"
                    st.rerun()
            with col_del:
                del_ticker = st.selectbox("포트폴리오에서 삭제할 자산 선택", ["선택 안 함"] + pf_tickers)
                if del_ticker != "선택 안 함":
                    if st.button("🗑️ 선택 자산 삭제", width="stretch"):
                        sh.remove_from_portfolio(del_ticker)
                        st.cache_data.clear()
                        st.success(f"{del_ticker} 삭제 성공!")
                        st.rerun()

    with tab_wl:
        st.subheader("⭐ 내 관심 종목 목록")
        watchlist = get_watchlist_cached()
        
        if not watchlist:
            st.info("관심 등록된 종목이 없습니다. '📊 개별 종목 분석' 페이지에서 추가해 주세요.")
        else:
            watchlist_df = stocks_df[stocks_df['symbol'].isin(watchlist)]
            if watchlist_df.empty:
                st.info("관심 등록한 종목이 있지만 마스터 종목 정보에 존재하지 않습니다.")
            else:
                wl_display = watchlist_df.copy().rename(columns={
                    'symbol': '티커',
                    'companyName': '회사명',
                    'lastDividend': '최근 주당 배당금 ($)',
                    'stock_type': '자산 분류',
                    'updated_at': '마지막 동기화'
                })
                
                st.dataframe(
                    wl_display[['티커', '회사명', '최근 주당 배당금 ($)', '자산 분류', '마지막 동기화']],
                    width="stretch",
                    hide_index=True,
                    column_config={
                        "최근 주당 배당금 ($)": st.column_config.NumberColumn("최근 주당 배당금 ($)", format="$%.4f")
                    }
                )
                
                wl_sel = st.selectbox("📊 분석 차트로 이동할 관심 종목 선택", ["선택 안 함"] + wl_display['티커'].tolist())
                if wl_sel != "선택 안 함":
                    st.session_state.ticker = wl_sel
                    st.session_state.menu = "📊 개별 종목 분석"
                    st.rerun()

