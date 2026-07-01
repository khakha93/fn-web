from datetime import datetime, timezone, timedelta
import pandas as pd


def to_unix_timestamp(date_str):
    # 한국 시간대 설정
    kst = timezone(timedelta(hours=9))
    
    # 문자열 → datetime 객체 변환
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    dt = dt.replace(tzinfo=kst)
    
    # 유닉스 타임스탬프 반환
    return int(dt.timestamp())

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