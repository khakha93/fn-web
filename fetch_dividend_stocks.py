import os
import requests
import pandas as pd
import yfinance as yf
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
from io import StringIO

# Try to load API key from environment variable or local .env file
API_KEY = os.environ.get("FMP_API_KEY")
if not API_KEY:
    try:
        with open(".env", "r") as f:
            for line in f:
                if line.startswith("FMP_API_KEY="):
                    API_KEY = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
    except:
        pass

def fetch_from_fmp():
    # Attempting to fetch using FMP's stable company screener
    url = f"https://financialmodelingprep.com/stable/company-screener?dividendMoreThan=0&country=US&limit=10000&apikey={API_KEY}"
    print("Attempting to fetch from Financial Modeling Prep (FMP) API...")
    try:
        response = requests.get(url)
        if response.status_code == 200:
            data = response.json()
            if isinstance(data, list) and len(data) > 0:
                df = pd.DataFrame(data)
                cols = [c for c in ['symbol', 'companyName', 'dividend'] if c in df.columns]
                result_df = df[cols].copy()
                result_df.rename(columns={'companyName': 'companyName', 'dividend': 'lastDividend'}, inplace=True)
                return result_df
            else:
                print("FMP returned empty list or unexpected format.")
        else:
            print(f"FMP API returned status code {response.status_code}.")
            print("Response:", response.text)
    except Exception as e:
        print(f"Error fetching from FMP: {e}")
    return None

def fetch_sp500_tickers(headers):
    print("Fetching S&P 500 tickers from Wikipedia...")
    try:
        url = 'https://en.wikipedia.org/wiki/List_of_S%26P_500_companies'
        response = requests.get(url, headers=headers)
        tables = pd.read_html(StringIO(response.text))
        df = tables[0]
        # Standardize tickers (replace . with - for yfinance)
        tickers = [t.replace('.', '-') for t in df['Symbol'].tolist()]
        print(f"Found {len(tickers)} S&P 500 tickers.")
        return tickers
    except Exception as e:
        print(f"Error fetching S&P 500: {e}")
        return []

def fetch_nasdaq100_tickers(headers):
    print("Fetching NASDAQ 100 tickers from Wikipedia...")
    try:
        url = 'https://en.wikipedia.org/wiki/Nasdaq-100'
        response = requests.get(url, headers=headers)
        tables = pd.read_html(StringIO(response.text))
        for t in tables:
            if 'Ticker' in t.columns:
                tickers = [symbol.replace('.', '-') for symbol in t['Ticker'].tolist()]
                print(f"Found {len(tickers)} NASDAQ 100 tickers.")
                return tickers
            elif 'Symbol' in t.columns:
                tickers = [symbol.replace('.', '-') for symbol in t['Symbol'].tolist()]
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
                name = t.info.get('longName', ticker)
            except:
                pass
            return {'symbol': ticker, 'companyName': name, 'lastDividend': last_div}
    except Exception:
        pass
    return None

def fetch_local_fallback():
    print("\n--- Running Fallback Generator (S&P 500 & NASDAQ 100 constituents via yfinance) ---")
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    
    # 1. Fetch S&P 500 and NASDAQ 100 lists
    sp500 = fetch_sp500_tickers(headers)
    nasdaq100 = fetch_nasdaq100_tickers(headers)
    
    # 2. Merge lists to get unique tickers
    unique_tickers = sorted(list(set(sp500 + nasdaq100)))
    print(f"Total unique tickers to check: {len(unique_tickers)}")
    
    if not unique_tickers:
        print("No tickers found to check. Exiting.")
        return None

    # 3. Check dividends in parallel
    results = []
    print("Checking dividend history for tickers in parallel...")
    start_time = time.time()
    
    with ThreadPoolExecutor(max_workers=15) as executor:
        futures = {executor.submit(check_dividend_status, ticker): ticker for ticker in unique_tickers}
        for future in as_completed(futures):
            res = future.result()
            if res:
                results.append(res)
                
    elapsed = time.time() - start_time
    print(f"Finished checking in {elapsed:.2f} seconds.")
    print(f"Found {len(results)} dividend-paying stocks.")
    
    return pd.DataFrame(results)

def main():
    # 1. Try FMP
    df = fetch_from_fmp()
    
    # 2. If FMP fails, run local fallback
    if df is None or df.empty:
        print("FMP API query failed or was restricted. Using local index fallback...")
        df = fetch_local_fallback()
        
    if df is not None and not df.empty:
        # Save to csv
        output_path = "c:/dev_project/fn-web/us_dividend_tickers.csv"
        df.to_csv(output_path, index=False, encoding='utf-8-sig')
        print(f"\nSuccess! Saved {len(df)} dividend stocks to '{output_path}'.")
        print(df.head(10))
    else:
        print("Failed to retrieve any dividend stocks.")

if __name__ == "__main__":
    main()
