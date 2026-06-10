# fmp-to-pg — 财报数据 + 一致预期 → PostgreSQL

并行于 `fmp-to-ddb/`(它管日 K 线)。本子项目管 **季度财报 + 一致预期 + 一致预期修正轨迹**。

## 架构(为什么 PG 而不是 DDB)

| 数据类型 | 落到哪 |
|---|---|
| 日 K 线 / 技术指标 / 因子 | DolphinDB(列存 + `mavg/mrsi` 几百倍快) |
| **季度财报 / 一致预期** | **PostgreSQL**(关系结构 + UPSERT + 修正轨迹) |

详见 `CLAUDE.md §🗄️`(DDB 约定)和 §💰(财报约定)。

## 4 张表

1. **`company`** — 公司画像 + **主题概念**(`themes` ARRAY)+ Non-GAAP 矩阵
2. **`corporate_action`** — 拆股 / Non-GAAP 定义变更 / 重述事件
3. **`earnings_quarter`** — 每季度一行,actual + consensus 并列,**生成列保证 `calendar_quarter` 和 `_current_basis` 永远正确**
4. **`consensus_history`** — append-only 时序,每次刷 FMP 都追加 snapshot

加 **`validation_log`** 审计所有 L1-L5 验证检查的历史。

## 数据正确性保证(分 5 层)

| 层 | 谁做 | 内容 |
|---|---|---|
| **L1 schema 约束** | PG | CHECK / UNIQUE / FK,impossible 值进不来 |
| **L2 数学不变式** | ingest | `bottomLineNetIncome ÷ 股数 ≈ epsDiluted`(±5%,归属普通股净利)/ Revenue YoY 在 [-50%, +400%](基数 < $20M 跳过)/ 拆股因子 > 0 |
| **L3 跨端点一致** | ingest | GAAP-only 公司的 `epsActual` == `epsDiluted`(非 GAAP 公司自动跳过) |
| **L4 日期/季度** | ingest | calendar_quarter 用独立算法重算对账 / 财季月份与公司财年偏移一致 |
| **L5 SEC EDGAR**(权威源) | 你本地跑 | 抽样 vs 10-Q XBRL,**金标准** |

**广义股票池策略(`--stocks`)**:L2 的 EPS/营收检查**降为 warn(不再中止整批)**,坏行照常入库并标记到 `data_quality_flags`(见下文「坏数据标记」)。其余 `error` 级(如 calendar_quarter 对账失败)仍会中止。`warn` 需 `--force-on-warn` 放行。

> company 表已从 Stocks.base 补齐 270 家(财年末月由 FMP Q4 利润表月份推导,`seed_company_from_base.py`);全量 dry-run 警告已从 907 降到 73(48 已标记坏数据 + 29 真实 YoY 异动 + 1 非 GAAP)。

## 跑

```powershell
# 0) 复制 .env
cp .env.example .env
# 编辑 .env,填入 PG 密码 + FMP_API_KEY

# 1) 连通性测试
python test_connect.py

# 2) 建库
psql ... -f schema/001_init.sql
psql ... -f schema/002_seed_mag7.sql
psql ... -f schema/005_add_data_quality_flags.sql   # 坏数据标记列

# 3) 干跑(只验证,不写)
python fetch_fmp_to_pg.py --mag7 --dry-run
python fetch_fmp_to_pg.py --stocks --dry-run --force-on-warn   # 全量(来自 Stocks.base)

# 4) 正式入库(验证通过才会写)
python fetch_fmp_to_pg.py --mag7
python fetch_fmp_to_pg.py --stocks --force-on-warn

# 5) 看看入了什么
python inspect_db.py
```

## 股票池来源(`--stocks`)

与 `fmp-to-ddb` 同源:`--stocks` 从 **Stocks.base**(`10_Stocks/Stocks/*.md` 的
`code`+`market`)读股票池,默认 `market==US`(财报/一致预期只对股票有意义,
加密/期货/港股跳过)。CLI:`--mag7` / `--stocks` / `--symbol AAPL` /
`--symbols AAPL,MSFT` / `--markets US` / `--years N` / `--dry-run` / `--force-on-warn`。

- 不在 `company` 表的标的用**默认元数据**(财年末月=Dec);过去季度的财年/财季直接
  取自利润表,不受影响,只有远期推导/Non-GAAP 口径走默认(标记为 warn)。
- `.env` 从脚本目录**向上逐级查找**,放仓库根目录即可。

## 坏数据标记(`data_quality_flags`)

广义股票池有一条长尾的 FMP 真实数据异常(SPAC/矿企/次新股的摊薄股数 vs EPS
口径不一致、个别负营收)。策略是**不阻断整批**:这些行照常入库,但在
`earnings_quarter.data_quality_flags`(`schema/005`)里标明触发了哪些校验。

- EPS 对账改用 `bottomLineNetIncome`(归属普通股净利,已扣优先股/少数股东),
  而非总 `netIncome` —— 修复 ALB 这类复杂资本结构的误报。
- `l2_ni_eq` / `l2_revenue_nonneg` 降为 **warn**(不再 error 中止);近零 EPS 的
  相对误差噪声为 info,不标记。负营收违反 PG L1 CHECK,入库前置 NULL 并标记。
### 查坏数据

```sql
-- 1) 列出所有被标记的行 + 触发了哪些校验
SELECT symbol, quarter_label, calendar_quarter, data_quality_flags
FROM   earnings_quarter
WHERE  data_quality_flags <> '{}'
ORDER  BY symbol, period_end;

-- 2) 按标记类型统计(unnest 数组)
SELECT flag, COUNT(*) AS rows
FROM   earnings_quarter, unnest(data_quality_flags) AS flag
GROUP  BY flag ORDER BY rows DESC;

-- 3) 只看某一类(如 EPS 对不上的)
SELECT symbol, quarter_label, gaap_ni, gaap_eps_diluted_as_reported, dil_shares_as_reported
FROM   earnings_quarter
WHERE  'l2_ni_eq_eps_x_shares' = ANY(data_quality_flags);

-- 4) 干净数据视图(下游分析只取没标记的)
SELECT * FROM earnings_quarter WHERE data_quality_flags = '{}';

-- 5) 全量审计轨迹(每条校验的历史,含 info/warn/error)
SELECT record_key, check_name, severity, observed_value
FROM   validation_log WHERE NOT passed ORDER BY validated_at DESC;
```

> 实测全量 `--stocks --years 2 --dry-run --force-on-warn`:270/270 抓取,**0 error**,
> 3472 行,**48 行标记**(42 EPS 口径差 + 6 负营收),未写库。

## calendar_quarter 是怎么算的

```sql
calendar_quarter GENERATED ALWAYS AS (
    'Q' || EXTRACT(QUARTER FROM (period_end - INTERVAL '1 month')) || ' ' ||
    EXTRACT(YEAR FROM (period_end - INTERVAL '1 month'))
) STORED
```

把每个财季按"期末月 −1 月"落到一个标准化日历季度桶,**跨公司财年偏移不影响对齐**:

| Symbol | Quarter | period_end | calendar_quarter |
|---|---|---|---|
| AAPL | FQ3 2025 | 2025-06-28 | **Q2 2025** |
| MSFT | Q4 FY25 | 2025-06-30 | **Q2 2025** ✓ 同档 |
| TSLA | Q2 2025 | 2025-06-30 | **Q2 2025** ✓ 同档 |
| NVDA | Q2 FY26 | 2025-07-27 | **Q2 2025** ✓ 也进 Q2 |
| NVDA | Q4 FY26 | 2026-01-25 | **Q4 2025** |
| AAPL | FQ1 2026 | 2025-12-27 | **Q4 2025** ✓ 同档 |

## 拆股安全

每行有 `cumulative_split_factor`。`_as_reported` 列存当年 10-Q 原始值;`_current_basis` 列由 DB 自动算成今日股数基准。新拆股发生时,只需:

```sql
-- 把所有受影响的历史季度乘上拆股比
UPDATE earnings_quarter
SET cumulative_split_factor = cumulative_split_factor * 10
WHERE symbol='NVDA' AND period_end < '2024-06-10';
-- _current_basis 列自动重算,无需手工更新
```

## 你"几天后要发"的工作流

```sql
SELECT symbol, quarter_label, calendar_quarter, report_date,
       revenue_consensus, ng_eps_consensus_as_reported, num_analysts_eps,
       consensus_last_refreshed
FROM earnings_quarter
WHERE actuals_filing_date IS NULL
  AND report_date BETWEEN current_date AND current_date + 7;
```

每天定时跑 `fetch_fmp_to_pg.py --mag7` 就会刷新这些行的 consensus 列(同时往 `consensus_history` append 当天 snapshot)。

## 注意

- 容器里跑不了实际入库测试(连不到你 `localhost:5433`)
- L5 SEC EDGAR 验证也要你本地跑(`sec.gov` 在容器里被屏)
- 容器侧已验证:`validate.py` 自带 self-test 全过(包括故意错配的负向测试)
