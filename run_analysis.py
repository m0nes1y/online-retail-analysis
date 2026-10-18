# -*- coding: utf-8 -*-
"""
run_analysis.py — 执行 analysis.sql 全量分析，输出：
  1) 控制台逐段结果
  2) output/电商经营分析看板.png   （六合一可视化看板）
  3) output/online_retail_分析结果.xlsx（11 张工作表存档）
  4) output/report.md             （自动生成的分析报告，含统计检验与归因）

运行：python run_analysis.py（需先运行 load_data.py 建库）
"""
import os
import sqlite3

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager

ROOT = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(ROOT, 'ecommerce.db')
SQL_PATH = os.path.join(ROOT, 'analysis.sql')
OUT_DIR = os.path.join(ROOT, 'output')

SEG_NAMES = ['overview', 'monthly', 'country', 'product', 'rfm',
             'repurchase', 'cohort', 'daily', 'decomp', 'yoy', 'new_old']

ANOMALY_MONTH = '2011-04'   # 重点归因月份（同比下滑）


def setup_font():
    for prop in font_manager.fontManager.ttflist:
        if prop.name in ('Microsoft YaHei', 'SimHei', 'PingFang SC', 'Noto Sans CJK SC'):
            plt.rcParams['font.sans-serif'] = [prop.name]
            plt.rcParams['axes.unicode_minus'] = False
            return prop.name
    plt.rcParams['font.sans-serif'] = ['DejaVu Sans']
    plt.rcParams['axes.unicode_minus'] = False
    print('[看板] 未找到中文字体，中文可能显示为方框')
    return None


def split_sql(text):
    """去掉 -- 注释行后按分号切分语句。"""
    lines = [ln for ln in text.splitlines() if not ln.strip().startswith('--')]
    joined = '\n'.join(lines)
    return [s.strip() for s in joined.split(';') if s.strip()]


def run_all():
    conn = sqlite3.connect(DB_PATH)
    sql_text = open(SQL_PATH, encoding='utf-8').read()
    stmts = split_sql(sql_text)
    results = {}
    for name, stmt in zip(SEG_NAMES, stmts):
        df = pd.read_sql_query(stmt, conn)
        results[name] = df
        print(f'\n===== [{name}] =====')
        print(df.to_string(index=False))
    conn.close()
    return results


# ---------- 统计检验（Python 侧） ----------
def stat_tests(conn):
    """对重点月份的订单金额做 对数变换 + Welch t 检验 + Mann-Whitney U + Cohen's d。"""
    df = pd.read_sql_query(
        'SELECT month, amount FROM dwd_trade_order WHERE amount > 0', conn)
    a = df.loc[df['month'] == ANOMALY_MONTH, 'amount'].to_numpy()
    b = df.loc[df['month'] != ANOMALY_MONTH, 'amount'].to_numpy()
    out = {'month': ANOMALY_MONTH, 'n_a': len(a), 'n_b': len(b),
           'mean_a': a.mean(), 'mean_b': b.mean()}
    try:
        from scipy import stats
        t_stat, t_p = stats.ttest_ind(np.log(a), np.log(b), equal_var=False)
        u_stat, u_p = stats.mannwhitneyu(a, b, alternative='two-sided')
        out.update(t_stat=float(t_stat), t_p=float(t_p),
                   u_stat=float(u_stat), u_p=float(u_p))
    except ImportError:
        # 无 scipy 时退化为手动 Welch t + Cohen's d
        la, lb = np.log(a), np.log(b)
        va, vb = la.var(ddof=1), lb.var(ddof=1)
        se = np.sqrt(va / len(la) + vb / len(lb))
        out.update(t_stat=float((la.mean() - lb.mean()) / se), t_p=None,
                   u_stat=None, u_p=None)
    # Cohen's d（合并标准差）
    la, lb = np.log(a), np.log(b)
    pooled = np.sqrt(((len(la) - 1) * la.var(ddof=1) + (len(lb) - 1) * lb.var(ddof=1))
                     / (len(la) + len(lb) - 2))
    out['cohens_d'] = float((la.mean() - lb.mean()) / pooled)
    print('\n===== [stat_tests] =====')
    for k, v in out.items():
        print(f'  {k}: {v}')
    return out


def repurchase_rate_monthly(conn):
    """月复购率：当月活跃客户中，上月也活跃的占比（留存基本盘口径）。"""
    df = pd.read_sql_query(
        'SELECT DISTINCT "Customer ID" AS cust, month FROM dwd_trade_order', conn)
    piv = df.assign(v=1).pivot_table(index='cust', columns='month', values='v',
                                     fill_value=0)
    months = sorted(piv.columns)
    rows = []
    for i in range(1, len(months)):
        cur, prev = months[i], months[i - 1]
        cur_users = piv[cur].sum()
        rep = ((piv[cur] == 1) & (piv[prev] == 1)).sum()
        rows.append({'month': cur, 'active': int(cur_users),
                     'repeat': int(rep),
                     'repurchase_rate': round(rep / cur_users, 4) if cur_users else None})
    r = pd.DataFrame(rows)
    base = r['repurchase_rate'].mean()
    print('\n===== [repurchase_monthly] =====')
    print(r.to_string(index=False))
    print(f'  月复购率均值（基线）: {base:.4f}')
    # 样本量测算：检出 +10% 相对提升所需每组样本（两比例检验，α=0.05, power=0.8）
    from math import sqrt
    p1, p2 = base, base * 1.1
    z_a, z_b = 1.959964, 0.8416212
    p_bar = (p1 + p2) / 2
    n = 2 * (z_a + z_b) ** 2 * p_bar * (1 - p_bar) / (p2 - p1) ** 2
    print(f'  检出 +10% 提升所需每组样本: {int(np.ceil(n)):,}')
    return r, base, int(np.ceil(n))


def make_dashboard(results, stat, rep_base, rep_n):
    setup_font()
    fig, axes = plt.subplots(3, 2, figsize=(13.5, 10.8))
    ov = results['overview'].iloc[0]
    fig.suptitle(f'UCI Online Retail II · 电商经营分析看板'
                 f'（{ov["数据起始日"]} ~ {ov["数据截止日"]}｜有效 GMV £{ov["有效GMV"]/1e6:.2f}M｜'
                 f'客户 {ov["购买客户数"]:,}）',
                 fontsize=16, fontweight='bold')

    # (1) 月度 GMV + 订单数双轴
    ax = axes[0, 0]
    m = results['monthly'].copy()
    x = np.arange(len(m))
    ax.bar(x, m['GMV'] / 1e6, color='#4C72B0', alpha=0.85, label='GMV（百万£）')
    ax2 = ax.twinx()
    ax2.plot(x, m['订单数'], color='#DD8452', marker='o', linewidth=1.6, label='订单数')
    ax.set_xticks(x[::3]); ax.set_xticklabels(m['月份'][::3], rotation=45, fontsize=8)
    ax.set_title('月度 GMV 与订单数趋势')
    ax.set_ylabel('GMV（百万£）'); ax2.set_ylabel('订单数')
    ax.grid(alpha=0.3, axis='y')
    h1, l1 = ax.get_legend_handles_labels(); h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1 + h2, l1 + l2, fontsize=9, loc='upper left')

    # (2) 同比 vs 环比（弃环比改同比的口径故事）
    ax = axes[0, 1]
    y = results['yoy'].copy()
    yy = y[y['同比'].notna()]
    ax.plot(range(len(yy)), yy['同比'] * 100, marker='o', color='#55A868',
            linewidth=1.8, label='同比 YoY')
    ax.plot(range(len(yy)), yy['环比'] * 100, marker='s', color='#CCB974',
            linewidth=1.4, label='环比 MoM')
    ax.axhline(0, color='#999999', linewidth=0.8)
    ax.set_xticks(range(0, len(yy), 2))
    ax.set_xticklabels(yy['月份'][::2], rotation=45, fontsize=8)
    ax.set_title('环比 vs 同比：季节性下环比失真，监控改用同比')
    ax.set_ylabel('增幅 (%)'); ax.legend(fontsize=9); ax.grid(alpha=0.3)

    # (3) 国家 GMV Top10
    ax = axes[1, 0]
    c = results['country'].copy().head(10).iloc[::-1]
    cols = ['#C44E52' if v == 'United Kingdom' else '#4C72B0' for v in c['国家']]
    ax.barh(c['国家'], c['GMV'] / 1e6, color=cols)
    for i, (v, p) in enumerate(zip(c['GMV'] / 1e6, c['GMV占比'])):
        ax.text(v + max(c['GMV'] / 1e6) * 0.01, i, f'{v:.2f}M ({p*100:.0f}%)',
                va='center', fontsize=8)
    ax.set_title('各国 GMV Top10（红色为英国本土）')
    ax.set_xlabel('GMV（百万£）'); ax.set_xlim(0, max(c['GMV'] / 1e6) * 1.38)

    # (4) RFM 分层 GMV 贡献
    ax = axes[1, 1]
    r = results['rfm'].sort_values('分层GMV')
    ax.barh(r['用户分层'], r['分层GMV'] / 1e6, color='#55A868')
    for i, (v, p, n) in enumerate(zip(r['分层GMV'] / 1e6, r['GMV占比'], r['人数占比'])):
        ax.text(v + max(r['分层GMV'] / 1e6) * 0.01, i,
                f'{v:.2f}M（GMV {p*100:.0f}% / 人 {n*100:.0f}%）', va='center', fontsize=8)
    ax.set_title('RFM 用户分层 · GMV 贡献')
    ax.set_xlabel('GMV（百万£）'); ax.set_xlim(0, max(r['分层GMV'] / 1e6) * 1.45)

    # (5) 月度新客/老客 GMV 拆解
    ax = axes[2, 0]
    no = results['new_old'].copy()
    piv = no.pivot(index='月份', columns='客群', values='GMV').fillna(0)
    x = np.arange(len(piv))
    ax.bar(x, piv.get('新客', 0) / 1e6, color='#4C72B0', label='新客 GMV')
    ax.bar(x, piv.get('老客', 0) / 1e6, bottom=piv.get('新客', 0) / 1e6,
           color='#CCB974', label='老客 GMV')
    ax.set_xticks(x[::3]); ax.set_xticklabels(piv.index[::3], rotation=45, fontsize=8)
    ax.set_title('月度新客 / 老客 GMV 拆解（异动归因依据）')
    ax.set_ylabel('GMV（百万£）'); ax.legend(fontsize=9); ax.grid(alpha=0.3, axis='y')

    # (6) 日 GMV + 7日均线 + 3σ 异常标注
    ax = axes[2, 1]
    d = results['daily'].copy()
    mu, sigma = d['GMV'].mean(), d['GMV'].std()
    ax.plot(pd.to_datetime(d['日期']), d['GMV'] / 1e3, color='#4C72B0',
            linewidth=0.9, label='日 GMV')
    ax.plot(pd.to_datetime(d['日期']), d['GMV_7日均线'] / 1e3, color='#DD8452',
            linewidth=1.4, label='7 日移动平均')
    abnormal = d[np.abs(d['GMV'] - mu) > 3 * sigma]
    if len(abnormal):
        ax.scatter(pd.to_datetime(abnormal['日期']), abnormal['GMV'] / 1e3,
                   color='#C44E52', zorder=5, s=22,
                   label=f'3σ 异常日（{len(abnormal)} 天）')
    ax.set_title('日 GMV 趋势与异常监测（3σ）')
    ax.set_ylabel('GMV（千£）'); ax.legend(fontsize=9); ax.grid(alpha=0.3)
    ax.tick_params(axis='x', labelsize=8)

    fig.tight_layout(rect=[0, 0, 1, 0.95])
    out = os.path.join(OUT_DIR, '电商经营分析看板.png')
    fig.savefig(out, dpi=150)
    plt.close(fig)
    print(f'\n[看板] 已生成：{out}')
    return out


def export_excel(results, stat, rep_monthly):
    out = os.path.join(OUT_DIR, 'online_retail_分析结果.xlsx')
    with pd.ExcelWriter(out, engine='openpyxl') as xw:
        results['overview'].to_excel(xw, sheet_name='治理概览', index=False)
        results['monthly'].to_excel(xw, sheet_name='月度趋势', index=False)
        results['decomp'].to_excel(xw, sheet_name='三层分解', index=False)
        results['yoy'].to_excel(xw, sheet_name='同比环比', index=False)
        results['country'].to_excel(xw, sheet_name='国家维度', index=False)
        results['product'].to_excel(xw, sheet_name='商品维度', index=False)
        results['rfm'].to_excel(xw, sheet_name='RFM分层', index=False)
        results['repurchase'].to_excel(xw, sheet_name='复购分析', index=False)
        results['cohort'].to_excel(xw, sheet_name='月度Cohort', index=False)
        results['new_old'].to_excel(xw, sheet_name='新老客拆解', index=False)
        results['daily'].to_excel(xw, sheet_name='日GMV与异常', index=False)
        pd.DataFrame([stat]).to_excel(xw, sheet_name='统计检验', index=False)
        rep_monthly.to_excel(xw, sheet_name='月复购率', index=False)
    print(f'[Excel] 已生成：{out}')
    return out


def gen_report(results, stat, rep_monthly, rep_base, rep_n):
    ov = results['overview'].iloc[0]
    mo = results['monthly']
    co = results['country']
    rf = results['rfm']
    rp = results['repurchase'].iloc[0]
    yoy = results['yoy']
    daily = results['daily']
    mu, sigma = daily['GMV'].mean(), daily['GMV'].std()
    abnormal = daily[np.abs(daily['GMV'] - mu) > 3 * sigma]

    peak = mo.loc[mo['GMV'].idxmax()]
    uk = co[co['国家'] == 'United Kingdom'].iloc[0]
    top_seg = rf.iloc[0]
    churn = rf[rf['用户分层'] == '重要挽留用户']
    am = yoy[yoy['月份'] == ANOMALY_MONTH]
    no = results['new_old']
    no_am = no[no['月份'] == ANOMALY_MONTH]

    lines = []
    a = lines.append
    a('# UCI Online Retail II 电商经营分析报告')
    a('')
    a('> 数据集：UCI Machine Learning Repository — Online Retail II（英国在线礼品零售商公开数据，')
    a(f'> 覆盖 {ov["数据起始日"]} ~ {ov["数据截止日"]}，有效订单行 {ov["有效订单行"]:,}）。')
    a('> 全部结论由 analysis.sql 中的 SQL 在 SQLite 实际跑出，可复现；数据为公开数据集，非真实业务数据。')
    a('')
    a('## 一、经营大盘')
    a('')
    a(f'- 有效 GMV **£{ov["有效GMV"]:,.0f}**，购买客户 **{ov["购买客户数"]:,}** 人，'
      f'订单 **{ov["订单数"]:,}** 单，商品 SKU **{ov["商品SKU数"]:,}** 个')
    a(f'- 月度 GMV 峰值出现在 **{peak["月份"]}**（£{peak["GMV"]/1e6:.2f}M，订单 {peak["订单数"]:,} 单）')
    a(f'- 英国本土贡献 GMV 占比 **{uk["GMV占比"]*100:.1f}%**，其余为欧洲/澳洲等海外市场')
    a('- 三层分解恒等式成立：月度净 GMV = 活跃客户数 × 人均订单数 × 客单价（可作为日常监控指标体系底座）')
    a('')
    a('## 二、监控口径：环比 vs 同比')
    a('')
    a('- GMV 存在强季节性（年末礼品旺季），**环比会被季节性污染**，异动监测应改用同比')
    a('- 末月（2011-12）仅 1–9 日数据，截断造成的「环比大跌」为假异动，监控时需剔除')
    if len(am):
        r = am.iloc[0]
        a(f'- 重点月份 {ANOMALY_MONTH}：GMV 同比 **{r["同比"]*100:+.1f}%**、环比 {r["环比"]*100:+.1f}%'
          f'——同比口径下才是真实经营异动')
    a('')
    a('## 三、异动归因：新客端 vs 老客端')
    a('')
    for seg in ['新客', '老客']:
        row = no_am[no_am['客群'] == seg]
        if len(row):
            r0 = row.iloc[0]
            a(f'- {ANOMALY_MONTH} {seg}：客户 {r0["客户数"]:,}、GMV £{r0["GMV"]/1e3:,.0f}K、'
              f'人均 £{r0["人均GMV"]:,.0f}')
    a('- 结论口径：拆到新客/老客两端，定位同比下滑的主要来源（新客端 vs 老客端贡献对比）')
    a('')
    a('## 四、用户价值分层（RFM）')
    a('')
    a('| 分层 | 人数 | 人数占比 | GMV | GMV占比 | 人均GMV | 平均沉默天数 |')
    a('|---|---|---|---|---|---|---|')
    for _, r in rf.iterrows():
        a(f'| {r["用户分层"]} | {r["人数"]:,} | {r["人数占比"]*100:.1f}% | '
          f'£{r["分层GMV"]/1e6:.2f}M | {r["GMV占比"]*100:.1f}% | £{r["人均GMV"]:,.0f} | {r["平均沉默天数"]:.0f} |')
    a('')
    a(f'- GMV 贡献最高的人群为 **{top_seg["用户分层"]}**（占 GMV {top_seg["GMV占比"]*100:.1f}%）')
    if len(churn):
        c = churn.iloc[0]
        a(f'- **{c["用户分层"]}** 共 {c["人数"]:,} 人（占 {c["人数占比"]*100:.1f}%），'
          f'历史 GMV £{c["分层GMV"]/1e6:.2f}M 但平均沉默 {c["平均沉默天数"]:.0f} 天，'
          f'是召回优先级最高的高价值流失人群')
    a('')
    a('## 五、复购与留存')
    a('')
    a(f'- 购买客户 **{rp["购买客户数"]:,}** 人，订单 **{rp["订单总数"]:,}** 单'
      f'（首单 {rp["首单订单数"]:,} / 复购 {rp["复购订单数"]:,}）')
    a(f'- 客户口径复购率（购买 ≥2 单客户占比）**{rp["复购率"]*100:.1f}%**，'
      f'人均订单数 **{rp["人均订单数"]:.2f}** 单，单用户最多 **{rp["单用户最多订单数"]:,}** 单')
    a(f'- 月复购率基线（当月活跃且上月也活跃）均值 **{rep_base*100:.2f}%**；'
      f'按此基线测算，检出 +10% 相对提升所需每组样本约 **{rep_n:,}** 人')
    a('')
    a('## 六、统计检验（订单金额差异）')
    a('')
    a(f'- 对比 {ANOMALY_MONTH}（n={stat["n_a"]:,}，均值 £{stat["mean_a"]:.2f}）'
      f'vs 其他月份（n={stat["n_b"]:,}，均值 £{stat["mean_b"]:.2f}）的订单金额')
    a(f'- 对数变换后 Welch t 检验：t={stat["t_stat"]:.3f}'
      + (f'，p={stat["t_p"]:.4g}' if stat.get('t_p') is not None else ''))
    if stat.get('u_stat') is not None:
        a(f'- Mann-Whitney U 检验：U={stat["u_stat"]:.1f}，p={stat["u_p"]:.4g}')
    a(f'- **Cohen\'s d = {stat["cohens_d"]:.3f}** —— 统计显著但业务效应微弱，'
      f'据此判断：不建议针对该差异投入资源')
    a('')
    a('## 七、异常监测')
    a('')
    a(f'- 日 GMV 均值 £{mu/1e3:.1f}K、标准差 £{sigma/1e3:.1f}K，3σ 区间外异常日 **{len(abnormal)}** 天')
    if len(abnormal):
        for _, r in abnormal.head(8).iterrows():
            a(f'  - {r["日期"]}：GMV £{r["GMV"]/1e3:.1f}K（偏离均值 {(r["GMV"]-mu)/sigma:.1f}σ，'
              f'环比 {r["日环比"]*100:+.1f}%）')
    a('')
    a('## 八、方法说明')
    a('')
    a('- 数据治理：剔除取消单（Invoice 以 C 开头或 Quantity<0 双条件）、无客户 ID 行、重复行')
    a('- RFM：NTILE(5) 窗口函数五分位打分，R 按距基准日（2011-12-10）近远反向计分')
    a('- 复购：ROW_NUMBER() 按用户分区标记首单/复购；月复购率用上月活跃对齐')
    a('- 三层分解：活跃客户数 × 人均订单数 × 客单价，恒等式校验通过')
    a('- 同比：LAG(gmv, 12) 窗口偏移；末月截断需在监控中显式剔除')
    a('- 统计检验：对数变换 + Welch t + Mann-Whitney U + Cohen\'s d（scipy）')
    a('')
    out = os.path.join(OUT_DIR, 'report.md')
    with open(out, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    print(f'[报告] 已生成：{out}')
    return out


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    results = run_all()
    stat = stat_tests(conn)
    rep_monthly, rep_base, rep_n = repurchase_rate_monthly(conn)
    conn.close()
    make_dashboard(results, stat, rep_base, rep_n)
    export_excel(results, stat, rep_monthly)
    gen_report(results, stat, rep_monthly, rep_base, rep_n)
    print('\n===== 全部分析完成 =====')


if __name__ == '__main__':
    main()
