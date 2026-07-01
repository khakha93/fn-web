import time
import pandas as pd
import yfinance as yf


def get_yf_dividend_history(ticker):
    Ticker = yf.Ticker(ticker)
    df_div_period = Ticker.dividends.reset_index()
    df_div_period['Date'] = pd.to_datetime(df_div_period['Date'].dt.date)
    return df_div_period

def add_period_columns_by_div(df):
    # df_temp = df.sort_index().reset_index()
    df['period'] = (df['Dividends'] != df['Dividends'].shift()).cumsum() - 1
    return df

def group_by_period_by_div(df, sum_frequency=4):
    df_com = df.groupby('period').agg(
            start_date=("Date", "min"),
            end_date=("Date", "max"),
            dividend_mean=("Dividends", "mean"),
            dividend_sum=("Dividends", "sum"),
            count=("Date", "count"))
    # $$$ 1년 기준으로 통일시켜줘야함
    # df_com['adj_div'] = df_com['dividend_mean'] * df_com['count'].mode()[0]
    df_com['adj_div'] = df_com['dividend_mean'] * sum_frequency
    df_com['div_change'] = df_com['adj_div'].pct_change()
    return df_com.reset_index()

def merge_dividend_data(df_price, df_com):
    ticker = df_price.columns[0]

    # 각 배당 주기에 대하여, 시작 날짜의 다음날부터 다음 주기의 시작 날짜까지, adj_div 를 기입
    df_merge = pd.merge(df_price.reset_index(), df_com, left_on='Date', right_on='start_date', how='outer')
    cols = ['period', 'start_date', 'end_date', 'dividend_mean', 'dividend_sum', 'count', 'adj_div', 'div_change']
    df_merge.loc[:, cols] = df_merge.loc[:, cols].shift(1).ffill()
    df_merge.dropna(subset=[ticker, "period"], inplace=True)
    df_merge['dfs'] = df_merge['adj_div'] / df_merge[ticker]


    # dfs_agg: 기간별 통계 집계
    dfs_agg = df_merge.groupby('period').agg(
        date_s=('Date', 'min'),
        date_e=('Date', 'max'),
        div=('adj_div', 'mean'),
        pr_min=(ticker, 'min'),
        pr_max=(ticker, 'max'),
        dfs_min=('dfs', 'min'),
        dfs_max=('dfs', 'max'),)

    # 다시 정보 취합
    df_left = df_merge[['Date', ticker, 'period', 'start_date', 'end_date', 'adj_div', 'dfs']].reset_index(drop=True)
    df_right = dfs_agg.shift(1).loc[df_merge['period']].reset_index(drop=True)
    df_stat = pd.concat([df_left, df_right], axis=1)

    return dfs_agg, df_stat