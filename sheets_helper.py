import streamlit as st
import gspread
from gspread_dataframe import set_with_dataframe, get_as_dataframe
import pandas as pd
import datetime

STOCKS_COLUMNS = [
    "symbol",
    "companyName",
    "lastDividend",
    "stock_type",
    "group",
    "weight",
    "marketCap",
    "dividendYield",
    "updated_at",
]

# Streamlit secrets에서 구글 서비스 계정 키 및 스프레드시트 URL 정보 읽기
try:
    creds = dict(st.secrets["gcp_service_account"])
    # private_key가 JSON에서 개행 문자가 \n 문자열로 들어가 있을 수 있으므로 처리
    if "private_key" in creds:
        creds["private_key"] = creds["private_key"].replace("\\n", "\n")

    gc = gspread.service_account_from_dict(creds)
    spreadsheet_url = st.secrets["google_sheets"]["spreadsheet_url"]
    sh = gc.open_by_url(spreadsheet_url)
except Exception as e:
    st.error(f"구글 시트 연결 실패: secrets.toml 설정 및 공유 상태를 확인해 주세요. 에러: {e}")
    sh = None


def _normalize_stocks_df(df):
    if df is None or df.empty:
        return pd.DataFrame(columns=STOCKS_COLUMNS)

    safe_df = df.copy()
    for col in STOCKS_COLUMNS:
        if col not in safe_df.columns:
            safe_df[col] = ""

    safe_df = safe_df[STOCKS_COLUMNS]
    safe_df = safe_df.dropna(subset=["symbol"])
    safe_df["symbol"] = safe_df["symbol"].astype(str).str.strip().str.upper()
    safe_df = safe_df[safe_df["symbol"] != ""]
    safe_df["companyName"] = safe_df["companyName"].fillna("").astype(str).str.strip()
    safe_df["stock_type"] = safe_df["stock_type"].fillna("STOCK").astype(str).str.strip().str.upper()
    safe_df["group"] = safe_df["group"].fillna("").astype(str).str.strip()
    safe_df["weight"] = pd.to_numeric(safe_df["weight"], errors="coerce")
    safe_df["marketCap"] = pd.to_numeric(safe_df["marketCap"], errors="coerce")
    safe_df["dividendYield"] = pd.to_numeric(safe_df["dividendYield"], errors="coerce")
    safe_df["updated_at"] = safe_df["updated_at"].fillna("").astype(str)
    return safe_df


def _ensure_stocks_schema():
    """stocks 시트 컬럼이 최신 스키마를 따르도록 보정합니다."""
    if not sh:
        return

    ws = sh.worksheet("stocks")
    all_values = ws.get_all_values()

    if not all_values:
        ws.append_row(STOCKS_COLUMNS)
        return

    headers = all_values[0]
    if headers == STOCKS_COLUMNS:
        return

    df = get_as_dataframe(ws, evaluate_formulas=True)
    df = _normalize_stocks_df(df)
    ws.clear()
    set_with_dataframe(ws, df, row=1, col=1, include_index=False, resize=True)


def init_sheets():
    """앱 구동 시 필요한 4개의 시트(stocks, comments, watchlist, portfolio)가 없으면 생성합니다."""
    if not sh:
        return

    required_sheets = {
        "stocks": STOCKS_COLUMNS,
        "comments": ["symbol", "content", "updated_at"],
        "watchlist": ["symbol", "created_at"],
        "portfolio": ["symbol", "shares", "purchase_price", "created_at"],
    }

    existing_sheets = [ws.title for ws in sh.worksheets()]

    for sheet_name, headers in required_sheets.items():
        if sheet_name not in existing_sheets:
            # 시트가 존재하지 않으면 새로 생성
            ws = sh.add_worksheet(title=sheet_name, rows=1000, cols=len(headers) + 2)
            # 헤더 삽입
            ws.append_row(headers)
            print(f"구글 시트 생성 완료: {sheet_name}")

    _ensure_stocks_schema()


# --- 1. 종목 캐시 (stocks) 관련 ---
def get_stocks():
    """stocks 시트에서 전체 종목 데이터를 DataFrame으로 조회합니다."""
    if not sh:
        return pd.DataFrame(columns=STOCKS_COLUMNS)

    _ensure_stocks_schema()
    ws = sh.worksheet("stocks")
    df = get_as_dataframe(ws, evaluate_formulas=True)
    return _normalize_stocks_df(df)


def save_stocks(df):
    """stocks 시트에 새로운 종목 리스트를 덮어씁니다."""
    if not sh:
        return

    _ensure_stocks_schema()
    safe_df = _normalize_stocks_df(df)

    ws = sh.worksheet("stocks")
    ws.clear()  # 기존 데이터 초기화

    # gspread_dataframe을 이용해 일괄 쓰기
    set_with_dataframe(ws, safe_df, row=1, col=1, include_index=False, resize=True)

    # weight 열(F)은 숫자 서식으로 고정해야 4.5가 날짜(1900-01-03 12:00:00)로 보존되지 않습니다.
    try:
        ws.format(
            "F:F",
            {
                "numberFormat": {
                    "type": "NUMBER",
                    "pattern": "0.00",
                }
            },
        )
    except Exception:
        # 서식 지정 실패 시에도 저장 자체는 완료되도록 무시
        pass


# --- 2. 코멘트 (comments) 관련 ---
def get_comment(symbol):
    """특정 종목의 코멘트를 구글 시트에서 가져옵니다."""
    if not sh:
        return ""
    ws = sh.worksheet("comments")
    data = ws.get_all_records()
    for row in data:
        if str(row.get("symbol")).strip().upper() == symbol.strip().upper():
            return row.get("content", "")
    return ""


def save_comment(symbol, content):
    """코멘트를 저장하거나 수정합니다."""
    if not sh:
        return
    ws = sh.worksheet("comments")
    data = ws.get_all_values()

    headers = data[0] if data else ["symbol", "content", "updated_at"]
    rows = data[1:] if len(data) > 1 else []

    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    found_idx = -1

    for i, row in enumerate(rows):
        if row and row[0].strip().upper() == symbol.strip().upper():
            found_idx = i
            break

    if found_idx != -1:
        # 기존 행 수정 (행 인덱스는 1부터 시작하고 헤더가 1이므로 +2)
        row_num = found_idx + 2
        ws.update_cell(row_num, 2, content)
        ws.update_cell(row_num, 3, now_str)
    else:
        # 새 코멘트 추가
        ws.append_row([symbol.upper(), content, now_str])


# --- 3. 관심 종목 (watchlist) 관련 ---
def get_watchlist():
    """관심 종목 리스트를 가져옵니다."""
    if not sh:
        return []
    ws = sh.worksheet("watchlist")
    data = ws.get_all_records()
    watchlist = []
    for row in data:
        symbol = str(row.get("symbol")).strip().upper()
        if symbol:
            watchlist.append(symbol)
    return watchlist


def add_to_watchlist(symbol):
    """관심 종목에 추가합니다."""
    if not sh:
        return
    ws = sh.worksheet("watchlist")
    symbol = symbol.strip().upper()
    existing = get_watchlist()
    if symbol not in existing:
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ws.append_row([symbol, now_str])


def remove_from_watchlist(symbol):
    """관심 종목에서 제거합니다."""
    if not sh:
        return
    ws = sh.worksheet("watchlist")
    symbol = symbol.strip().upper()
    data = ws.get_all_values()

    for i, row in enumerate(data):
        if i == 0:
            continue
        if row and row[0].strip().upper() == symbol:
            # 삭제 (행 번호는 1-indexed이므로 i + 1)
            ws.delete_rows(i + 1)
            break


# --- 4. 포트폴리오 (portfolio) 관련 ---
def get_portfolio():
    """포트폴리오 리스트를 DataFrame으로 가져옵니다."""
    if not sh:
        return pd.DataFrame(columns=["symbol", "shares", "purchase_price", "created_at"])
    ws = sh.worksheet("portfolio")
    df = get_as_dataframe(ws).dropna(subset=["symbol"])
    return df


def save_portfolio(symbol, shares, purchase_price):
    """포트폴리오 아이템을 추가하거나 수정합니다."""
    if not sh:
        return
    ws = sh.worksheet("portfolio")
    symbol = symbol.strip().upper()
    data = ws.get_all_values()

    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    found_idx = -1

    for i, row in enumerate(data):
        if i == 0:
            continue
        if row and row[0].strip().upper() == symbol:
            found_idx = i
            break

    if found_idx != -1:
        # 기존 기록 수정
        row_num = found_idx + 1
        ws.update_cell(row_num, 2, float(shares))
        ws.update_cell(row_num, 3, float(purchase_price))
        ws.update_cell(row_num, 4, now_str)
    else:
        # 신규 등록
        ws.append_row([symbol, float(shares), float(purchase_price), now_str])


def remove_from_portfolio(symbol):
    """포트폴리오에서 아이템을 제거합니다."""
    if not sh:
        return
    ws = sh.worksheet("portfolio")
    symbol = symbol.strip().upper()
    data = ws.get_all_values()

    for i, row in enumerate(data):
        if i == 0:
            continue
        if row and row[0].strip().upper() == symbol:
            ws.delete_rows(i + 1)
            break
