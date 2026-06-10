# 10_fmp-to-ddb — DolphinDB connectivity test (step 1)

Step 1 of the FMP → DolphinDB pipeline: just prove your machine can talk to your DDB cluster. No FMP yet.

## Setup(<1 分钟)

`.env` / `.env.example` 现在放在**仓库根目录**(`ai-trading-platform/`),不在本项目文件夹里。脚本会从自身目录**向上逐级查找** `.env`,所以放根目录即可被自动加载。

```bash
# 在仓库根目录执行
cp .env.example .env
# 编辑 .env 填入你的 DDB host/port/账号
```

## 跑

```bash
python test_connect.py
```

第一次会自动 `pip install dolphindb`(已装则跳过)。

## 你会看到什么

每个节点(Controller / Data / Compute)分别打印:

```
── Data node  localhost:8902 ──
OK   TCP + auth ok
OK   DDB version: 3.00.4 2025.xx.xx
OK   node alias: dnode1
OK   controller alias: ctl1
OK   compute roundtrip works (sum(1..5)=15)
     DFS databases: ['dfs://market_daily', ...]
```

最下面有汇总。**只要 Data node 是绿的,后面 FMP → DDB 这条链就能走通**(Controller 用于集群管理,Compute 是可选)。

## 失败排查

| 现象 | 检查 |
|---|---|
| `connect() returned False` | host/port 是否正确;DDB 进程是否在跑(`getNodeAlias()` 在 web 控制台能跑就证明 DDB 自己 OK) |
| `Failed to connect ... error code 111` | TCP 拒绝,大概率端口写错 |
| `Authentication failed` | user/password 不对 |
| `UnicodeDecodeError: 'gbk' codec` | 你 `.env` 文件被 Windows 用 GBK 保存了。VSCode 右下角改成 UTF-8 重存 |

跑通后告诉我,我开下一步:**建 DFS 表 schema + FMP 单 ticker 试拉一段写进去**。

---

# 完整管线:FMP → DolphinDB(MAG7 / 10年 / 股息复权 / 含均线)

架构(遵循 CLAUDE.md §🗄️):**Python 只拉取 + 编排,所有列计算在 DolphinDB 里跑**。

```
fetch_fmp_to_ddb.py        Python: REST 拉 FMP + 插值股数 + 上传 staging 表
dos_scripts/
  ensure_schema.dos        建表(已存在则 no-op)
  load_and_compute.dos     算 pct_change/turnover/market_cap/MA20-250 + 幂等写入
verify_after_insert.py     读回校验(AAPL 锚点 + MA 覆盖 + 分区方案)
```

## 字段口径

- **价格**:`/historical-price-eod-dividend-adjusted`(拆分+股息双复权 = Yahoo Adj Close)
- **pct_change**:`(close/prevClose - 1) × 100`,百分制 2 位(close-to-close,**不取** FMP changePercent)
- **turnover**:`close × volume`(div-adj 端点无 vwap,降级走 close)
- **market_cap**:`close(adj) × 插值股数` —— ⚠️ 这是**总回报口径市值**(用复权价算),内部一致但**不等于当年真实市值头条数**。要真实市值需另拉原始价。
- **ma20/30/60/120/250**:DDB 原生 `mavg(close, N)`,3 位小数,窗口不足留 NULL

## 跑

```powershell
# 0) 先确认表是空的(可选)
python inspect_db.py

# 1) 干跑预览(不写库,先看拉下来多少行)
python fetch_fmp_to_ddb.py --stocks --years 10

# 2) 正式写入 DDB —— 全量来自 Stocks.base(默认 markets=US,HK,Crypto,Futures)
python fetch_fmp_to_ddb.py --stocks --years 10 --save-ddb

# 单独某市场 / 某标的
python fetch_fmp_to_ddb.py --stocks --markets Crypto --years 10 --save-ddb
python fetch_fmp_to_ddb.py --symbol AAPL --years 10 --save-ddb

# 3) 读回校验
python verify_after_insert.py
```

CLI:`--stocks`(从 Stocks.base 读全量股票池)/ `--markets US,HK,Crypto,Futures` / `--symbol AAPL` / `--symbols AAPL,MSFT` / `--years N` / `--from-date` / `--to-date` / `--save-ddb` / `--ensure-schema`(表缺失才建)。

**股票池来源 = Stocks.base**(`10_Stocks/Stocks/*.md` 的 `code`+`market`),四类市场全部走同一股息复权端点:
- `US` → 直接抓;`HK` → 抓 `<code>.HK`、存 4 位 `code`。
- `Crypto` → 抓 `<code>USD`、存裸 `code`(BTC/ETH);市值用 `cryptocurrency-list` 的 `circulatingSupply`(当前流通量,近似常量)× close。
- `Futures` → 显式映射 `ESmain→ESUSD / NQmain→NQUSD / YMmain→YMUSD / MGCmain→GCUSD / SILmain→SIUSD`,存库里 code;**market_cap=0**(无流通量概念)。
- 所有非美股的 `db_symbol` 都用库里 code,与 Base 对齐。

> ⚠️ 期货连续合约口径:FMP 的 `ESUSD` 等是连续序列(2007 至今),但是否做了移仓回调(back-adjust)未确认;若未回调,换月处会有跳空,影响跨月均线连续性。

## 离线调试(不连 DDB)

```powershell
# 把拉取+变换结果落 JSON,先肉眼看字段对不对,完全不碰 DDB
python fetch_fmp_to_ddb.py --symbol AAPL --years 1 --dump-json aapl_staging.json
```

## 注意

- **不会重建你的表**:`load_and_compute.dos` 只 append 到现有表;`ensure_schema.dos` 仅当表不存在时建(你的表已存在 → no-op)。
- **幂等**:重复跑同一批,会先按 symbol 删除该批日期范围再 append,不会重复。
- **增量安全的均线**:每个标的写入前,会先从 DFS 表读取该批起始日**之前**的最多 250 根 close 作为预热窗口,拼接后再算 `pct_change` / `mavg`,然后只写回本批日期范围。所以增量更新(只追加最近几天)时,均线会读取历史正确计算,批次边界不会出现断层。
  - 仅追加新数据用增量即可:`python fetch_fmp_to_ddb.py --symbol AAPL --from-date <上次最后日+1> --save-ddb`。
  - **注意复权口径**:预热用的是库里已存的(旧复权基准)close,而新批是最新复权 close。若两次抓取之间发生分红/拆股,接缝处均线会有极小偏差;日常每日增量可忽略,要彻底重新对齐就整段重抓一次。
- **容器跑不了**:这套必须在 DDB 所在机器(你本地)跑;开发容器连不到你的 DDB。

## 踩坑记录(Gotchas)

实战中踩过的坑,按"FMP 符号 / DolphinDB / 数据质量 / 流程"分类。

### FMP 符号与端点
- **加密货币 FMP 是有的**,但符号是 `<币>USD`(`BTCUSD`/`ETHUSD`);**裸 `BTC` 会命中一个同名股票**(完全不同的价格),`BTCUSDT` 返回 0。
- **港股**用 `<4 位 code>.HK`(`0700.HK`);`00700.HK`(5 位)和裸 `0700` 都返回 0。
- **期货也在 FMP**,但**不是统一后缀**:`ESmain→ESUSD`、`NQmain→NQUSD`、`YMmain→YMUSD`、`MGCmain→GCUSD`、`SILmain→SIUSD`(`SIL→SI`、`MGC→GC`),必须用**显式映射表**。
- **四类市场共用同一个**股息复权端点(`historical-price-eod/dividend-adjusted`),都返回 `adjOpen/High/Low/Close`,所以 `build_staging_rows` 不用按市场分支。
- **入库 `symbol` 一律用库里 code**(`0700`/`BTC`/`ESmain`),不是 FMP 抓取符号 —— 这样 DDB 与 Stocks.base 对齐。
- **港股前导零**:Base note 的 `code` 必须是带引号字符串(`"0700"`),否则 YAML 会把 `0700` 当**八进制**、`0388` 丢前导零。早期还混入过 5 位 `00388` 的旧写法,导致重复分区。
- **`GOOG` vs `GOOGL`**:跟踪的是 `GOOG`(谷歌 C);`GOOGL`(谷歌 A)是不同标的,别混。
- **FMP 单票数据有滞后**:个别票(如 GOOGL/PSTG)末日会比大盘晚 1-2 个交易日;过一天或下次增量自然补上,不是 bug。

### DolphinDB
- **`mavg`/`pct_change` 必须带历史预热**:只对上传批次算,增量更新会在批次起点 null 掉均线。解法见上面「增量安全的均线」——先从 DFS 读批次前最多 250 根 close。
- **建空表容量不能为 0**:`table(0:0, ...)` 报错,要写 `table(1:0, ...)`(容量 1、行数 0)。
- **没有 `count(distinct col)`**:用 `size(exec distinct col from t)` 或子查询。
- **删空分区/清理分区域**:VALUE 分区删数据块后,**值名仍留在分区域(schema)里**。`dropPartition(db, paths)` 删不掉值名;要传第 4、5 个参数 **`forceDelete=true, deleteSchema=true`** 才能连值名一起清:
  `dropPartition(database(DB), ["GOOGL"], "us_daily_kline", true, true)`。
  完整签名:`dropPartition(dbHandle, partitionPaths, [tableName], [forceDelete], [deleteSchema])`。
- **⚠️ Windows 大小写不敏感 + symbol VALUE 分区会互相误伤**:磁盘上 `ESMAIN` 和 `ESmain` 是**同一个目录**。删一个空的大写 `ESMAIN` 分区(deleteSchema),会把有数据的小写 `ESmain` 分区的**列文件一起删掉**(踩过:5 个期货的 ma* 列文件被清,OHLCV 侥幸残留)。后果是 `select ma60 ...` 报 `Cannot open file ...ma60.col`。**修复**:重抓该 symbol(`--stocks --markets Futures --save-ddb`),幂等 delete+append 会重建列文件。**预防**:别在 Windows 上 dropPartition 仅大小写不同的 symbol;要清理大写残留前先确认它和小写不共用目录。

### 数据质量(源头噪声,非管线 bug)
- **期货 close 可能越界 [low,high]**:FMP 期货 OHLC 用结算价,close 可略超当日高/低几个点(实测 YMmain 有十几行)。校验时给这类留容差。
- **小盘妖股单日 >50%**:真实波动(LIDR/QSI/ASTS/SOUN 等),别误判为脏数据。
- **加密/期货无股数**:加密市值用 `circulatingSupply`(近似常量)× close;**期货 market_cap=0**。

### 流程
- **后台跑批看不到中途日志**:Python stdout 重定向到文件时是**块缓冲**,进程结束才 flush;别因为日志空就以为没动。
- **Bash 跑 DolphinDB 语句注意反引号**:DDB 的 `` `AAPL `` 反引号会被 bash 当命令替换,写复杂查询用 `.py` 脚本文件而不是 `python -c "..."`。
- **对齐全量用显式统一窗口**:`--from-date <早于现有最早日> --to-date <今天>`,让幂等删除覆盖旧行、所有标的同窗口替换;同市场内即对齐,跨市场差异是真实交易日历差(加密 7×24)。
