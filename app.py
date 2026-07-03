import div_yf as dyf

import streamlit as st
import yfinance as yf
import pandas as pd
import warnings

# Streamlit/yfinance 내부 expire_cache 관련 비동기 RuntimeWarning 무시
warnings.filterwarnings("ignore", category=RuntimeWarning, message=".*expire_cache.*")

import plotly.graph_objects as px
from plotly.subplots import make_subplots

st.title("미국 배당주 모니터링")

# 1. 자산 및 기간 선택 UI
# 입력창에 소문자를 타이핑해도 화면에 자동으로 대문자로 표시되도록 CSS 적용
st.markdown("<style>input { text-transform: uppercase; }</style>", unsafe_allow_html=True)

col1, col2 = st.columns([5, 1])
with col1:
    ticker = st.text_input("티커 입력", "").strip().upper()
with col2:
    st.markdown("<div style='padding-top: 28px;'></div>", unsafe_allow_html=True)
    st.button("조회", use_container_width=True)

if not ticker:
    st.info("차트를 조회하려면 티커를 입력해주세요. (예: QCOM, AAPL, TSLA)")
    st.stop()

@st.cache_data
def get_stock_data(ticker):
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
    _, df_stat = dyf.merge_dividend_data(df_close, df_com)
    
    # 데이터의 날짜 범위 확인 (timezone 제거하여 일치시킴)
    df_price.index = df_price.index.tz_localize(None)
    df_stat['Date'] = pd.to_datetime(df_stat['Date']).dt.tz_localize(None)
    
    return df_price, df_stat, df_div_period, df_com

df_price, df_stat, df_div_period, df_com = get_stock_data(ticker)

min_date = df_price.index.min().to_pydatetime().date()
max_date = df_price.index.max().to_pydatetime().date()

# 기본 조회 범위를 2010년 10월 1일로 설정 (데이터 시작일이 그보다 늦은 경우 데이터 시작일로 대체)
default_start = max(min_date, pd.to_datetime("2010-01-01").date())

# Session State를 이용한 시작일/종료일 상태 초기화
if "start_date" not in st.session_state:
    st.session_state.start_date = default_start
if "end_date" not in st.session_state:
    st.session_state.end_date = max_date

# 상세 기간 설정용 expander 추가 (모바일 화면 최적화)
with st.expander("📅 상세 기간 직접 설정 (날짜 지정)", expanded=False):
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
        submitted = st.form_submit_button(label="기간 적용 및 조회", use_container_width=True)

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

# st.fragment 데코레이터 지원 확인 및 조건부 정의
def conditional_fragment(func):
    if hasattr(st, "fragment"):
        return st.fragment()(func)
    return func

@conditional_fragment
def render_chart_section(ticker, df_price, df_stat, df_div_period, df_com, start_date, end_date):
    tab1, tab2 = st.tabs(["📊 분석 차트", "📜 배당 상세 내역"])
    with tab1:
            # 1. Quick Period Selector (Streamlit 가로형 라디오 버튼)
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

            # 선택한 기간으로 데이터 필터링
            df_filtered = df_price.loc[actual_start_date:actual_end_date]
            df_stat_filtered = df_stat[
                (df_stat['Date'] >= actual_start_date) & 
                (df_stat['Date'] <= actual_end_date)
            ]
            df_com_filtered = df_com[
                (df_com['start_date'] >= actual_start_date) & 
                (df_com['start_date'] <= actual_end_date)
            ].copy()

            # 우측 버퍼(5%)를 데이터 자체에 강제로 공백 행(NaN)으로 주입하여 여백 형성 (비율이 현재 표시된 기간 기준으로 유동적 계산됨)
            if not df_filtered.empty:
                time_buffer = (actual_end_date - actual_start_date) * 0.05
                buffer_date = actual_end_date + time_buffer

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
                px.Scatter(x=df_filtered_buffered.index, y=df_filtered_buffered['Close'], name="주가 (종가)", line=dict(color='royalblue', width=1), hovertemplate="%{y}<extra></extra>"),
                row=1, col=1
            )

            # 내가 만든 고유 지표를 하단 차트에 따로 그리기 (빨간 점선, 버퍼가 적용된 데이터 사용)
            fig.add_trace(
                px.Scatter(x=df_stat_filtered_buffered.Date, y=df_stat_filtered_buffered['dfs'], name="배당수익률(DFS)", line=dict(color='firebrick', width=1, dash='solid'), hovertemplate="%{y}<extra></extra>"),
                row=2, col=1
            )

            # 3단: 배당금 (Adjusted Dividend) - 단계형 선(step line)으로 구성
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

            # 4단: 배당 성장률 (div_change) - 단계형 영역 차트(Step Area Chart)로 구성
            fig.add_trace(
                px.Scatter(
                    x=df_stat_filtered_buffered.Date,
                    y=df_stat_filtered_buffered['div_change'] * 100,
                    name="배당 성장률 (%)",
                    line=dict(color='rgba(46, 204, 113, 1)', width=1.5, shape='hv'),
                    fill='tozeroy',
                    fillcolor='rgba(46, 204, 113, 0.15)',  # 반투명 초록색으로 하단 영역 채움
                    hovertemplate="%{y}<extra></extra>"
                ),
                row=4, col=1
            )

            # 5단: 주가 비교 (Close vs Adj Close)
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
                    (df_div_period_temp['Date'] >= pd.to_datetime(actual_start_date)) & 
                    (df_div_period_temp['Date'] <= pd.to_datetime(actual_end_date))
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

                # period 변경 지점들에 x축 눈금(ticks) 설정 (모든 서브플롯에 표시하되, 가독성을 위해 -45도 회전)
                if tick_vals:
                    # Plotly가 날짜 축에서 올바르게 직렬화하여 인식할 수 있도록 pandas Timestamp를 파이썬 내장 datetime 객체로 변환합니다.
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

            # 차트 레이아웃 조정 (HTS 느낌 내기)
            fig.update_layout(
                title=dict(
                    text=f"{ticker} 주가 및 배당 분석",
                    x=0.5,
                    xanchor="center",
                    y=0.965, # 높이가 2000px로 줄었으므로 상단 마진 내에서 타이틀 위치 재조정
                    yanchor="top"
                ),
                hovermode="x unified", # 마우스를 올리면 같은 날짜의 모든 지표를 한눈에 보여줌 (HTS 핵심 기능)
                template="plotly_dark", # 어두운 HTS 테마 느낌
                height=2000, # 모바일 스크롤 및 화면 비율을 고려해 2000으로 조정 (각 subplot 400px 수준)
                showlegend=False, # 전체 범례를 숨기고 개별 그래프 내부에 표시
                margin=dict(l=50, r=20, t=120, b=50), # 상단 여백을 120으로 조정하여 공간 확보
                hoverlabel=dict(
                    bgcolor="rgba(33, 37, 41, 0.3)",     # 투명도를 높인(30%) 어두운 배경
                    font_color="white",                  # 글자 색상
                    font_size=11,                        # 글자 크기
                    bordercolor="rgba(255, 255, 255, 0.1)" # 더욱 은은한 테두리
                )
            )

            # 서브플롯 제목들의 폰트 크기 및 위치 조정 (위쪽 겹침을 방지하기 위해 조금만 위로 띄움)
            for annotation in fig.layout.annotations:
                annotation.font.size = 12
                annotation.y = annotation.y + 0.007

            # 7. 복수 지표가 들어가는 최하단 서브플롯(Row 5)에만 개별 범례(Legend) 표시 (단일 지표인 Row 1~4는 제목과 중복되므로 제거하여 모바일 공간 확보)
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

            # 각 서브플롯의 y축 스타일 및 줌 고정 설정 (서브플롯 제목과 중복되는 y축 제목은 제거하여 가로 공간 확보)
            fig.update_yaxes(
                fixedrange=True,
                showline=True,
                linewidth=1,
                linecolor='rgba(255, 255, 255, 0.3)',
                mirror=False
            )

            # x축 선 및 스타일 설정 (날짜임이 명확하므로 '날짜' 제목 제거하여 세로 공간 절약)
            fig.update_xaxes(
                type="date",            # x축을 날짜 축으로 강제
                showline=True, 
                linewidth=1, 
                linecolor='rgba(255, 255, 255, 0.8)', 
                mirror=False,
                ticks="outside",        # 눈금 표시선(Tick mark)을 축 바깥쪽으로 표시
                ticklen=5,              # 눈금 표시선 길이
                tickwidth=1,            # 눈금 표시선 두께를 1로 글로벌 통일
                tickcolor="grey",       # 눈금 표시선 색상
                rangeslider=dict(visible=False) # 범위 선택기(Rangeslider) 공백 제거
            )

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

            fig.update_yaxes(
                showspikes=True,
                spikethickness=1,
                spikedash="dot",
                spikecolor="grey",
                spikemode="across",
                spikesnap="data"
            )

            # 5. 웹 화면에 차트 띄우기
            st.plotly_chart(fig, use_container_width=True, config={'scrollZoom': True, 'doubleClick': 'reset'})


    with tab2:
        st.markdown("### 📜 배당 변동 주기별 상세 내역")
        st.markdown("각 배당금 지급 주기별 주요 통계 및 배당성장률 요약표입니다. (최신 주기 순 정렬)")
        
        if not df_com_filtered.empty:
            # 1. 상단 통계 카드 메트릭 추가
            latest_row = df_com_filtered.iloc[-1]
            latest_div = latest_row['adj_div']
            latest_growth = latest_row['div_change'] * 100 if pd.notnull(latest_row['div_change']) else 0
            avg_growth = df_com_filtered['div_change'].mean() * 100 if df_com_filtered['div_change'].notnull().any() else 0
            
            m1, m2, m3 = st.columns(3)
            m1.metric("현재 연간 배당금 (환산)", f"${latest_div:.4f}")
            m2.metric("최근 배당 성장률", f"{latest_growth:+.2f}%" if pd.notnull(latest_row['div_change']) else "-")
            m3.metric("평균 배당 성장률", f"{avg_growth:+.2f}%")
            
            st.divider()
            
            # 2. 데이터 가공
            df_display = df_com_filtered.copy()
            # 최신 period 순 정렬
            df_display = df_display.sort_values('period', ascending=False)
            
            # 컬럼명 매핑 및 필터링
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
            
            # 배당 성장률 백분율 변환 (100 곱하기)
            df_display['배당 성장률'] = df_display['배당 성장률'] * 100
            
            # 데이터프레임 렌더링
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
