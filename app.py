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

# 앱 기동 시 구글 시트 테이블 초기화
sh.init_sheets()

# Session State를 이용한 메뉴 및 기본 티커 상태 초기화
if "menu" not in st.session_state:
    st.session_state.menu = "📊 개별 종목 분석"
if "ticker" not in st.session_state:
    st.session_state.ticker = ""  # 기본값으로 코카콜라(KO) 설정

# 사이드바 네비게이션 구성
st.sidebar.title("💰 배당 모니터링 시스템")
menu_options = ["📊 개별 종목 분석", "📋 전체 종목 리스트", "💼 내 자산 & 관심 종목"]
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
        query_btn = st.button("조회", use_container_width=True)
        
    if query_btn:
        st.session_state.ticker = ticker_input
        st.rerun()

    ticker = st.session_state.ticker

    if not ticker:
        st.info("차트를 조회하려면 티커를 입력해주세요. (예: QCOM, KO, PG)")
        st.stop()

    @st.cache_data
    def get_stock_data(ticker):
        # 데이터 가져오기
        df_price = yf.download(ticker, period="max", auto_adjust=False)
        df_close = df_price['Close'].copy()
        
        # 단일 티커 검색 시 MultiIndex 컬럼일 경우 평탄화
        if isinstance(df_price.columns, pd.MultiIndex):
            df_price.columns = df_price.columns.droplevel(1)

        # 배당수익률 지표 계산 로직
        df_div = dyf.get_yf_dividend_history(ticker)
        df_div_period = dyf.add_period_columns_by_div(df_div)
        df_com = dyf.group_by_period_by_div(df_div_period)
        _, df_stat = dyf.merge_dividend_data(df_close, df_com)
        
        # 데이터의 날짜 범위 확인 (timezone 제거하여 일치시킴)
        df_price.index = df_price.index.tz_localize(None)
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
            submitted = st.form_submit_button(label="기간 적용 및 조회", use_container_width=True)

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

            st.plotly_chart(fig, use_container_width=True, config={'scrollZoom': True, 'doubleClick': 'reset'})

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
                    use_container_width=True,
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

    # --- 개인 기록 관리 섹션 추가 ---
    st.divider()
    st.subheader(f"📝 {ticker} 개인 기록 관리")

    col_wl, col_pf = st.columns(2)

    # 관심 종목 체크 및 설정
    watchlist = sh.get_watchlist()
    is_in_wl = ticker in watchlist

    with col_wl:
        st.markdown("##### ⭐ 관심 종목 설정")
        if is_in_wl:
            if st.button("⭐ 관심 종목에서 해제", use_container_width=True):
                sh.remove_from_watchlist(ticker)
                st.success("관심 종목에서 해제되었습니다.")
                st.rerun()
        else:
            if st.button("⭐ 관심 종목으로 등록", use_container_width=True, type="primary"):
                sh.add_to_watchlist(ticker)
                st.success("관심 종목으로 등록되었습니다.")
                st.rerun()

    # 포트폴리오 정보 불러오기
    portfolio_df = sh.get_portfolio()
    in_portfolio = ticker in portfolio_df['symbol'].values
    p_shares = 0.0
    p_price = 0.0
    if in_portfolio:
        row = portfolio_df[portfolio_df['symbol'] == ticker].iloc[0]
        p_shares = float(row['shares'])
        p_price = float(row['purchase_price'])

    with col_pf:
        st.markdown("##### 💼 포트폴리오 관리")
        status_txt = f"현재 보유 중: {p_shares}주 (평단 ${p_price:.2f})" if in_portfolio else "현재 미보유"
        st.caption(status_txt)
        
        with st.popover("💼 보유 자산 정보 수정", use_container_width=True):
            with st.form("pf_edit_form", clear_on_submit=False):
                shares_in = st.number_input("보유 수량 (주)", min_value=0.0, value=p_shares, step=0.1)
                price_in = st.number_input("평균 매수 단가 ($)", min_value=0.0, value=p_price, step=0.01)
                pf_submit = st.form_submit_button("저장하기", use_container_width=True)
                if pf_submit:
                    if shares_in > 0:
                        sh.save_portfolio(ticker, shares_in, price_in)
                        st.success("포트폴리오가 정상적으로 저장되었습니다.")
                        st.rerun()
                    else:
                        if in_portfolio:
                            sh.remove_from_portfolio(ticker)
                            st.success("포트폴리오에서 삭제되었습니다.")
                            st.rerun()

    st.markdown("##### ✍️ 투자 메모 및 코멘트")
    saved_comment = sh.get_comment(ticker)
    comment_in = st.text_area("이 종목에 대한 분석이나 매수 근거 등의 기록을 남겨보세요.", value=saved_comment, height=120)
    if st.button("📝 코멘트 저장", use_container_width=True):
        sh.save_comment(ticker, comment_in)
        st.success("코멘트가 성공적으로 저장되었습니다.")
        st.rerun()

# 2. 전체 종목 리스트 페이지
elif st.session_state.menu == "📋 전체 종목 리스트":
    st.header("📋 전체 배당 종목 리스트")
    st.markdown("구글 스프레드시트에 연동된 나스닥 + S&P 500의 배당주 및 주요 배당 ETF 목록입니다.")
    
    with st.spinner("종목 리스트 불러오는 중..."):
        stocks_df = sh.get_stocks()
        
    if stocks_df.empty:
        st.warning("구글 시트에 저장된 종목 데이터가 없습니다. 아래 업데이트 버튼을 눌러 데이터를 수집해 주세요.")
    else:
        if "group" not in stocks_df.columns:
            stocks_df["group"] = ""
        if "weight" not in stocks_df.columns:
            stocks_df["weight"] = pd.NA

        stocks_df["group"] = stocks_df["group"].fillna("").astype(str).str.strip()
        stocks_df["weight"] = pd.to_numeric(stocks_df["weight"], errors="coerce")

        # 검색 및 필터 UI
        col_search, col_asset, col_group = st.columns([3, 1, 1])
        with col_search:
            search_query = st.text_input("종목 검색 (티커 또는 회사명)", "").strip().upper()
        with col_asset:
            asset_filter = st.selectbox("자산 분류 필터", ["전체", "주식", "ETF 구성종목"])
        with col_group:
            preferred = ["S&P", "Nasdaq", "SCHD", "VIG", "DGRO"]
            groups = [g for g in stocks_df["group"].dropna().unique().tolist() if str(g).strip()]
            group_ordered = [g for g in preferred if g in groups] + sorted([g for g in groups if g not in preferred])
            group_filter = st.selectbox("그룹 필터", ["전체"] + group_ordered)

        filtered_df = stocks_df.copy()
        etf_groups = ["SCHD", "VIG", "DGRO"]

        # 필터 적용
        if asset_filter == "ETF 구성종목":
            filtered_df = filtered_df[filtered_df["group"].isin(etf_groups)]
        elif asset_filter == "주식":
            filtered_df = filtered_df[~filtered_df["group"].isin(etf_groups)]

        if group_filter != "전체":
            filtered_df = filtered_df[filtered_df["group"] == group_filter]

        if search_query:
            filtered_df = filtered_df[
                filtered_df["symbol"].astype(str).str.contains(search_query, case=False, na=False)
                | filtered_df["companyName"].astype(str).str.contains(search_query, case=False, na=False)
            ]

        st.markdown(f"**총 {len(filtered_df)}개의 배당 자산이 조회되었습니다.**")

        # 종목 빠른 분석 연계
        selected_ticker = st.selectbox("📊 상세 차트 분석으로 이동할 종목 선택", ["선택 안 함"] + filtered_df["symbol"].tolist())
        if selected_ticker != "선택 안 함":
            st.session_state.ticker = selected_ticker
            st.session_state.menu = "📊 개별 종목 분석"
            st.rerun()

        # 테이블 표시
        display_df = filtered_df.copy()
        display_df = display_df.rename(columns={
            "symbol": "티커",
            "companyName": "회사명",
            "lastDividend": "최근 주당 배당금 ($)",
            "stock_type": "자산 유형",
            "group": "그룹",
            "weight": "비중(%)",
            "updated_at": "동기화 일자",
        })

        columns_to_show = ["티커", "회사명", "최근 주당 배당금 ($)", "자산 유형", "그룹", "동기화 일자"]
        if group_filter in etf_groups or asset_filter == "ETF 구성종목":
            columns_to_show.insert(5, "비중(%)")

        st.dataframe(
            display_df[columns_to_show],
            use_container_width=True,
            hide_index=True,
            column_config={
                "최근 주당 배당금 ($)": st.column_config.NumberColumn("최근 주당 배당금 ($)", format="$%.4f"),
                "비중(%)": st.column_config.NumberColumn("비중(%)", format="%.4f"),
            },
        )

    st.divider()
    st.subheader("🔄 데이터 실시간 강제 동기화")
    st.markdown("Wikipedia와 무료 소스를 참조하여 S&P 500, 나스닥 100, SCHD/VIG/DGRO 구성종목 데이터를 동기화합니다. (약 30~120초 소요)")
    if st.button("🔄 종목 리스트 및 배당정보 수동 업데이트", use_container_width=True):
        with st.spinner("웹 스크레이퍼 및 yfinance를 실행하여 구글 시트 데이터를 동기화하는 중..."):
            try:
                import fetch_dividend_stocks as fds
                fds.run_update()

                success_groups = getattr(fds, "LAST_SUCCESS_GROUPS", [])
                failed_groups = getattr(fds, "LAST_FAILED_GROUPS", [])

                if success_groups:
                    st.info(f"성공 그룹: {', '.join(success_groups)}")
                if failed_groups:
                    st.warning(f"실패 그룹(부분 실패): {', '.join(failed_groups)}")

                if failed_groups:
                    st.success("구글 시트와 부분 동기화가 완료되었습니다.")
                else:
                    st.success("구글 시트와 동기화가 성공적으로 완료되었습니다!")
                st.rerun()
            except Exception as e:
                st.error(f"동기화 중 오류 발생: {e}")

# 3. 내 자산 & 관심 종목 페이지
elif st.session_state.menu == "💼 내 자산 & 관심 종목":
    st.header("💼 내 자산 & 관심 종목")
    
    tab_pf, tab_wl = st.tabs(["💼 내 포트폴리오", "⭐ 관심 종목"])
    
    # 캐시된 종목 마스터 리스트 로딩
    stocks_df = sh.get_stocks()
    
    with tab_pf:
        st.subheader("보유 자산 현황 요약")
        portfolio_df = sh.get_portfolio()
        
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
                use_container_width=True,
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
                    if st.button("🗑️ 선택 자산 삭제", use_container_width=True):
                        sh.remove_from_portfolio(del_ticker)
                        st.success(f"{del_ticker} 삭제 성공!")
                        st.rerun()

    with tab_wl:
        st.subheader("⭐ 내 관심 종목 목록")
        watchlist = sh.get_watchlist()
        
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
                    use_container_width=True,
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

