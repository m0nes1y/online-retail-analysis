-- ============================================================
-- online-retail-analysis · 电商经营分析 SQL（数据仓库口径）
-- 数据集：UCI Online Retail II（英国在线礼品零售，2009-12 ~ 2011-12，约 106 万行公开数据）
-- 分层：dwd_trade_order 明细事实表（dt 日分区口径）
-- 运行：python run_analysis.py（逐段执行并输出结果）
-- ============================================================

-- 1) 数据治理概览：有效行数 / 客户数 / 商品数 / 订单数 / 覆盖周期
SELECT
    COUNT(*)                                        AS 有效订单行,
    COUNT(DISTINCT "Customer ID")                   AS 购买客户数,
    COUNT(DISTINCT StockCode)                       AS 商品SKU数,
    COUNT(DISTINCT Invoice)                         AS 订单数,
    MIN(dt)                                         AS 数据起始日,
    MAX(dt)                                         AS 数据截止日,
    ROUND(SUM(amount), 2)                           AS 有效GMV
FROM dwd_trade_order;

-- 2) 月度经营趋势：GMV / 订单数 / 购买人数 / 客单价（按月）
SELECT
    month                                                   AS 月份,
    ROUND(SUM(amount), 2)                                   AS GMV,
    COUNT(DISTINCT Invoice)                                 AS 订单数,
    COUNT(DISTINCT "Customer ID")                           AS 购买人数,
    ROUND(1.0 * SUM(amount) / COUNT(DISTINCT Invoice), 2)   AS 客单价
FROM dwd_trade_order
GROUP BY month
ORDER BY month;

-- 3) 国家维度：各国 GMV 与占比（英国本土 vs 海外市场拆解）
SELECT
    Country                                    AS 国家,
    ROUND(SUM(amount), 2)                      AS GMV,
    COUNT(DISTINCT "Customer ID")              AS 客户数,
    ROUND(1.0 * SUM(amount) / SUM(SUM(amount)) OVER (), 4) AS GMV占比
FROM dwd_trade_order
GROUP BY Country
ORDER BY GMV DESC
LIMIT 12;

-- 4) 商品维度：Top15 热销商品（GMV 口径）
SELECT
    StockCode                          AS 商品编码,
    MAX(Description)                   AS 商品描述,
    SUM(Quantity)                      AS 销量,
    ROUND(SUM(amount), 2)              AS GMV,
    COUNT(DISTINCT Invoice)            AS 订单数
FROM dwd_trade_order
GROUP BY StockCode
ORDER BY GMV DESC
LIMIT 15;

-- 5) RFM 用户价值分层：NTILE 5 分位打分 → 6 类人群
--    基准日 = 数据截止日次日（2011-12-10）；R 越近分越高（DESC），F/M 越大分越高（ASC 分位）
WITH rfm_base AS (
    SELECT
        "Customer ID"                                        AS cust_id,
        MAX(dt)                                               AS last_dt,
        COUNT(DISTINCT Invoice)                               AS order_cnt,
        SUM(amount)                                           AS gmv,
        CAST(JULIANDAY('2011-12-10') - JULIANDAY(MAX(dt)) AS INTEGER) AS recency_days
    FROM dwd_trade_order
    GROUP BY "Customer ID"
),
rfm_score AS (
    SELECT
        cust_id, recency_days, order_cnt, gmv,
        NTILE(5) OVER (ORDER BY recency_days DESC) AS r_score,
        NTILE(5) OVER (ORDER BY order_cnt    ASC) AS f_score,
        NTILE(5) OVER (ORDER BY gmv         ASC) AS m_score
    FROM rfm_base
),
rfm_seg AS (
    SELECT
        cust_id, recency_days, order_cnt, ROUND(gmv, 2) AS gmv,
        r_score, f_score, m_score,
        CASE
            WHEN r_score >= 4 AND f_score >= 4 AND m_score >= 4 THEN '重要价值用户'
            WHEN r_score <= 2 AND m_score >= 4               THEN '重要挽留用户'
            WHEN r_score >= 4 AND f_score <= 2 AND m_score >= 4 THEN '重要发展用户'
            WHEN f_score >= 4 AND r_score <= 2 AND m_score <  4 THEN '重要保持用户'
            WHEN r_score <= 2 AND f_score <= 2 AND m_score <= 2 THEN '流失预警用户'
            ELSE '一般价值用户'
        END AS rfm_segment
    FROM rfm_score
)
SELECT
    rfm_segment                              AS 用户分层,
    COUNT(*)                                 AS 人数,
    ROUND(1.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 4)      AS 人数占比,
    ROUND(SUM(gmv), 2)                       AS 分层GMV,
    ROUND(1.0 * SUM(gmv) / SUM(SUM(gmv)) OVER (), 4)      AS GMV占比,
    ROUND(1.0 * SUM(gmv) / COUNT(*), 2)      AS 人均GMV,
    ROUND(AVG(order_cnt), 2)                 AS 人均订单数,
    ROUND(AVG(recency_days), 1)              AS 平均沉默天数
FROM rfm_seg
GROUP BY rfm_segment
ORDER BY 分层GMV DESC;

-- 6) 复购分析：先按「客户 × 订单」去重（事实表是订单行明细），
--    再用 ROW_NUMBER 按用户分区标记首单 / 复购订单，输出客户口径复购率
WITH user_invoice AS (
    SELECT DISTINCT "Customer ID" AS cust_id, Invoice AS invoice
    FROM dwd_trade_order
),
tagged AS (
    SELECT cust_id, invoice,
           ROW_NUMBER() OVER (PARTITION BY cust_id ORDER BY invoice) AS rn
    FROM user_invoice
)
SELECT
    COUNT(DISTINCT cust_id)                                            AS 购买客户数,
    COUNT(*)                                                           AS 订单总数,
    SUM(CASE WHEN rn = 1 THEN 1 ELSE 0 END)                            AS 首单订单数,
    SUM(CASE WHEN rn > 1 THEN 1 ELSE 0 END)                            AS 复购订单数,
    ROUND(1.0 * COUNT(DISTINCT CASE WHEN rn > 1 THEN cust_id END)
              / COUNT(DISTINCT cust_id), 4)                            AS 复购率,
    ROUND(1.0 * COUNT(*) / COUNT(DISTINCT cust_id), 2)                 AS 人均订单数,
    MAX(rn)                                                            AS 单用户最多订单数
FROM tagged;

-- 7) 月度 Cohort：每月新客 / 老客（复购客）人数，观察留存基本盘
WITH first_purchase AS (
    SELECT "Customer ID" AS cust_id, MIN(month) AS first_month
    FROM dwd_trade_order
    GROUP BY "Customer ID"
),
monthly_users AS (
    SELECT DISTINCT "Customer ID" AS cust_id, month
    FROM dwd_trade_order
)
SELECT
    m.month                                     AS 月份,
    SUM(CASE WHEN f.first_month = m.month THEN 1 ELSE 0 END) AS 新客数,
    SUM(CASE WHEN f.first_month <  m.month THEN 1 ELSE 0 END) AS 老客复购数,
    ROUND(1.0 * SUM(CASE WHEN f.first_month < m.month THEN 1 ELSE 0 END) / COUNT(*), 4) AS 老客占比
FROM monthly_users m
JOIN first_purchase f ON m.cust_id = f.cust_id
GROUP BY m.month
ORDER BY m.month;

-- 8) 日 GMV 异常监测基准：7 日移动平均 + 环比（3σ 判定在 Python 侧完成）
WITH daily AS (
    SELECT dt, ROUND(SUM(amount), 2) AS gmv
    FROM dwd_trade_order
    GROUP BY dt
)
SELECT
    dt                                                      AS 日期,
    gmv                                                     AS GMV,
    ROUND(AVG(gmv) OVER (ORDER BY dt ROWS BETWEEN 6 PRECEDING AND CURRENT ROW), 2) AS GMV_7日均线,
    ROUND(1.0 * gmv / NULLIF(LAG(gmv) OVER (ORDER BY dt), 0) - 1, 4)               AS 日环比
FROM daily
ORDER BY dt;

-- 9) 三层分解：月度净GMV = 活跃客户数 × 人均订单数 × 客单价（恒等式校验，指标体系底座）
WITH m AS (
    SELECT month,
           COUNT(DISTINCT "Customer ID") AS active_cust,
           COUNT(DISTINCT Invoice)       AS orders,
           SUM(amount)                   AS gmv
    FROM dwd_trade_order
    GROUP BY month
)
SELECT
    month                                       AS 月份,
    active_cust                                 AS 活跃客户数,
    orders                                      AS 订单数,
    ROUND(1.0 * orders / active_cust, 3)        AS 人均订单数,
    ROUND(1.0 * gmv / orders, 2)                AS 客单价,
    ROUND(gmv, 2)                               AS GMV,
    ROUND(active_cust * (1.0 * orders / active_cust) * (1.0 * gmv / orders), 2) AS 分解校验GMV
FROM m
ORDER BY month;

-- 10) 环比 vs 同比：GMV 强季节性下环比失真，监控口径应改用同比
WITH m AS (
    SELECT month, SUM(amount) AS gmv FROM dwd_trade_order GROUP BY month
)
SELECT
    month                                            AS 月份,
    ROUND(gmv, 2)                                    AS GMV,
    ROUND(1.0 * gmv / NULLIF(LAG(gmv)      OVER (ORDER BY month), 0) - 1, 4) AS 环比,
    ROUND(1.0 * gmv / NULLIF(LAG(gmv, 12)  OVER (ORDER BY month), 0) - 1, 4) AS 同比
FROM m
ORDER BY month;

-- 11) 月度新客 / 老客 GMV 拆解（异动归因：定位下滑来自新客端还是老客端）
WITH first_purchase AS (
    SELECT "Customer ID" AS cust, MIN(month) AS first_month
    FROM dwd_trade_order
    GROUP BY "Customer ID"
),
um AS (
    SELECT DISTINCT d."Customer ID" AS cust, d.month
    FROM dwd_trade_order d
)
SELECT
    um.month                                                        AS 月份,
    CASE WHEN f.first_month = um.month THEN '新客' ELSE '老客' END  AS 客群,
    COUNT(DISTINCT um.cust)                                         AS 客户数,
    COUNT(DISTINCT o.Invoice)                                       AS 订单数,
    ROUND(SUM(o.amount), 2)                                         AS GMV,
    ROUND(1.0 * SUM(o.amount) / COUNT(DISTINCT um.cust), 2)         AS 人均GMV
FROM um
JOIN first_purchase f  ON um.cust = f.cust
JOIN dwd_trade_order o ON o."Customer ID" = um.cust AND o.month = um.month
GROUP BY um.month, 客群
ORDER BY um.month, 客群;
