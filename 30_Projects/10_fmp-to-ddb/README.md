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
python fetch_fmp_to_ddb.py --mag7 --years 10

# 2) 正式写入 DDB
python fetch_fmp_to_ddb.py --mag7 --years 10 --save-ddb

# 3) 读回校验
python verify_after_insert.py
```

CLI 兼容老 Polygon 脚本:`--symbol AAPL` / `--symbols AAPL,MSFT` / `--mag7` / `--years N` / `--from-date` / `--to-date` / `--save-ddb` / `--ensure-schema`(表缺失才建)。

## 离线调试(不连 DDB)

```powershell
# 把拉取+变换结果落 JSON,先肉眼看字段对不对,完全不碰 DDB
python fetch_fmp_to_ddb.py --symbol AAPL --years 1 --dump-json aapl_staging.json
```

## 注意

- **不会重建你的表**:`load_and_compute.dos` 只 append 到现有表;`ensure_schema.dos` 仅当表不存在时建(你的表已存在 → no-op)。
- **幂等**:重复跑同一批,会先按 symbol 删除该批日期范围再 append,不会重复。
- **容器跑不了**:这套必须在 DDB 所在机器(你本地)跑;开发容器连不到你的 DDB。
