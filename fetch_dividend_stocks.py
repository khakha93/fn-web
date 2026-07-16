import requests
import pandas as pd
import yfinance as yf
import os
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
import datetime
from io import StringIO
import re
from lxml import html as lxml_html
import sheets_helper

LAST_SUCCESS_GROUPS = []
LAST_FAILED_GROUPS = []
ETF_GROUPS = ["SCHD", "VIG", "DGRO"]
HOLDINGS_COLUMNS = ["symbol", "companyName", "lastDividend", "stock_type", "group", "weight", "marketCap", "dividendYield"]
INVALID_SYMBOLS = {
    "NAN",
    "SHOW",
    "TOTAL",
    "HOLDINGS",
    "NAME",
    "WEIGHT",
    "SHARES",
    "SECURITY",
    "SYMBOL",
    "TICKER",
    "FUND",
    "NET",
    "RATIO",
    "INDEX",
    "NAV",
    "RATE",
    "STYLE",
    "CUSIP",
    "SEC",
    "AS",
}


def _empty_holdings_df():
    return pd.DataFrame(columns=HOLDINGS_COLUMNS)


def _normalize_holdings_df(df):
    if df is None or df.empty:
        return _empty_holdings_df()

    out = df.copy()
    for col in HOLDINGS_COLUMNS:
        if col not in out.columns:
            out[col] = pd.NA

    out = out[HOLDINGS_COLUMNS]
    out["symbol"] = out["symbol"].astype(str).str.strip().str.upper()
    out["companyName"] = out["companyName"].fillna("").astype(str).str.strip()
    out["lastDividend"] = pd.to_numeric(out["lastDividend"], errors="coerce")
    out["stock_type"] = out["stock_type"].fillna("STOCK").astype(str).str.strip().str.upper()
    out["group"] = out["group"].fillna("").astype(str).str.strip()
    out["weight"] = pd.to_numeric(out["weight"], errors="coerce")
    out["marketCap"] = pd.to_numeric(out["marketCap"], errors="coerce")
    out["dividendYield"] = pd.to_numeric(out["dividendYield"], errors="coerce")
    out = out[out["symbol"] != ""]
    out = out[out["group"] != ""]
    out = out[~out["symbol"].isin(INVALID_SYMBOLS)]
    return out


def _is_valid_symbol(sym):
    if not sym:
        return False
    s = str(sym).strip().upper()
    if s in INVALID_SYMBOLS:
        return False
    return bool(re.fullmatch(r"[A-Z]{1,5}(?:-[A-Z]{1,2})?", s))


def _is_quality_holdings(df):
    if df is None or df.empty:
        return False

    weight_count = df["weight"].notna().sum() if "weight" in df.columns else 0
    # ETF 구성종목은 최소한 일부 비중 값이 있어야 유효 테이블로 간주
    return len(df) >= 10 and weight_count >= 5





def fetch_sp500_tickers(headers):
    print("Fetching S&P 500 tickers from Wikipedia...")
    try:
        url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
        response = requests.get(url, headers=headers, timeout=20)
        response.raise_for_status()
        tables = pd.read_html(StringIO(response.text))
        df = tables[0]
        ticker_map = {}
        for _, row in df.iterrows():
            ticker = str(row.get("Symbol", "")).replace(".", "-").strip().upper()
            name = str(row.get("Security", ticker)).strip()
            if ticker:
                ticker_map[ticker] = name
        print(f"Found {len(ticker_map)} S&P 500 tickers.")
        return ticker_map
    except Exception as e:
        print(f"Error fetching S&P 500: {e}")
        return {}


def fetch_nasdaq100_tickers(headers):
    print("Fetching NASDAQ 100 tickers from Wikipedia...")
    try:
        url = "https://en.wikipedia.org/wiki/List_of_NASDAQ-100_companies"
        response = requests.get(url, headers=headers, timeout=20)
        response.raise_for_status()
        tables = pd.read_html(StringIO(response.text))
        for t in tables:
            ticker_col = None
            if "Ticker" in t.columns:
                ticker_col = "Ticker"
            elif "Symbol" in t.columns:
                ticker_col = "Symbol"
            
            company_col = None
            if "Company" in t.columns:
                company_col = "Company"
            elif "Security" in t.columns:
                company_col = "Security"

            if ticker_col:
                ticker_map = {}
                for _, row in t.iterrows():
                    ticker = str(row[ticker_col]).replace(".", "-").strip().upper()
                    name = str(row[company_col]) if company_col else ticker
                    if ticker:
                        ticker_map[ticker] = name
                print(f"Found {len(ticker_map)} NASDAQ 100 tickers.")
                return ticker_map
    except Exception as e:
        print(f"Error fetching NASDAQ 100: {e}")
    return {}


def _chunk_list(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i:i + n]


def fetch_market_caps_in_parallel(tickers):
    """
    고유 티커 목록 전체에 대하여 yfinance의 fast_info 속성을 병렬로 초고속 조회하여
    {ticker: market_cap} 딕셔너리를 반환합니다.
    """
    tickers = sorted(list(set(tickers)))
    market_caps = {}
    if not tickers:
        return market_caps

    print(f"Fetching market caps in parallel for {len(tickers)} tickers...")
    
    def fetch_single_cap(ticker):
        try:
            t = yf.Ticker(ticker)
            # fast_info는 가볍고 빠른 호출용 속성
            cap = getattr(t.fast_info, "market_cap", None)
            if cap is not None:
                return ticker, float(cap)
            
            # fallback
            info_cap = t.info.get("marketCap", None)
            if info_cap is not None:
                return ticker, float(info_cap)
        except Exception:
            pass
        return ticker, None

    with ThreadPoolExecutor(max_workers=35) as executor:
        futures = {executor.submit(fetch_single_cap, ticker): ticker for ticker in tickers}
        for fut in as_completed(futures):
            ticker, cap = fut.result()
            if cap is not None:
                market_caps[ticker] = cap

    print(f"Market cap fetch complete: found {len(market_caps)} records.")
    return market_caps


def fetch_active_dividends_in_batch(tickers, company_name_dict):
    """
    Given a list of tickers, downloads their dividend data in batch chunks,
    filters out inactive ones (no dividend in the last 1 year),
    and returns a dictionary of {ticker: last_dividend_value}.
    """
    tickers = sorted(list(set(tickers)))
    active_divs = {}
    if not tickers:
        return active_divs

    print(f"Downloading batch dividends for {len(tickers)} tickers...")
    import datetime
    now = datetime.datetime.now()

    chunks = list(_chunk_list(tickers, 150))
    for chunk_idx, chunk in enumerate(chunks):
        print(f"Downloading chunk {chunk_idx + 1}/{len(chunks)} ({len(chunk)} tickers)...")
        try:
            df_batch = yf.download(chunk, period="1y", actions=True, group_by="ticker", progress=False)

            for ticker in chunk:
                try:
                    if isinstance(df_batch.columns, pd.MultiIndex):
                        if ticker not in df_batch.columns.levels[0]:
                            continue
                        ticker_df = df_batch[ticker]
                    else:
                        ticker_df = df_batch

                    div_history = ticker_df[ticker_df["Dividends"] > 0]
                    if div_history.empty:
                        continue

                    last_date = div_history.index[-1]
                    last_div = div_history["Dividends"].iloc[-1]

                    # Filter: must be within recent 1 year (365 days)
                    one_year_ago = now - datetime.timedelta(days=365)
                    if last_date.tzinfo is not None:
                        one_year_ago = one_year_ago.astimezone(last_date.tzinfo)

                    if last_date < one_year_ago:
                        continue

                    # TTM 배당률(수익률) 연산
                    current_price = 0.0
                    if "Close" in ticker_df.columns:
                        filled_close = ticker_df["Close"].ffill()
                        if not filled_close.empty:
                            current_price = float(filled_close.iloc[-1])

                    annual_dividend = float(ticker_df["Dividends"].sum())
                    div_yield = 0.0
                    if current_price > 0:
                        div_yield = (annual_dividend / current_price) * 100

                    active_divs[ticker] = {
                        "last_div": float(last_div),
                        "yield": float(div_yield)
                    }
                except Exception:
                    pass
        except Exception as e:
            print(f"Failed to download batch {chunk_idx + 1}: {e}")

    print(f"Active dividend status check complete: found {len(active_divs)} dividend-paying stocks.")
    return active_divs


def build_index_group_rows(ticker_map, group_name, active_dividends_map, market_caps_map):
    if not ticker_map:
        return _empty_holdings_df()

    rows = []
    for ticker, name in ticker_map.items():
        if ticker in active_dividends_map:
            rows.append(
                {
                    "symbol": ticker,
                    "companyName": name,
                    "lastDividend": active_dividends_map[ticker]["last_div"],
                    "stock_type": "STOCK",
                    "group": group_name,
                    "weight": pd.NA,
                    "marketCap": market_caps_map.get(ticker, pd.NA),
                    "dividendYield": active_dividends_map[ticker]["yield"],
                }
            )
    return _normalize_holdings_df(pd.DataFrame(rows))


def normalize_weight(value, is_ratio=None):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return pd.NA

    text = str(value).strip().replace(",", "")
    if text == "":
        return pd.NA

    is_percent = text.endswith("%")
    text = text.replace("%", "")

    try:
        num = float(text)
    except Exception:
        return pd.NA

    if is_percent:
        return round(num, 6)

    if is_ratio is True:
        num = num * 100
    elif is_ratio is False:
        pass
    else:
        if 0 < num <= 1:
            num = num * 100

    return round(num, 6)


def _pick_column(columns, candidates):
    lowered = {str(c).lower().strip(): c for c in columns}
    for cand in candidates:
        for key, original in lowered.items():
            if cand in key:
                return original
    return None


def _extract_symbol(text):
    if text is None:
        return ""
    s = str(text).strip().upper().replace(".", "-")
    matches = re.findall(r"\b[A-Z]{1,5}(?:-[A-Z]{1,2})?\b", s)
    for m in matches:
        if _is_valid_symbol(m):
            return m
    return ""


def _clean_text(text):
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _extract_display_info(html_text):
    m = re.search(r"Displaying\s+(\d+)\s*-\s*(\d+)\s+of\s+(\d+)", html_text, flags=re.I)
    if not m:
        return 1, 100, 100
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def _extract_asof_date(html_text):
    m = re.search(r"As of\s+([0-9]{2}/[0-9]{2}/[0-9]{2})", html_text, flags=re.I)
    return m.group(1) if m else ""


def _find_header_idx(headers, candidates):
    lowered = [str(h).lower().strip() for h in headers]
    for i, header in enumerate(lowered):
        for cand in candidates:
            if cand in header:
                return i
    return None


def _parse_schd_table_rows(table_el):
    rows = []

    header_cells = table_el.xpath(".//thead//th")
    if not header_cells:
        header_cells = table_el.xpath(".//tr[1]//th|.//tr[1]//td")

    headers = [_clean_text(c.text_content()) for c in header_cells]
    if not headers:
        return rows

    i_symbol = _find_header_idx(headers, ["symbol", "ticker"])
    i_name = _find_header_idx(headers, ["fund name", "security", "name", "holding"])
    i_weight = _find_header_idx(headers, ["% of assets", "% of net assets", "weight", "portfolio", "%"])

    body_rows = table_el.xpath(".//tbody/tr")
    if not body_rows:
        all_tr = table_el.xpath(".//tr")
        body_rows = all_tr[1:] if len(all_tr) > 1 else []

    for tr in body_rows:
        tds = tr.xpath("./td")
        if not tds:
            continue

        cells = [_clean_text(td.text_content()) for td in tds]
        if not cells:
            continue

        symbol = ""
        if i_symbol is not None and i_symbol < len(cells):
            symbol = str(cells[i_symbol]).strip().upper().replace(".", "-")

        name = ""
        if i_name is not None and i_name < len(cells):
            name = str(cells[i_name]).strip()

        if not symbol and name:
            symbol = _extract_symbol(name)

        if not _is_valid_symbol(symbol):
            continue
        if symbol == "USD":
            continue

        weight = pd.NA
        if i_weight is not None and i_weight < len(cells):
            weight = normalize_weight(cells[i_weight], is_ratio=False)

        rows.append(
            {
                "symbol": symbol,
                "companyName": name or symbol,
                "lastDividend": pd.NA,
                "stock_type": "STOCK",
                "group": "SCHD",
                "weight": weight,
            }
        )

    return rows


def _fetch_schd_allholdings_official():
    base_url = "https://www.schwabassetmanagement.com/allholdings/SCHD"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    try:
        resp0 = requests.get(f"{base_url}?page=0", headers=headers, timeout=25)
        resp0.raise_for_status()
    except Exception as e:
        print(f"SCHD official allholdings failed on first page: {e}")
        return _empty_holdings_df()

    html0 = resp0.text
    start_i, end_i, total_i = _extract_display_info(html0)
    page_size = max(1, end_i - start_i + 1)
    total_pages = int((total_i + page_size - 1) / page_size)
    asof = _extract_asof_date(html0)
    if asof:
        print(f"SCHD official allholdings as-of: {asof}")

    all_rows = []
    for page in range(total_pages):
        try:
            if page == 0:
                html_text = html0
            else:
                resp = requests.get(f"{base_url}?page={page}", headers=headers, timeout=25)
                resp.raise_for_status()
                html_text = resp.text

            tree = lxml_html.fromstring(html_text)
            tables = tree.xpath("//div[contains(concat(' ', normalize-space(@class), ' '), ' view-content ')]//table")
            if not tables:
                print(f"SCHD page={page}: holdings table not found.")
                continue

            page_rows = _parse_schd_table_rows(tables[0])
            all_rows.extend(page_rows)
            print(f"SCHD page={page}: parsed {len(page_rows)} rows.")
        except Exception as e:
            print(f"SCHD page={page} parse failed: {e}")

    if not all_rows:
        return _empty_holdings_df()

    out = _normalize_holdings_df(pd.DataFrame(all_rows))
    out = out.drop_duplicates(subset=["group", "symbol"], keep="first")
    if not _is_quality_holdings(out):
        return _empty_holdings_df()
    return out


def _extract_initial_fund_data(html_text):
    m = re.search(r"window\.__INITIAL_FUND_DATA__\s*=\s*(\{.*?\});", html_text, flags=re.S)
    if not m:
        return {}

    try:
        return json.loads(m.group(1))
    except Exception:
        return {}


def _parse_vanguard_holdings_payload(payload, group_name):
    if not isinstance(payload, dict) or not payload:
        return _empty_holdings_df(), ""

    latest_effective_date = str(payload.get("latestEffectiveDate") or "").strip()

    date_key = ""
    if latest_effective_date and latest_effective_date in payload:
        date_key = latest_effective_date
    else:
        for k in payload.keys():
            if k != "latestEffectiveDate":
                date_key = k
                break

    if not date_key:
        return _empty_holdings_df(), latest_effective_date

    equity_rows = payload.get(date_key, {}).get("equity", [])
    if not equity_rows:
        return _empty_holdings_df(), latest_effective_date or date_key

    rows = []
    for row in equity_rows:
        symbol = _extract_symbol(row.get("ticker", ""))
        if not _is_valid_symbol(symbol):
            continue

        company_name = str(row.get("holdingName") or symbol).strip()
        weight = normalize_weight(row.get("percentOfFunds"), is_ratio=False)

        rows.append(
            {
                "symbol": symbol,
                "companyName": company_name,
                "lastDividend": pd.NA,
                "stock_type": "STOCK",
                "group": group_name,
                "weight": weight,
            }
        )

    if not rows:
        return _empty_holdings_df(), latest_effective_date or date_key

    out = _normalize_holdings_df(pd.DataFrame(rows))
    out = out.drop_duplicates(subset=["group", "symbol"], keep="first")
    return out, (latest_effective_date or date_key)


def _fetch_vig_official_holdings():
    product_url = "https://advisors.vanguard.com/investments/products/vig/vanguard-dividend-appreciation-etf"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Accept": "application/json, text/plain, */*",
        "Referer": product_url,
    }

    # Stable default for VIG; if it fails, we still try parsing current product page bootstrap data.
    port_id = "0920"

    try:
        page_resp = requests.get(product_url, headers=headers, timeout=25)
        page_resp.raise_for_status()
        initial_data = _extract_initial_fund_data(page_resp.text)
        parsed_port_id = str(initial_data.get("portId") or "").strip()
        if parsed_port_id:
            port_id = parsed_port_id
    except Exception as e:
        print(f"VIG: product page bootstrap parse failed, using default port id {port_id}. ({e})")

    api_url = f"https://advisors.vanguard.com/investments/products/api/funds/{port_id}/holdings/latest"
    try:
        resp = requests.get(api_url, headers=headers, timeout=25)
        resp.raise_for_status()
        payload = resp.json()
    except Exception as e:
        print(f"VIG official API fetch failed: {e}")
        return _empty_holdings_df()

    out, asof = _parse_vanguard_holdings_payload(payload, "VIG")
    if out.empty:
        print("VIG official API parse failed: no valid equity rows.")
        return _empty_holdings_df()

    print(f"VIG official API as-of: {asof} rows={len(out)} weight_notna={int(out['weight'].notna().sum())}")
    if not _is_quality_holdings(out):
        print("VIG official API failed quality gate.")
        return _empty_holdings_df()

    return out


def _rows_from_holdings_table(df, group_name):
    if df is None or df.empty:
        return []

    symbol_col = _pick_column(df.columns, ["ticker", "symbol", "holding ticker", "security ticker"])
    name_col = _pick_column(df.columns, ["name", "security", "holding"])
    weight_col = _pick_column(df.columns, ["weight", "% of net assets", "% assets", "portfolio", "holding %", "market value"])

    if symbol_col is None and name_col is None:
        return []

    rows = []
    for _, row in df.iterrows():
        raw_symbol = row[symbol_col] if symbol_col else ""
        symbol = _extract_symbol(raw_symbol)
        if not symbol and name_col:
            symbol = _extract_symbol(row[name_col])
        if not _is_valid_symbol(symbol):
            continue

        company_name = ""
        if name_col:
            company_name = str(row[name_col]).strip()
        if not company_name:
            company_name = symbol

        weight = normalize_weight(row[weight_col], is_ratio=False) if weight_col else pd.NA

        rows.append(
            {
                "symbol": symbol,
                "companyName": company_name,
                "lastDividend": pd.NA,
                "stock_type": "STOCK",
                "group": group_name,
                "weight": weight,
            }
        )

    return rows


def _parse_csv_text(csv_text):
    if not csv_text:
        return pd.DataFrame()

    candidate_texts = [csv_text]

    stripped = csv_text.strip()
    if (stripped.startswith('"') and stripped.endswith('"')) or "\\\"" in stripped:
        unescaped = stripped
        if unescaped.startswith('"') and unescaped.endswith('"'):
            unescaped = unescaped[1:-1]
        unescaped = unescaped.replace('\\r\\n', '\n').replace('\\n', '\n').replace('\\t', '\t')
        unescaped = unescaped.replace('\\\"', '"')
        candidate_texts.append(unescaped)

    for text in candidate_texts:
        try:
            df = pd.read_csv(StringIO(text), low_memory=False)
            col_text = " ".join([str(c).lower() for c in df.columns])
            if any(k in col_text for k in ["ticker", "symbol", "holding", "security", "name", "weight"]):
                return df
        except Exception:
            pass

        lines = text.splitlines()
        for i, line in enumerate(lines):
            lower = line.lower()
            if "ticker" in lower or "symbol" in lower:
                sliced = "\n".join(lines[i:])
                try:
                    df = pd.read_csv(StringIO(sliced), low_memory=False)
                    col_text = " ".join([str(c).lower() for c in df.columns])
                    if any(k in col_text for k in ["ticker", "symbol", "holding", "security", "name", "weight"]):
                        return df
                except Exception:
                    continue

    return pd.DataFrame()


def _fetch_dgro_official_holdings_varnish():
    api_url = (
        "https://www.blackrock.com/varnish-api/blk-one01-product-data/product-data/api/v1/get-fund-document"
        "?appType=PRODUCT_PAGE"
        "&appSubType=ISHARES"
        "&targetSite=us-ishares"
        "&locale=en_US"
        "&portfolioId=264623"
        "&userType=individual"
        "&component=holdings"
    )

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": "https://www.ishares.com/",
    }

    try:
        resp = requests.get(api_url, headers=headers, timeout=25)
        resp.raise_for_status()
        raw_text = resp.text
    except Exception as e:
        print(f"DGRO API fetch failed: {e}")
        return _empty_holdings_df()

    raw_lines = raw_text.splitlines()
    start_idx = -1
    table_lines = []

    for idx, line in enumerate(raw_lines):
        if "Ticker,Name,Sector" in line:
            start_idx = idx

        if start_idx != -1 and idx >= start_idx:
            if not line.strip():
                break
            table_lines.append(line)

    if not table_lines:
        print("DGRO Varnish parse failed: 'Ticker,Name,Sector' header not found.")
        return _empty_holdings_df()

    try:
        df = pd.read_csv(StringIO("\n".join(table_lines)))
    except Exception as e:
        print(f"DGRO CSV read failed: {e}")
        return _empty_holdings_df()

    rows = _rows_from_holdings_table(df, "DGRO")
    if not rows:
        return _empty_holdings_df()

    out = _normalize_holdings_df(pd.DataFrame(rows))
    out = out.drop_duplicates(subset=["group", "symbol"], keep="first")
    return out


def _fetch_official_holdings(etf):
    if etf == "SCHD":
        schd_df = _fetch_schd_allholdings_official()
        if not schd_df.empty:
            return schd_df
    if etf == "VIG":
        vig_df = _fetch_vig_official_holdings()
        if not vig_df.empty:
            return vig_df
    if etf == "DGRO":
        dgro_df = _fetch_dgro_official_holdings_varnish()
        if not dgro_df.empty:
            return dgro_df

    official_sources = {
        "SCHD": [
            "https://www.schwabassetmanagement.com/products/schd",
        ],
        "VIG": [
            "https://advisors.vanguard.com/investments/products/vig/vanguard-dividend-appreciation-etf",
        ],
        "DGRO": [
            "https://www.ishares.com/us/products/264623/ishares-core-dividend-growth-etf/1467271812596.ajax?fileType=csv",
            "https://www.ishares.com/us/products/264623/ishares-core-dividend-growth-etf",
        ],
    }

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    rows = []

    for url in official_sources.get(etf, []):
        try:
            resp = requests.get(url, headers=headers, timeout=20)
            resp.raise_for_status()
            text = resp.text

            if "filetype=csv" in url.lower() or url.lower().endswith(".csv"):
                df = _parse_csv_text(text)
                rows.extend(_rows_from_holdings_table(df, etf))
            else:
                tables = pd.read_html(StringIO(text), flavor="lxml")
                for table in tables:
                    rows.extend(_rows_from_holdings_table(table, etf))

            if rows:
                deduped = {(r["symbol"], r["group"]): r for r in rows}
                out = _normalize_holdings_df(pd.DataFrame(deduped.values()))
                if _is_quality_holdings(out):
                    return out
        except Exception as e:
            print(f"{etf} official source parse failed: {url} ({e})")

    return _empty_holdings_df()


def fetch_etf_constituents(etf):
    print(f"Fetching ETF holdings for {etf} (official source)...")

    df = _fetch_official_holdings(etf)
    if not df.empty:
        return df, "official"

    return _empty_holdings_df(), "failed"


def fetch_dividend_etf_constituents(df_existing, target_etfs=None):
    if target_etfs is None:
        target_etfs = ETF_GROUPS
    frames = []
    success_groups = []
    failed_groups = []

    for etf in target_etfs:
        df, source = fetch_etf_constituents(etf)
        if df.empty:
            # 1단계 웹 조회 실패 시 -> 구글 시트 기존 데이터에서 백업 추출
            cached_df = pd.DataFrame()
            if df_existing is not None and not df_existing.empty:
                cached_df = df_existing[df_existing["group"] == etf].copy()

            if cached_df.empty:
                failed_groups.append(etf)
                print(f"{etf}: failed to fetch holdings from official source and no sheet backup available.")
                continue
            success_groups.append(f"{etf}(sheet_backup)")
            frames.append(cached_df)
            continue

        success_groups.append(f"{etf}({source})")
        frames.append(_normalize_holdings_df(df))

    if not frames:
        return _empty_holdings_df(), success_groups, failed_groups

    out = _normalize_holdings_df(pd.concat(frames, ignore_index=True))
    return out, success_groups, failed_groups


def run_update(target_groups=None):
    """배당 종목 정보를 업데이트하여 구글 시트에 저장합니다. target_groups가 있으면 부분 업데이트합니다."""
    global LAST_SUCCESS_GROUPS, LAST_FAILED_GROUPS

    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

    # 1. 구글 시트에서 기존 데이터 로딩
    print("Loading existing stocks from Google Sheets...")
    try:
        df_existing = sheets_helper.get_stocks()
    except Exception as e:
        print(f"Failed to load existing stocks from Google Sheets: {e}")
        df_existing = pd.DataFrame()

    ALL_GROUPS = ["S&P", "Nasdaq", "SCHD", "VIG", "DGRO"]
    if not target_groups:
        target_groups = ALL_GROUPS
    else:
        # 대소문자 매핑 및 검증
        target_groups = [g for g in ALL_GROUPS if g.lower() in [tg.lower() for tg in target_groups]]

    if not target_groups:
        print("No valid target groups specified. Defaulting to all.")
        target_groups = ALL_GROUPS

    print(f"Target groups for update: {target_groups}")

    # 2. 각 그룹별 로우 수집 (티커 & 회사명 맵 확보)
    sp500_map = {}
    nasdaq_map = {}
    df_etf_raw = pd.DataFrame()
    etf_success = []
    etf_failed = []
    index_success_groups = []

    if "S&P" in target_groups:
        sp500_map = fetch_sp500_tickers(headers)
        if not sp500_map and not df_existing.empty:
            print("S&P 500 fetch from Wikipedia failed. Using existing S&P 500 tickers from sheet as backup...")
            sp_existing = df_existing[df_existing["group"] == "S&P"]
            sp500_map = {row["symbol"]: row["companyName"] for _, row in sp_existing.iterrows()}
            if sp500_map:
                index_success_groups.append("S&P(sheet_backup)")
        elif sp500_map:
            index_success_groups.append("S&P(official)")

    if "Nasdaq" in target_groups:
        nasdaq_map = fetch_nasdaq100_tickers(headers)
        if not nasdaq_map and not df_existing.empty:
            print("NASDAQ 100 fetch from Wikipedia failed. Using existing NASDAQ 100 tickers from sheet as backup...")
            nas_existing = df_existing[df_existing["group"] == "Nasdaq"]
            nasdaq_map = {row["symbol"]: row["companyName"] for _, row in nas_existing.iterrows()}
            if nasdaq_map:
                index_success_groups.append("Nasdaq(sheet_backup)")
        elif nasdaq_map:
            index_success_groups.append("Nasdaq(official)")
    
    target_etfs = [etf for etf in ETF_GROUPS if etf in target_groups]
    if target_etfs:
        df_etf_raw, etf_success, etf_failed = fetch_dividend_etf_constituents(df_existing, target_etfs)

    # 3. 신규 수집하려는 티커 목록 취합
    new_tickers = set()
    if sp500_map:
        new_tickers.update(sp500_map.keys())
    if nasdaq_map:
        new_tickers.update(nasdaq_map.keys())
    if not df_etf_raw.empty:
        new_tickers.update(df_etf_raw["symbol"].tolist())

    # 4. 회사명 딕셔너리 통합
    combined_name_map = {**sp500_map, **nasdaq_map}
    if not df_etf_raw.empty:
        for _, row in df_etf_raw.iterrows():
            sym = row["symbol"]
            if sym not in combined_name_map:
                combined_name_map[sym] = row["companyName"]

    # 5. 수집할 티커 세트에 대해 yfinance 배치 배당 조회 실행 및 시가총액 병렬 조회
    active_dividends_map = {}
    market_caps_map = {}
    if new_tickers:
        active_dividends_map = fetch_active_dividends_in_batch(new_tickers, combined_name_map)
        market_caps_map = fetch_market_caps_in_parallel(new_tickers)

    # 6. 각 그룹별 최종 데이터프레임 빌드
    df_sp500 = pd.DataFrame()
    df_nasdaq = pd.DataFrame()
    df_etf = pd.DataFrame()

    if "S&P" in target_groups and sp500_map:
        df_sp500 = build_index_group_rows(sp500_map, "S&P", active_dividends_map, market_caps_map)
    if "Nasdaq" in target_groups and nasdaq_map:
        df_nasdaq = build_index_group_rows(nasdaq_map, "Nasdaq", active_dividends_map, market_caps_map)
    if not df_etf_raw.empty:
        # ETF 구성 종목 중 active 배당을 지급하는 종목만 남김
        df_etf_filtered = df_etf_raw[df_etf_raw["symbol"].isin(active_dividends_map)].copy()
        if not df_etf_filtered.empty:
            df_etf_filtered["lastDividend"] = df_etf_filtered["symbol"].map(lambda s: active_dividends_map[s]["last_div"] if s in active_dividends_map else pd.NA)
            df_etf_filtered["marketCap"] = df_etf_filtered["symbol"].map(market_caps_map)
            df_etf_filtered["dividendYield"] = df_etf_filtered["symbol"].map(lambda s: active_dividends_map[s]["yield"] if s in active_dividends_map else pd.NA)
            df_etf = _normalize_holdings_df(df_etf_filtered)

    # 7. 신규 업데이트된 결과물 취합
    new_frames = []
    if not df_sp500.empty:
        new_frames.append(df_sp500)
    if not df_nasdaq.empty:
        new_frames.append(df_nasdaq)
    if not df_etf.empty:
        new_frames.append(df_etf)

    # 8. 기존 구글 시트 데이터 중 업데이트하지 '않은' 그룹 데이터 보존 (Read-Modify-Write)
    success_groups = []
    success_groups.extend(index_success_groups)
    success_groups.extend(etf_success)

    failed_groups = list(etf_failed)
    if "S&P" in target_groups and df_sp500.empty:
        failed_groups.append("S&P")
    if "Nasdaq" in target_groups and df_nasdaq.empty:
        failed_groups.append("Nasdaq")

    df_preserved = pd.DataFrame()
    # success_groups에 소스 구분자(예: '(official)', '(cache)')가 포함되어 있으므로 실제 그룹 이름으로 변환
    clean_success_groups = []
    for g in success_groups:
        if "(" in g:
            clean_success_groups.append(g.split("(")[0])
        else:
            clean_success_groups.append(g)

    if not df_existing.empty:
        # 성공적으로 신규 수집된 그룹을 기존 데이터에서 제외하고 보존
        df_preserved = df_existing[~df_existing["group"].isin(clean_success_groups)].copy()

    # 9. 보존된 기존 그룹 데이터 + 신규 업데이트 데이터를 병합
    combined_list = []
    if not df_preserved.empty:
        combined_list.append(df_preserved)
    combined_list.extend(new_frames)

    if not combined_list:
        LAST_SUCCESS_GROUPS = []
        LAST_FAILED_GROUPS = target_groups
        raise RuntimeError("수집 및 병합할 수 있는 데이터가 없습니다.")

    df_combined = pd.concat(combined_list, ignore_index=True)

    # 10. 동기화 시각 업데이트 (신규 수집된 그룹 행에만 업데이트, 보존된 그룹 행은 기존 시각 유지)
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 임시로 updated_at 백업
    updated_at_series = df_combined["updated_at"] if "updated_at" in df_combined.columns else pd.Series([""] * len(df_combined))

    # 표준 필드 규격화 (updated_at 컬럼 유실됨)
    df_combined = _normalize_holdings_df(df_combined)

    # updated_at 다시 복원
    df_combined["updated_at"] = updated_at_series.values

    # 신규 성공 그룹의 updated_at을 최신 시간으로 업데이트
    df_combined.loc[df_combined["group"].isin(clean_success_groups), "updated_at"] = now_str

    # 비어있는 updated_at이 있으면 현재 시간으로 채움
    df_combined.loc[df_combined["updated_at"].isna() | (df_combined["updated_at"] == ""), "updated_at"] = now_str

    # 중복 제거 (그룹, 티커 기준)
    df_combined = df_combined.drop_duplicates(subset=["group", "symbol"], keep="first")

    if df_combined.empty:
        LAST_SUCCESS_GROUPS = []
        LAST_FAILED_GROUPS = target_groups
        raise RuntimeError("정제 후 동기화할 데이터가 존재하지 않습니다.")

    # 11. 구글 시트 최종 일괄 동기화
    print("Syncing data to Google Sheets...")
    sheets_helper.save_stocks(df_combined)
    print("Google Sheets sync successful!")

    LAST_SUCCESS_GROUPS = success_groups
    LAST_FAILED_GROUPS = failed_groups

    return df_combined


def main():
    run_update()


if __name__ == "__main__":
    main()
