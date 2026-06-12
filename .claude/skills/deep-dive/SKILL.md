---
name: deep-dive
description: Comprehensive deep research on a stock/ticker. Use when the user wants in-depth analysis of a company — its core product, competitive moat, industry supply/demand & competitive landscape, and bull/bear thesis. Pulls from curated INDEPENDENT specialist research (SemiAnalysis, MBI Deep Dives, Speedwell, Stratechery, sector market-research firms, skeptic/short sources) PLUS primary data (FMP financials/segments/estimates/transcripts/filings, local DolphinDB price, PostgreSQL earnings). Produces a moat-thesis card with a MANDATORY bear case + falsifiable predictions, a dynamic driver/milestone/catalyst stream, and a forward catalyst calendar. Invoke with a ticker, e.g. "/deep-dive INTC".
---

# Stock Deep-Dive Research

**Core principle: information value ∝ 1 / propagation-distance.** A view that
reaches a group chat or a Bank-of-America note has already moved the price. This
skill deliberately works the **upstream** layers — independent specialists and
primary data — which lead the bulge-bracket/consensus by weeks to months. The
goal is not a summary; it is a **falsifiable moat thesis** you could have formed
before consensus crystallized.

Full source catalog: see [sources.md](sources.md). Pick the clusters that match
the company's sector; always include the **primary** (H) and **skeptic** (I) layers.

---

## Procedure (given a TICKER)

### 1 · Scope
- FMP `company/profile-symbol` + `company/peers` → sector, industry, core business, peers.
- Pick matching source clusters from `sources.md` (e.g. semis → A; optical → B;
  general → C; biotech → E). Note paywalls — fall back to press releases, podcast
  notes, X threads, and search snippets.

### 2 · Gather PRIMARY data (the numbers — lead sell-side)
- FMP `statements/revenue-product-segmentation` (where does revenue actually sit + which segment is inflecting)
- FMP `statements/income-statement` + `income-statement-growth` (revenue/margin trajectory)
- FMP `analyst/financial-estimates` + `analyst/price-target-summary` + `analyst/grades` (forward expectations + consensus drift)
- FMP `earningsTranscript/search-transcripts` (latest 2–3 — management frames the thesis in its OWN words first)
- FMP `secFilings` → 10-K business section + risk factors; 8-K for material events
- Local DBs **if reachable** (user runs locally; cloud agents cannot reach localhost):
  - DolphinDB `dfs://market_daily/us_daily_kline` → trend legs (zigzag, threshold rel. to own vol) + biggest **idiosyncratic** up/down days (stock pct − index pct) = catalyst days
  - PostgreSQL `earnings_quarter` / `consensus_history` → earnings surprises + consensus history
- For demand-side context, pull the customers'/hyperscalers' capex commentary too.

### 3 · Gather INDEPENDENT specialist research (the views — lead sell-side)
- WebSearch the curated sources in `sources.md` for `<ticker>` + its key industry themes.
- WebFetch the 3–6 highest-signal deep pieces (specialist substacks, market-research summaries, conference talks).
- **MANDATORY: also search the skeptic/short layer (I)** and the bear case — this feeds the bear slot below and guards against a one-sided bull narrative.

### 4 · Synthesize — the 6-action method (this is what produces depth)
1. **Decompose** the product down to the value-chain layer where SCARCITY actually sits (not "it makes chips" — *which* component, *which* layer is hard).
2. At THAT layer: **who can / can't make it, and why** (yield/process/IP? ecosystem lock-in? capital? license?).
3. Does the secular wave **deepen or erode** THIS specific moat as the industry roadmap advances?
4. **Adversarially hunt the strongest bear case / erosion risk** (who could break it, how, how soon).
5. **Peg every qualitative claim to a number** (segment revenue, share %, margin trend, customer commitment, capex).
6. **Map to an analog pattern** (a known structural playbook).

### 5 · Output — fill the template below
Cite sources inline as markdown links. State access limits honestly (paywalled / not found).

### 6 · Maintain the catalog (self-improving — do this every run)
If this run surfaced a **genuinely high-signal** source not already in
[sources.md](sources.md), append it under the right category with a one-line note,
a quality caveat if needed (retail / AI-syndicated / paywalled → verify), and a
`*(found via <TICKER>)*` tag. Keep junk out (generic media, content farms). The
catalog should get sharper with every deep-dive.

---

## Output — match the GOLDEN EXAMPLE

The target quality bar is [example-LITE.md](example-LITE.md). **Read it first** —
reproduce its *moves and shape*, not its content. The flow:

```
# <TICKER> 真正的核心产品与护城河:不是 <表层>,是 <真正的稀缺层>
**关键认知**: 谁做不了 / 做不了什么 / 为什么(named competitors + named component + mechanism)
- 份额/地位数字 · moat 本质(yield/IP/lockin/capital/license)+ 切换成本
- 行业浪 × 方向:这条浪让 moat 变深还是变浅?

## 锚定客户验证(+ 类比范式)
  <anchor-customer commitment> — 和 <analog playbook> 同款

## 财报"兑现"了什么(分部/毛利数据钉论点)
  <a TABLE: segment revenue / margin / growth showing the thesis cashing in>

## 🔒 最有价值的诚实点:moat 的可证伪软肋   ← THIS IS THE CLIMAX, never skip
  <strongest named bear/erosion risk — name it, then QUANTIFY the erosion path: how much share / margin at risk, on what timeline>
  > 可证伪预言:…(give it a NUMBER or a TIMELINE, not just a qualitative event)
  > 证伪触发:…
  > moat 趋势核验:那条核心 moat 指标(份额/良率/续费率/混合占比)本季在改善还是恶化?（查斜率,不只快照)

## 🔒 共识 + 估值位置(必答:这是「论点机会」还是「只是只好公司」?)
  - 共识阶段:论点处在 contrarian/forming → consensus → 已结晶? (依据:卖方目标价漂移、被写烂程度、深度研究是否已遍地)
  - 估值位置:核心倍数 vs 自身历史分位 + 隐含 price-in 了什么增速/完美度
  - 🎯 不对称裁决(三选一,必须明确归档):
    ① 窗口【开】= 论点机会:论点先于共识 + 估值便宜 + 生意已可证 → 不对称上行(LITE 型)
    ② 窗口【关】= 好公司≠好买点:护城河已证,但已 price-in 完美 → 窄区间,涨幅有限(ARM/COST 型)
    ③ 【未证·高方差】= 彩票:护城河未证 + 结局二元(全押少数开关)+ 价格已假设中奖 →
       结局极宽(可能数倍 / 可能 -70%+),不是被错杀的不对称机会;若参与只能当小仓位投机,
       严禁把热门 IPO 的兴奋误当成"窗口开"的便宜(CBRS 型)
    ⚠️ moat-赢 ≠ 股票-赢:估值已满时,即便所有预言兑现,股票仍可能因倍数压缩而平庸

## 接口 / 框架落点
  这条预言如何把未来新闻/财报变成对 moat 的"确认票/否决票";
  + 这论点最早什么时候、从哪层能立住(early-detection value)

## Part 2 · 动态流 + 🔭前瞻日历  (drivers / milestones / past catalysts+price-reaction / forward calendar)

下一步: <一个犀利的、推进性的问题>
来源: <inline links> + 标注付费墙/没拿到的
```

## Style & quality bar (what makes the output GREAT, not just correct)
- **Respond in the user's language** (default Chinese for this user). Punchy, structured, headers + a table, sources inline.
- **Lead with a "不是 X,而是 Y" reframe** — the decompose-to-scarcity move is the whole insight; put it first.
- **Be specific or say nothing**: name the component, the competitor, the process, the number. "It has a moat" is banned; "唯一量产 200G EML、份额 50-60%" is the bar.
- **Every qualitative claim carries a figure.** Always include the financial cash-in table.
- **The 🔒 honest soft-spot is the climax, not a disclaimer.** Frame it as "最有价值的诚实点"; it MUST carry a falsifiable prediction + invalidation trigger (this is also the anti-confabulation guard). Quantify the erosion path (how much / how soon), and check the moat metric's *slope*, not just a snapshot.
- **Always file a verdict — one of THREE tiers, never fudge it.** ① window-open (mispriced opportunity, LITE) · ② window-closed (great-but-priced, ARM/COST) · ③ unproven·high-variance (a lottery — unproven moat + binary outcome + price already assuming the win, CBRS). The 🔒 consensus+valuation slot is mandatory. Operationalizes alpha = the gap between fundamental-visible (①) and consensus-crystallized (②); tier ③ is when the moat itself isn't yet provable so there's no gap to price — only a bet.
- **Insight over balance.** This is a sharp falsifiable thesis, not a two-handed summary. If the company has no real moat, say THAT plainly (its predictions will be failing) — don't manufacture one.
- **End with a next-step question.**

---

## Principles
- **Go upstream.** Prefer primary (transcripts, filings, segment data) and
  independent specialists over aggregated sell-side/media. Treat a wave of
  bulge-bracket notes converging as a *consensus thermometer* (window closing),
  not as a buy signal.
- **The bear case is mandatory.** A deep-dive without its strongest disconfirming
  evidence is a sales pitch, not research. The THREE 🔒 slots — bear/erosion,
  falsifiable predictions, and consensus+valuation position — are non-negotiable.
  They block confabulating a bull case for a weak company AND block recommending a
  great-but-fully-priced one.
- **Depth is capped by source access × consensus-saturation.** On under-covered /
  non-consensus names the skill can produce real edge; on fully-covered consensus
  names it mostly organizes consensus — there the most valuable output is the
  honest 机会-vs-好公司 verdict, so lean into that. Say when you're reproducing
  consensus rather than finding edge.
- **Peg to numbers.** Every qualitative claim gets a figure or it's a vibe.
- **Falsifiability is the interface to the live stream.** The predictions you
  write become the lens that makes future news/earnings interpretable as
  confirm/deny votes (see the moat-thesis-engine design in memory).
- **Be honest about access + uncertainty.** Mark paywalled/unfound sources; the
  earliest-layer (first-principles) reads are the least certain — say so.
