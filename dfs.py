import yfinance as yf
import pandas as pd

import div_yf as dyf
# import utills as ut


def get_div_data(ticker:str, df_close: pd.DataFrame):
    # 배당금 지급 내역 가져오기
    df_div_period = dyf.get_yf_dividend_history(ticker)
    # 배당급 집계
    dyf.add_period_columns_by_div(df_div_period)
    df_com = dyf.group_by_period_by_div(df_div_period)

    dfs_agg, df_stat = dyf.merge_dividend_data(df_close, df_com)
    return dfs_agg, df_stat

if __name__ == "__main__":
    print("execute!")

