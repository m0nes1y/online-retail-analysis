# -*- coding: utf-8 -*-
"""
load_data.py — 将 UCI Online Retail II 原始数据(xlsx) 清洗后加载进 SQLite

数据源：UCI Machine Learning Repository — Online Retail II
       https://archive.ics.uci.edu/dataset/502/online+retail+ii
       英国某在线礼品零售商 2009-12-01 ~ 2011-12-09 全渠道交易明细（约 106 万行，公开数据集）

清洗规则（数据治理，与真实数仓 dwd 层口径一致）：
  1. 剔除取消/退货订单：Invoice 以 'C' 开头
  2. 剔除数量异常：Quantity <= 0
  3. 剔除价格异常：Price <= 0
  4. 剔除缺失客户：CustomerID 为空（无法归属用户，RFM/复购分析会漏算）
  5. 全字段去重

产出：ecommerce.db 中的 dwd_trade_order 表（订单行明细）+ 索引
运行：python load_data.py
"""
import os
import sqlite3
import sys

import pandas as pd

ROOT = os.path.dirname(os.path.abspath(__file__))
XLSX_PATH = os.path.join(ROOT, '..', '..', 'downloads', 'online_retail_II.zip')  # 占位，实际用解压后 xlsx
XLSX_DIR = os.path.join(ROOT, '..', '..', 'downloads', 'online_retail_II')
DB_PATH = os.path.join(ROOT, 'ecommerce.db')


def find_xlsx():
    """在 downloads 目录下寻找 online_retail_II.xlsx（zip 解压后的文件）。"""
    cands = []
    for base in [os.path.join(ROOT, '..', '..', 'downloads'),
                 os.path.join(ROOT, '..', '..', 'downloads', 'online_retail_II'),
                 ROOT]:
        if os.path.isdir(base):
            for f in os.listdir(base):
                if f.lower().endswith('.xlsx') and 'retail' in f.lower():
                    cands.append(os.path.join(base, f))
    if not cands:
        # 兜底：任何 xlsx
        for base in [os.path.join(ROOT, '..', '..', 'downloads')]:
            if os.path.isdir(base):
                for f in os.listdir(base):
                    if f.lower().endswith('.xlsx'):
                        cands.append(os.path.join(base, f))
    return cands[0] if cands else None


def clean(df: pd.DataFrame) -> pd.DataFrame:
    n0 = len(df)
    df = df.drop_duplicates()
    n_dup = n0 - len(df)

    df = df[~df['Invoice'].astype(str).str.startswith('C')]
    n_cancel = n0 - n_dup - len(df)

    df = df[df['Quantity'] > 0]
    n_qty = len(df)

    # 3) 剔除价格异常：Price < 0.01（低于最小货币单位 1 便士，视为脏数据；
    #    原始数据中存在 Price=0.001 级别的行，round(2) 后会变成 0 金额）
    df = df[df['Price'] >= 0.01]
    n_price = len(df)

    df = df[df['Customer ID'].notna()]
    n_cust = len(df)

    # 派生字段：订单行金额 = 数量 × 单价；日期（yyyy-mm-dd）
    df = df.copy()
    df['amount'] = (df['Quantity'] * df['Price']).round(4)
    df['dt'] = pd.to_datetime(df['InvoiceDate']).dt.strftime('%Y-%m-%d')
    df['month'] = pd.to_datetime(df['InvoiceDate']).dt.strftime('%Y-%m')
    df['Customer ID'] = df['Customer ID'].astype(int)

    print(f'[清洗] 原始 {n0:,} 行 → 去重 -{n_dup:,} → 剔除取消单 -{n_cancel:,} '
          f'→ 剔除非正数量 → {n_qty:,} → 剔除非正价格 → {n_price:,} '
          f'→ 剔除无客户 → 有效 {n_cust:,} 行')
    return df.reset_index(drop=True)


def main():
    xlsx = find_xlsx()
    if not xlsx:
        print('[错误] 未找到 online_retail_II.xlsx，请先将数据集 zip 解压到 downloads/ 目录')
        sys.exit(1)
    print(f'[读取] {xlsx}')
    xl = pd.ExcelFile(xlsx)
    print(f'[读取] sheets: {xl.sheet_names}')

    frames = []
    for sh in xl.sheet_names:
        d = pd.read_excel(xl, sheet_name=sh)
        print(f'[读取] {sh}: {len(d):,} 行')
        frames.append(d)
    raw = pd.concat(frames, ignore_index=True)
    print(f'[读取] 合计 {len(raw):,} 行')

    df = clean(raw)

    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    conn = sqlite3.connect(DB_PATH)
    df[['Invoice', 'StockCode', 'Description', 'Quantity',
        'InvoiceDate', 'Price', 'Customer ID', 'Country',
        'amount', 'dt', 'month']].to_sql(
        'dwd_trade_order', conn, if_exists='replace', index=False)
    conn.execute('CREATE INDEX idx_dt ON dwd_trade_order(dt)')
    conn.execute('CREATE INDEX idx_cust ON dwd_trade_order("Customer ID")')
    conn.commit()
    n = conn.execute('SELECT COUNT(*) FROM dwd_trade_order').fetchone()[0]
    print(f'[入库] ecommerce.db / dwd_trade_order：{n:,} 行')
    print(f'[入库] 覆盖周期：{df["dt"].min()} ~ {df["dt"].max()}')
    conn.close()


if __name__ == '__main__':
    main()
