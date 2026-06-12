---
name: deep-dive
description: Comprehensive deep research on a stock/ticker. Use when the user wants an in-depth, structured investment memo on a company — business model, industry/competitive structure, moat, deep financials, management, growth vectors, bear/risk matrix, valuation with scenarios, and a falsifiable thesis. Pulls from curated INDEPENDENT specialist research (SemiAnalysis, MBI, Speedwell, Stratechery, market-research firms, skeptic/short sources) PLUS primary data (FMP financials/segments/balance-sheet/cashflow/estimates/transcripts/filings, local DolphinDB price, PostgreSQL earnings). Output = the full 13-section report (see report-template.md), NOT a summary — with 3 mandatory slots (bear/risk, valuation+scenarios, falsifiable thesis) and a 3-tier verdict. Invoke with a ticker, e.g. "/deep-dive INTC".
---

# Stock Deep-Dive Research

**Core principle: information value ∝ 1 / propagation-distance.** A view that
reaches a group chat or a bulge-bracket note has already moved the price. Work the
**upstream** layers — primary data and independent specialists — which lead
consensus by weeks to months. The goal is not a summary; it is a **deep,
structured, falsifiable investment memo** you could have formed before consensus
crystallized.

**Output = the full structured report in [report-template.md](report-template.md)
(13 sections), NOT a rough summary.** Voice & "moves" → [example-LITE.md](example-LITE.md).
Source catalog → [sources.md](sources.md). Pick the clusters matching the sector;
always include the **primary** (H) and **skeptic** (I) layers.

---

## Procedure (given a TICKER)

Gather first (don't write until you have the numbers), then synthesize, then fill
EVERY section of report-template.md. The gather blocks map to report sections (§).

### 0 · Identify & scope
- FMP `company/profile-symbol` + `company/peers` → sector, industry, core business, peers, IPO date, market cap.
- Pick source clusters from `sources.md`. Note paywalls — fall back to press releases, podcast notes, X threads, conference talks, search snippets.

### 1 · Primary financials (FMP) — fills §1 §5 §9
- `statements/income-statement` (annual ×5) + `income-statement-growth` + quarterly (×6) → revenue & margin trajectory
- `statements/balance-sheet-statement` → cash, debt, net cash, share count (dilution/buyback)
- `statements/cashflow-statement` → operating CF, capex intensity, buybacks/dividends, M&A
- `statements/revenue-product-segmentation` + `revenue-geographic-segments` → mix shift, customer/geo concentration
- `statements/key-metrics(-ttm)` + `metrics-ratios(-ttm)` → ROIC/ROE, margins, multiples
- `statements/enterprise-values` → EV multiples; `financial-scores` → Piotroski/Altman quick read
- `analyst/financial-estimates` (annual ×3-4) + `price-target-summary` + `grades` + `historical-grades` → forward expectations + consensus drift (anchors §9 scenarios & reverse-DCF)

### 2 · Business, management, customers — fills §1 §6
- `company/company-executives` + `company/shares-float` (insider %, float) → §6
- `earningsTranscript/search-transcripts` (latest 2-3) → management's OWN framing, guidance, segment/customer color
- `secFilings` → 10-K business section + **risk factors**; 8-K material events; S-1 for recent IPOs

### 3 · DEEP-MINE the source catalog (first-class step, NOT a single search) — fills §2 §3 §4 §8
FMP gives the numbers; **the catalog gives the insight**. Open [sources.md](sources.md),
select every relevant cluster (sector A–G + general C/D + primary H + skeptic I),
then mine them **source by source**:

1. **Targeted per-source queries**, not one generic search. For each selected
   source run `site:`-scoped searches, e.g.:
   - `site:semianalysis.com <ticker|company|tech-theme>` · `site:morethanmoore.substack.com <company>`
   - `site:mbi-deepdives.com <company>` · `site:speedwellmemos.com <company>` · `site:stratechery.com <company>`
   - market-research firms (F): `<LightCounting|TrendForce|Dell'Oro|Mercury> <market> share forecast`
   - supply chain (G): `site:digitimes.com <company/supplier>` — order/utilization checks lead earnings
   Also search the company's **key tech/industry theme**, not just the ticker —
   specialists often cover the theme without naming the stock.
2. **WebFetch the full text** of the highest-signal hits — *minimum 5-8 full
   articles* for a default deep-dive (snippets are not research). Paywalled →
   fetch the free portion / podcast notes / author's X thread, and mark it.
3. **Triangulate**: where independents disagree with each other or with sell-side,
   say so explicitly — disagreement locates the variant perception (§10).
4. **Log the yield**: §12 must list which catalog sources were consulted and what
   each contributed (or "checked, nothing relevant"). An empty trawl is a finding.

### 4 · Skeptic / bear (MANDATORY) — fills §8
- Mine layer (I) the same way: `site:thebearcave.substack.com <company>`, short-shop reports (Muddy Waters/Kerrisdale/Spruce Point/Hunterbrook), SA bear articles + an explicit "bear case / what breaks this" query
- IPO < ~12mo → read the S-1/F-1 **Risk Factors** (richest bear source); pull short interest if relevant

### 5 · Price & catalysts (local, if reachable) — fills §5 §11
- DolphinDB `dfs://market_daily/us_daily_kline` → trend legs (zigzag, threshold rel. to own vol) + biggest **idiosyncratic** up/down days (stock pct − index pct) = catalyst days
- PostgreSQL `earnings_quarter` → the **beat/miss table** (§5, mandatory when PG reachable): last 8-10 quarters of `revenue_actual` vs `revenue_consensus` (unit = millions) and `ng_eps_as_reported` (fallback `gaap_eps_diluted_as_reported`) vs `ng_eps_consensus_as_reported` → surprise %. **Cross-join each report_date with DDB next-day idiosyncratic move** — that maps the market's error-tolerance for this name (feeds §9's priced-for-perfection read). `consensus_history` → how estimates drifted into the print.
- (cloud agents can't reach localhost DBs — user runs locally; fallback = FMP earnings surprises / transcripts)

### 6 · Synthesize — the 6-action method (this produces depth)
1. **Decompose** the product to the value-chain layer where SCARCITY sits.
2. At that layer: **who can / can't make it, and why** (yield/IP/lock-in/capital/license).
3. Does the secular wave **deepen or erode** THIS moat?
4. **Adversarially hunt the strongest bear / erosion path** (quantified, with a timeline).
5. **Peg every claim to a number.**
6. **Map to an analog pattern.**

### 7 · FILL report-template.md fully, then maintain the catalog
- Write all 13 sections per the **Depth contract** below. Cite sources inline.
- If this run surfaced a genuinely high-signal source not in `sources.md`, append it (right category, quality caveat, `*(found via <TICKER>)*`). Keep junk out.

---

## Depth contract (non-negotiable — this is what "not a summary" means)
- **Every section: named specifics + numbers.** Fill every table the template shows. "It has a moat" is banned; "唯一量产 200G EML、份额 50-60%" is the bar.
- **The THREE 🔒 are mandatory and complete**: §8 bear/risk matrix (with quantified erosion path + moat-metric slope), §9 valuation with a bull/base/bear **scenario table**, §10 falsifiable thesis (predictions carry a number or timeline) + **variant perception**.
- **Reverse-engineer what's priced in** (§9) — don't just report the multiple; say what growth/margin it implies.
- **State your edge or admit you have none** (§10): where do you differ from consensus and why? If nowhere, say "organizing consensus, not edge."
- **Source mix is mandatory, not optional**: FMP alone is NOT a deep-dive. Default = ≥5-8 full articles fetched from catalog sources across ≥3 categories (incl. skeptic I), logged per-source in §12. The qualitative spine (§2 §3 §4 §8) must rest on independent/primary sources, with FMP supplying the numbers.
- **Mark every unknown / paywalled / unverified honestly. Never fabricate a number.** Partial data → say so and reason from what you have.
- Scale effort to the ask: a quick check can be lighter, but "deep-dive" defaults to the full 13 sections.

## Style & quality bar
- **Respond in the user's language** (default Chinese). Punchy, structured, headers + tables, sources inline. End with a next-step question.
- **Lead with a "不是 X,而是 Y" reframe** — the decompose-to-scarcity move is the spine; put it in §0/§1.
- **The 🔒 honest soft-spot is the climax, not a disclaimer.** Quantify the erosion path; check the moat metric's *slope*, not just a snapshot.
- **Always file a verdict — one of THREE tiers**: ① window-open (mispriced opportunity, LITE) · ② window-closed (great-but-priced, ARM/COST) · ③ unproven·high-variance (lottery — unproven moat + binary outcome + price already assuming the win, CBRS).
- **Insight over balance.** A sharp falsifiable thesis, not a two-handed summary. No real moat → say so plainly; its predictions will be failing.

## Principles
- **Go upstream.** Prefer primary (transcripts, filings, segment data) and independent specialists over aggregated sell-side/media. A wave of bulge-bracket notes converging = a *consensus thermometer* (window closing), not a buy signal.
- **The bear case is mandatory.** A deep-dive without its strongest disconfirming evidence is a sales pitch. The three 🔒 slots block both confabulating a bull case for a weak company AND recommending a great-but-fully-priced one.
- **Depth is capped by source access × consensus-saturation.** Under-covered / non-consensus names → real edge possible; fully-covered names → you mostly organize consensus, so lean into the honest 机会-vs-好公司 verdict. Say which you're doing.
- **Falsifiability is the interface to the live stream.** §10's predictions become the lens that turns future news/earnings into confirm/deny votes (see the moat-thesis-engine design in memory; output drops into the 护城河论点 Obsidian template).
- **Be honest about access + uncertainty.** Mark paywalled/unfound sources; earliest-layer (first-principles) reads are the least certain — say so.
