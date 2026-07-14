import requests
import pandas as pd
import yfinance as yf
import os
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
HOLDINGS_COLUMNS = ["symbol", "companyName", "lastDividend", "stock_type", "group", "weight"]
CACHE_FILE = "scratch/etf_holdings_cache.csv"
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


def _load_cached_etf_holdings(etf):
    if not os.path.exists(CACHE_FILE):
        return _empty_holdings_df()

    try:
        cache_df = pd.read_csv(CACHE_FILE)
        cache_df = _normalize_holdings_df(cache_df)
        out = cache_df[cache_df["group"] == etf].copy()
        if _is_quality_holdings(out):
            print(f"{etf}: loaded ETF holdings from cache ({len(out)} rows).")
            return out
    except Exception as e:
        print(f"{etf} cache load failed: {e}")

    return _empty_holdings_df()


def _save_cached_etf_holdings(df_etf):
    if df_etf is None or df_etf.empty:
        return

    new_df = _normalize_holdings_df(df_etf)
    if new_df.empty:
        return

    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)

    if os.path.exists(CACHE_FILE):
        try:
            old_df = _normalize_holdings_df(pd.read_csv(CACHE_FILE))
        except Exception:
            old_df = _empty_holdings_df()
    else:
        old_df = _empty_holdings_df()

    updated = pd.concat([old_df, new_df], ignore_index=True)
    updated = updated.drop_duplicates(subset=["group", "symbol"], keep="last")
    updated.to_csv(CACHE_FILE, index=False)


def fetch_sp500_tickers(headers):
    print("Fetching S&P 500 tickers from Wikipedia...")
    try:
        url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
        response = requests.get(url, headers=headers, timeout=20)
        response.raise_for_status()
        tables = pd.read_html(StringIO(response.text))
        df = tables[0]
        tickers = [str(t).replace(".", "-").strip().upper() for t in df["Symbol"].tolist()]
        tickers = [t for t in tickers if t]
        print(f"Found {len(tickers)} S&P 500 tickers.")
        return tickers
    except Exception as e:
        print(f"Error fetching S&P 500: {e}")
        return []


def fetch_nasdaq100_tickers(headers):
    print("Fetching NASDAQ 100 tickers from Wikipedia...")
    try:
        url = "https://en.wikipedia.org/wiki/Nasdaq-100"
        response = requests.get(url, headers=headers, timeout=20)
        response.raise_for_status()
        tables = pd.read_html(StringIO(response.text))
        for t in tables:
            if "Ticker" in t.columns:
                tickers = [str(symbol).replace(".", "-").strip().upper() for symbol in t["Ticker"].tolist()]
                tickers = [x for x in tickers if x]
                print(f"Found {len(tickers)} NASDAQ 100 tickers.")
                return tickers
            if "Symbol" in t.columns:
                tickers = [str(symbol).replace(".", "-").strip().upper() for symbol in t["Symbol"].tolist()]
                tickers = [x for x in tickers if x]
                print(f"Found {len(tickers)} NASDAQ 100 tickers.")
                return tickers
    except Exception as e:
        print(f"Error fetching NASDAQ 100: {e}")
    return []


def check_dividend_status(ticker):
    try:
        t = yf.Ticker(ticker)
        divs = t.dividends
        if len(divs) > 0:
            last_div = divs.iloc[-1]
            name = ticker
            try:
                name = t.info.get("longName", ticker)
            except Exception:
                pass
            return {"symbol": ticker, "companyName": name, "lastDividend": float(last_div)}
    except Exception:
        pass
    return None


def build_index_group_rows(tickers, group_name, max_workers=15):
    if not tickers:
        return _empty_holdings_df()

    rows = []
    print(f"Checking dividend status for {group_name} tickers in parallel...")
    start_time = time.time()

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(check_dividend_status, ticker): ticker for ticker in sorted(set(tickers))}
        for future in as_completed(futures):
            res = future.result()
            if not res:
                continue
            rows.append(
                {
                    "symbol": str(res["symbol"]).strip().upper(),
                    "companyName": str(res.get("companyName") or res["symbol"]).strip(),
                    "lastDividend": pd.to_numeric(res.get("lastDividend"), errors="coerce"),
                    "stock_type": "STOCK",
                    "group": group_name,
                    "weight": pd.NA,
                }
            )

    elapsed = time.time() - start_time
    print(f"{group_name}: found {len(rows)} dividend-paying stocks in {elapsed:.2f} seconds.")
    return _normalize_holdings_df(pd.DataFrame(rows))


def normalize_weight(value):
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

    if not is_percent and 0 < num <= 1:
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
            weight = normalize_weight(cells[i_weight])

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

        weight = normalize_weight(row[weight_col]) if weight_col else pd.NA

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


def _fetch_official_holdings(etf):
    if etf == "SCHD":
        schd_df = _fetch_schd_allholdings_official()
        if not schd_df.empty:
            return schd_df

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


def _fetch_holdings_from_slickcharts(etf):
    url = f"https://www.slickcharts.com/etf/{etf.upper()}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    try:
        resp = requests.get(url, headers=headers, timeout=20)
        resp.raise_for_status()
        tables = pd.read_html(StringIO(resp.text), flavor="lxml")

        rows = []
        for table in tables:
            rows.extend(_rows_from_holdings_table(table, etf))

        if not rows:
            return _empty_holdings_df()

        deduped = {(r["symbol"], r["group"]): r for r in rows}
        out = _normalize_holdings_df(pd.DataFrame(deduped.values()))
        if not _is_quality_holdings(out):
            return _empty_holdings_df()
        return out
    except Exception as e:
        print(f"{etf} slickcharts fallback failed: {e}")
        return _empty_holdings_df()


def _fetch_holdings_from_yfinance(etf):
    try:
        t = yf.Ticker(etf)
        fund_data = getattr(t, "funds_data", None)
        top_holdings = getattr(fund_data, "top_holdings", None)

        if top_holdings is None:
            getter = getattr(t, "get_funds_data", None)
            if callable(getter):
                fd = getter()
                top_holdings = getattr(fd, "top_holdings", None)

        if top_holdings is None or getattr(top_holdings, "empty", True):
            return _empty_holdings_df()

        df = top_holdings.copy()
        symbol_col = _pick_column(df.columns, ["symbol", "ticker", "holding"]) or df.columns[0]
        weight_col = _pick_column(df.columns, ["holding percent", "weight", "percent", "portfolio", "% assets"])
        name_col = _pick_column(df.columns, ["name", "holding", "security"])

        rows = []
        for _, row in df.iterrows():
            symbol = _extract_symbol(row[symbol_col])
            if not symbol:
                continue
            name = str(row[name_col]).strip() if name_col else symbol
            weight = normalize_weight(row[weight_col]) if weight_col else pd.NA
            rows.append(
                {
                    "symbol": symbol,
                    "companyName": name or symbol,
                    "lastDividend": pd.NA,
                    "stock_type": "STOCK",
                    "group": etf,
                    "weight": weight,
                }
            )

        if not rows:
            return _empty_holdings_df()

        return _normalize_holdings_df(pd.DataFrame(rows))
    except Exception as e:
        print(f"{etf} yfinance fallback failed: {e}")
        return _empty_holdings_df()


def fetch_etf_constituents(etf):
    print(f"Fetching ETF holdings for {etf} (official source first)...")

    df = _fetch_official_holdings(etf)
    if not df.empty:
        return df, "official"

    print(f"No official-source holdings parsed for {etf}; trying slickcharts fallback.")
    df = _fetch_holdings_from_slickcharts(etf)
    if not df.empty:
        return df, "slickcharts"

    print(f"No slickcharts holdings parsed for {etf}; using yfinance fallback.")
    df = _fetch_holdings_from_yfinance(etf)
    if not df.empty:
        return df, "yfinance"

    return _empty_holdings_df(), "failed"


def fetch_dividend_etf_constituents():
    frames = []
    success_groups = []
    failed_groups = []

    for etf in ETF_GROUPS:
        df, source = fetch_etf_constituents(etf)
        if df.empty:
            cached_df = _load_cached_etf_holdings(etf)
            if cached_df.empty:
                failed_groups.append(etf)
                print(f"{etf}: failed to fetch holdings from free sources and cache.")
                continue
            success_groups.append(f"{etf}(cache)")
            frames.append(cached_df)
            continue

        success_groups.append(f"{etf}({source})")
        frames.append(_normalize_holdings_df(df))

    if not frames:
        return _empty_holdings_df(), success_groups, failed_groups

    out = _normalize_holdings_df(pd.concat(frames, ignore_index=True))
    live_df = out[out["group"].isin([g for g in ETF_GROUPS if f"{g}(cache)" not in success_groups])]
    _save_cached_etf_holdings(live_df)
    return out, success_groups, failed_groups


def run_update():
    """배당 종목 정보를 업데이트하여 구글 시트에 저장합니다. ETF는 구성종목 기준으로 적재합니다."""
    global LAST_SUCCESS_GROUPS, LAST_FAILED_GROUPS

    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

    sp500_tickers = fetch_sp500_tickers(headers)
    nasdaq100_tickers = fetch_nasdaq100_tickers(headers)

    df_sp500 = build_index_group_rows(sp500_tickers, "S&P")
    df_nasdaq = build_index_group_rows(nasdaq100_tickers, "Nasdaq")

    df_etf, etf_success, etf_failed = fetch_dividend_etf_constituents()

    frames = [_normalize_holdings_df(df_sp500), _normalize_holdings_df(df_nasdaq), _normalize_holdings_df(df_etf)]
    non_empty = [f for f in frames if f is not None and not f.empty]

    if not non_empty:
        LAST_SUCCESS_GROUPS = []
        LAST_FAILED_GROUPS = ["S&P", "Nasdaq"] + ETF_GROUPS
        raise RuntimeError("수집 가능한 데이터가 없어 동기화를 중단했습니다.")

    df_combined = _normalize_holdings_df(pd.concat(non_empty, ignore_index=True))

    df_combined["weight"] = df_combined["weight"].apply(normalize_weight)

    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    df_combined["updated_at"] = now_str

    df_combined = df_combined[df_combined["symbol"] != ""]
    df_combined = df_combined[df_combined["group"] != ""]
    df_combined = df_combined.drop_duplicates(subset=["group", "symbol"], keep="first")

    if df_combined.empty:
        LAST_SUCCESS_GROUPS = []
        LAST_FAILED_GROUPS = ["S&P", "Nasdaq"] + ETF_GROUPS
        raise RuntimeError("정제 후 남은 데이터가 없어 동기화를 중단했습니다.")

    print("Syncing data to Google Sheets...")
    sheets_helper.save_stocks(df_combined)
    print("Google Sheets sync successful!")

    success_groups = []
    if not df_sp500.empty:
        success_groups.append("S&P")
    if not df_nasdaq.empty:
        success_groups.append("Nasdaq")
    success_groups.extend(etf_success)

    failed_groups = list(etf_failed)
    if df_sp500.empty:
        failed_groups.append("S&P")
    if df_nasdaq.empty:
        failed_groups.append("Nasdaq")

    LAST_SUCCESS_GROUPS = success_groups
    LAST_FAILED_GROUPS = failed_groups

    return df_combined


def main():
    run_update()


if __name__ == "__main__":
    main()
