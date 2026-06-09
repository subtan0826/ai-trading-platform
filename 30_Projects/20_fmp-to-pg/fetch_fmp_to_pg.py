"""
fetch_fmp_to_pg.py — pull MAG7 earnings + consensus from FMP, write to PG.

Architecture:
  1. Fetch /calendar/earnings-company  -> actuals + consensus
  2. Fetch /statements/income-statement (quarterly) -> GAAP NI/EPS/shares
  3. Match earnings and income-statement rows by date window
  4. Compute cumulative_split_factor from corporate_action table
  5. Run validate.run_all_checks() on EVERY row BEFORE insert
  6. If ANY check has severity='error' and fails -> ABORT, do not write
  7. UPSERT into earnings_quarter + INSERT into consensus_history
  8. Print a per-row validation summary + persist to validation_log

CLI:
    python fetch_fmp_to_pg.py --mag7
    python fetch_fmp_to_pg.py --symbol AAPL --years 5
    python fetch_fmp_to_pg.py --mag7 --dry-run    # print, don't write
    python fetch_fmp_to_pg.py --mag7 --force-on-warn   # write even on L2 warns
"""
import argparse
import datetime as dt
import json
import os
import re
import sys
import time
from collections import Counter
import urllib.request
import urllib.parse
import urllib.error
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# Lazy imports for psycopg (only needed when writing)
def _ensure_psycopg():
    try:
        import psycopg
        return psycopg
    except ImportError:
        import subprocess
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-q",
                               "psycopg[binary]"])
        import psycopg
        return psycopg


from validate import run_all_checks, ValidationReport, MONTH_TO_QUARTER

FMP_BASE = "https://financialmodelingprep.com/stable"
MAG7 = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA"]

# Checks whose failure means the SOURCE data is wrong (internally inconsistent
# or impossible) — these populate earnings_quarter.data_quality_flags. Softer
# review warns (revenue YoY band) and our default-FYE metadata gap
# (l4_fiscal_period_month) are intentionally excluded so the flag = "bad data".
DATA_QUALITY_CHECKS = {"l2_ni_eq_eps_x_shares", "l2_revenue_nonneg"}
MONTHS = ['Jan','Feb','Mar','Apr','May','Jun',
          'Jul','Aug','Sep','Oct','Nov','Dec']

GREEN="\033[32m"; RED="\033[31m"; YELLOW="\033[33m"; DIM="\033[2m"
BOLD="\033[1m"; RESET="\033[0m"


# ---- .env ----------------------------------------------------------------
def load_dotenv():
    here = Path(__file__).resolve().parent
    p = next((d / ".env" for d in (here, *here.parents) if (d / ".env").is_file()),
             here / ".env")
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


# ---- Universe from the Obsidian Base (10_Stocks/Stocks/*.md) --------------
# Same source of truth as fmp-to-ddb: one note per ticker, `code`+`market`
# frontmatter. Earnings/consensus only exists for equities, so this pipeline
# defaults to market==US (crypto/futures/HK have no quarterly earnings here).
def _find_stocks_dir():
    here = Path(__file__).resolve().parent
    for d in (here, *here.parents):
        cand = d / "10_Stocks" / "Stocks"
        if cand.is_dir():
            return cand
    return None


def _parse_frontmatter(text):
    m = re.match(r"^---\s*\n(.*?)\n---", text, re.S)
    if not m:
        return {}
    fm = {}
    for line in m.group(1).splitlines():
        if ":" in line and not line[:1].isspace():
            k, v = line.split(":", 1)
            fm[k.strip()] = v.strip().strip('"').strip("'")
    return fm


def load_stock_universe(markets=("US",)):
    """Read the ticker universe from Stocks.base. Returns (codes, skipped),
    where skipped = [(code, market)] for tickers outside the requested markets
    (crypto/futures/HK — no quarterly earnings in this pipeline)."""
    sdir = _find_stocks_dir()
    if sdir is None:
        sys.exit("could not locate 10_Stocks/Stocks (Stocks.base source) above "
                 "this script")
    want = set(markets)
    codes, skipped, seen = [], [], set()
    for f in sorted(sdir.glob("*.md")):
        fm = _parse_frontmatter(f.read_text(encoding="utf-8"))
        if fm.get("type") != "stock":
            continue
        code = (fm.get("code") or "").strip()
        mkt = (fm.get("market") or "").strip()
        if not code or code in seen:
            continue
        if mkt not in want:
            skipped.append((code, mkt))
            continue
        seen.add(code)
        codes.append(code)
    return codes, skipped


# ---- FMP REST with candidate fallback ------------------------------------
_WORKING_ENDPOINT = {}

# FMP stable API endpoint path candidates. The script tries each in order,
# 404s advance to the next, the first that returns 200 is cached and reused
# for all subsequent symbols in this run. Add more here if FMP changes paths.
EARNINGS_ENDPOINTS  = [
    "earnings",                              # FMP stable canonical
    "earnings-calendar",
    "earning_calendar",                      # legacy v3 spelling
    "calendar/earnings-company",
    "earnings-calendar/earnings-company",
    "earnings-surprises",
]
INCOME_ENDPOINTS    = [
    "income-statement",                      # FMP stable canonical
    "statements/income-statement",
    "income-statements",
]
EST_ENDPOINTS       = [
    "analyst-estimates",                     # FMP stable canonical
    "financial-estimates",
    "analyst-financial-estimates",
    "analyst/financial-estimates",
    "analyst/estimates",
]


class _NotFound(Exception):
    pass


def _get_once(endpoint, params, api_key, timeout=30):
    url = f"{FMP_BASE}/{endpoint}?{urllib.parse.urlencode({**params, 'apikey': api_key})}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise _NotFound(endpoint)
        raise


def _get(kind, candidates, params, api_key, retries=3):
    order = ([_WORKING_ENDPOINT[kind]] if kind in _WORKING_ENDPOINT else []) + \
            [c for c in candidates if c != _WORKING_ENDPOINT.get(kind)]
    attempts = []
    for endpoint in order:
        for i in range(retries):
            try:
                data = _get_once(endpoint, params, api_key)
                if kind not in _WORKING_ENDPOINT:
                    _WORKING_ENDPOINT[kind] = endpoint
                    print(f"  {DIM}[endpoint] using /{endpoint}{RESET}")
                return data
            except _NotFound:
                attempts.append((endpoint, "404"))
                break  # next candidate
            except Exception as e:
                if i == retries - 1:
                    attempts.append((endpoint, f"{type(e).__name__}: {e}"))
                else:
                    time.sleep(2 ** i)
    detail = "; ".join(f"/{ep} -> {st}" for ep, st in attempts)
    raise RuntimeError(f"FMP {kind} request failed. Tried: {detail}")


def fetch_earnings(symbol, from_date, to_date, api_key):
    data = _get("earnings", EARNINGS_ENDPOINTS,
                {"symbol": symbol, "from": from_date, "to": to_date}, api_key)
    return data or []


def fetch_income_quarterly(symbol, limit, api_key):
    data = _get("income", INCOME_ENDPOINTS,
                {"symbol": symbol, "period": "quarter", "limit": limit}, api_key)
    return data or []


def fetch_financial_estimates(symbol, api_key, limit=20):
    """Quarterly forward + recent past consensus from /analyst/financial-estimates.
    Returns richer data than /calendar/earnings-company: netIncomeAvg directly,
    plus low/high range and analyst counts."""
    try:
        data = _get("estimates", EST_ENDPOINTS,
                    {"symbol": symbol, "period": "quarter", "limit": limit}, api_key)
        return data or []
    except Exception as e:
        # Non-fatal: estimates endpoint sometimes lags for newer tickers
        print(f"  {DIM}[warn] financial-estimates failed for {symbol}: {e}{RESET}",
              file=sys.stderr)
        return []


# ---- Helpers --------------------------------------------------------------
def derive_calendar_period(period_end):
    """3-month window ending in fiscal period end month, e.g. 'Apr-Jun 2025'."""
    em = period_end.month
    sm = em - 2
    if sm <= 0:
        sm += 12
    return f"{MONTHS[sm-1]}-{MONTHS[em-1]} {period_end.year}"


def derive_calendar_quarter(period_end):
    """Cross-company comparable bucket via the -1 calendar month rule."""
    y, m = period_end.year, period_end.month
    if m == 1:
        m2, y2 = 12, y - 1
    else:
        m2, y2 = m - 1, y
    q = MONTH_TO_QUARTER[m2]
    return f"Q{q} {y2}"


def derive_quarter_label(symbol, fiscal_year, fiscal_period):
    """Per-company display label."""
    p = fiscal_period.lstrip("Q") if fiscal_period.startswith("Q") else fiscal_period
    if symbol == "AAPL":
        return f"FQ{p} {fiscal_year}"
    if symbol in ("MSFT", "NVDA"):
        return f"Q{p} FY{fiscal_year}"
    return f"Q{p} {fiscal_year}"


def derive_future_period(report_date, fye_month):
    """For an upcoming earnings report date with no income-statement available,
    derive (fiscal_year, fiscal_period, estimated_period_end).

    fiscal_year LABEL = calendar year the fiscal year ends in (NVDA convention).
    Validated against 11 real future MAG7 reports in the test suite."""
    est_pe = report_date - dt.timedelta(days=30)
    pe_m = est_pe.month
    q_end_months = {
        'Q1': ((fye_month + 3 - 1) % 12) + 1,
        'Q2': ((fye_month + 6 - 1) % 12) + 1,
        'Q3': ((fye_month + 9 - 1) % 12) + 1,
        'Q4': fye_month,
    }
    fiscal_period, best = None, 99
    for p, m in q_end_months.items():
        diff = min((pe_m - m) % 12, (m - pe_m) % 12)
        if diff < best:
            best, fiscal_period = diff, p
    fiscal_year = est_pe.year if est_pe.month <= fye_month else est_pe.year + 1
    return fiscal_year, fiscal_period, est_pe


def index_estimates_by_fy_q(estimates_rows, fye_month):
    """Map each /analyst/financial-estimates row to (fy, period) using its
    period-end date. Returns dict (fy, period) -> estimate row."""
    out = {}
    for r in estimates_rows:
        try:
            pe = dt.datetime.strptime(r["date"], "%Y-%m-%d").date()
        except (KeyError, ValueError):
            continue
        # Same fiscal_year rule as derive_future_period; ±1 month tolerance for
        # 52/53-week year drift
        pe_m = pe.month
        q_end_months = {
            'Q1': ((fye_month + 3 - 1) % 12) + 1,
            'Q2': ((fye_month + 6 - 1) % 12) + 1,
            'Q3': ((fye_month + 9 - 1) % 12) + 1,
            'Q4': fye_month,
        }
        fp, best = None, 99
        for p, m in q_end_months.items():
            diff = min((pe_m - m) % 12, (m - pe_m) % 12)
            if diff < best: best, fp = diff, p
        fy = pe.year if pe.month <= fye_month else pe.year + 1
        out[(fy, fp)] = r
    return out


def match_income_to_earnings(earnings_rows, income_rows):
    """For each earnings row, find latest income-statement row 0-90 days before."""
    e_sorted = sorted(earnings_rows, key=lambda r: r["date"])
    i_sorted = sorted(income_rows, key=lambda r: r["date"])
    matched = {}
    for e in e_sorted:
        ed = dt.datetime.strptime(e["date"], "%Y-%m-%d").date()
        best, best_date = None, None
        for i in i_sorted:
            id_ = dt.datetime.strptime(i["date"], "%Y-%m-%d").date()
            gap = (ed - id_).days
            if 0 <= gap <= 90:
                if best_date is None or id_ > best_date:
                    best, best_date = i, id_
        matched[e["date"]] = best
    return matched


# ---- Build ingest payload + validate -------------------------------------
def build_quarter_records(symbol, earnings_rows, income_rows, estimates_rows,
                          company_meta, corporate_actions, today):
    """
    Yields (record_dict, ValidationReport) for each (symbol, fiscal_year,
    fiscal_period). Includes FUTURE quarters that have no income-statement
    yet (epsActual is null in /calendar/earnings-company).

    estimates_rows comes from /analyst/financial-estimates and provides:
      * direct netIncomeAvg (more accurate than eps*shares for future quarters)
      * revenueLow/Avg/High + epsLow/Avg/High range
      * num analysts counts
    """
    matched = match_income_to_earnings(earnings_rows, income_rows)
    e_sorted = sorted(earnings_rows, key=lambda r: r["date"])

    fye_month = company_meta.get(symbol, {}).get("fiscal_year_end_month", 12)

    # Build (fy, period) -> income_row for past quarters
    by_fy_q = {}
    for i in income_rows:
        if i.get("period") in ("Q1","Q2","Q3","Q4"):
            by_fy_q[(int(i["fiscalYear"]), i["period"])] = i

    # Build (fy, period) -> estimate row for forward consensus
    est_by_fy_q = index_estimates_by_fy_q(estimates_rows, fye_month)

    for e in e_sorted:
        report_date = dt.datetime.strptime(e["date"], "%Y-%m-%d").date()
        i_row = matched.get(e["date"])

        if i_row is not None:
            # Past quarter: use income-statement as source of truth
            fiscal_year = int(i_row["fiscalYear"])
            fiscal_period = i_row["period"]
            if fiscal_period not in ("Q1","Q2","Q3","Q4"):
                continue   # skip annual rows
            period_end = dt.datetime.strptime(i_row["date"], "%Y-%m-%d").date()
            is_future = False
        elif report_date > today:
            # Future quarter: derive (fy, period) from report_date + FY end month
            fiscal_year, fiscal_period, period_end = derive_future_period(
                report_date, fye_month)
            is_future = True
        else:
            # PAST quarter with no matched income-statement = outside our fetch
            # window (FMP /earnings ignores from/to and returns full history).
            # Silently skip — we don't have enough data to make a complete row.
            continue

        # Compute cumulative split factor: product of all splits with
        # ex_date > period_end (all splits AFTER this quarter affect it).
        split_factor = 1.0
        for ca in corporate_actions:
            if ca["symbol"] != symbol:
                continue
            if ca["action_type"] not in ("split", "reverse_split"):
                continue
            ca_ex = ca["ex_date"]
            if isinstance(ca_ex, str):
                ca_ex = dt.datetime.strptime(ca_ex, "%Y-%m-%d").date()
            if ca_ex > period_end and ca.get("ratio"):
                ratio = float(ca["ratio"])
                if ca["action_type"] == "split":
                    split_factor *= ratio
                else:
                    split_factor /= ratio

        # YoY: find income-statement for (fy-1, period)
        yr_ago = by_fy_q.get((fiscal_year - 1, fiscal_period))
        revenue_year_ago = (yr_ago["revenue"] / 1e9) if yr_ago and yr_ago.get("revenue") else None

        # Actuals: filled only for past quarters
        if i_row is not None:
            revenue_actual = (i_row["revenue"] / 1e9) if i_row.get("revenue") else None
            gaap_ni = (i_row["netIncome"] / 1e9) if i_row.get("netIncome") else None
            gaap_eps = i_row.get("epsDiluted")
            dil_shares = (i_row["weightedAverageShsOutDil"] / 1e6) if i_row.get("weightedAverageShsOutDil") else None
            # Net income ATTRIBUTABLE TO COMMON (after preferred div / NCI /
            # mandatory-convertible). This is what reconciles with epsDiluted;
            # total netIncome does not for complex cap structures (e.g. ALB).
            blni = i_row.get("bottomLineNetIncome")
            ni_to_common = (blni / 1e9) if blni is not None else gaap_ni
        else:
            revenue_actual = gaap_ni = gaap_eps = dil_shares = ni_to_common = None

        ng_eps_actual = e.get("epsActual")     # NULL for future

        # Consensus: prefer /analyst/financial-estimates (higher precision,
        # has analyst counts + NI direct estimate), fall back to /earnings-company
        est_row = est_by_fy_q.get((fiscal_year, fiscal_period))
        if est_row:
            rev_cons = (est_row.get("revenueAvg") / 1e9) if est_row.get("revenueAvg") else None
            ng_eps_cons = est_row.get("epsAvg")
            ni_cons_est = (est_row.get("netIncomeAvg") / 1e9) if est_row.get("netIncomeAvg") else None
            rev_cons_low = (est_row.get("revenueLow") / 1e9) if est_row.get("revenueLow") else None
            rev_cons_high = (est_row.get("revenueHigh") / 1e9) if est_row.get("revenueHigh") else None
            ng_eps_cons_low = est_row.get("epsLow")
            ng_eps_cons_high = est_row.get("epsHigh")
            num_an_rev = est_row.get("numAnalystsRevenue")
            num_an_eps = est_row.get("numAnalystsEps")
            consensus_source = "FMP_financial_estimates"
        else:
            rev_cons = (e["revenueEstimated"] / 1e9) if e.get("revenueEstimated") else None
            ng_eps_cons = e.get("epsEstimated")
            ni_cons_est = None
            rev_cons_low = rev_cons_high = None
            ng_eps_cons_low = ng_eps_cons_high = None
            num_an_rev = num_an_eps = None
            consensus_source = "FMP_earnings_company"

        # calendar_quarter and calendar_period are GENERATED columns in PG;
        # we still derive them here for validation comparison.
        calendar_quarter = derive_calendar_quarter(period_end)
        quarter_label = derive_quarter_label(symbol, fiscal_year, fiscal_period)

        # Non-GAAP NI: for GAAP-only companies, mirror GAAP. For non-GAAP
        # companies, derive from epsActual × shares. For META, leave NULL.
        rg = company_meta.get(symbol, {}).get("reports_official_non_gaap", "")
        if "No" in rg or "GAAP only" in (company_meta.get(symbol, {}).get("default_non_gaap_basis") or ""):
            ng_ni = gaap_ni
        elif rg == "NO":   # META
            ng_ni = None
        else:
            ng_ni = (ng_eps_actual * dil_shares / 1000) if (ng_eps_actual and dil_shares) else None

        rec = {
            "symbol": symbol,
            "fiscal_year": fiscal_year,
            "fiscal_period": fiscal_period,
            "quarter_label": quarter_label,
            "_expected_calendar_quarter": calendar_quarter,
            "period_end": period_end,
            "report_date": report_date,
            "revenue_actual": revenue_actual,
            "gaap_ni": gaap_ni,
            "gaap_eps_diluted_as_reported": gaap_eps,
            "dil_shares_as_reported": dil_shares,
            "ng_ni": ng_ni,
            "ng_eps_as_reported": ng_eps_actual,
            "cumulative_split_factor": split_factor,
            "revenue_consensus": rev_cons,
            "ng_eps_consensus_as_reported": ng_eps_cons,
            "ni_consensus_estimate": ni_cons_est,
            "revenue_consensus_low":  rev_cons_low,
            "revenue_consensus_high": rev_cons_high,
            "ng_eps_consensus_low":   ng_eps_cons_low,
            "ng_eps_consensus_high":  ng_eps_cons_high,
            "num_analysts_revenue":   num_an_rev,
            "num_analysts_eps":       num_an_eps,
            "consensus_last_refreshed": dt.datetime.now(dt.timezone.utc),
            "consensus_source": consensus_source,
            "actuals_filing_date": (dt.datetime.strptime(i_row["filingDate"], "%Y-%m-%d").date()
                                    if i_row is not None and i_row.get("filingDate") else None),
        }

        # Run validation
        report = run_all_checks(
            symbol=symbol,
            fiscal_year=fiscal_year, fiscal_period=fiscal_period,
            period_end=period_end, report_date=report_date,
            expected_calendar_quarter=calendar_quarter,
            fiscal_year_end_month=company_meta.get(symbol, {}).get("fiscal_year_end_month", 12),
            revenue_actual=revenue_actual,
            gaap_ni=gaap_ni,
            gaap_eps_diluted=gaap_eps,
            dil_shares=dil_shares,
            cumulative_split_factor=split_factor,
            earnings_endpoint_eps_actual=ng_eps_actual,
            reports_official_non_gaap=rg,
            revenue_year_ago=revenue_year_ago,
            ni_to_common=ni_to_common,
        )

        # Mark the row when the SOURCE numbers are internally inconsistent or
        # impossible, so bad data is queryable in PG (data_quality_flags)
        # instead of aborting the batch. Scope is the genuine data-integrity
        # checks only — NOT softer review warns (YoY band) or our own
        # default-FYE metadata gap (l4_fiscal_period_month), which would
        # otherwise dilute the "bad data" set.
        rec["data_quality_flags"] = sorted({
            c.name for c in report.checks
            if (not c.passed) and c.severity in ("error", "warn")
            and c.name in DATA_QUALITY_CHECKS
        })
        # PG L1 CHECK rejects negative revenue; null the impossible value (the
        # l2_revenue_nonneg flag already records that it happened).
        if rec["revenue_actual"] is not None and rec["revenue_actual"] < 0:
            rec["revenue_actual"] = None

        yield rec, report


# ---- Persist -------------------------------------------------------------
# NOTE: calendar_period and calendar_quarter are GENERATED columns and must
# NOT appear in INSERT/UPDATE column lists. PG will compute them from
# period_end automatically.
UPSERT_SQL = """
INSERT INTO earnings_quarter (
    symbol, fiscal_year, fiscal_period, quarter_label,
    period_end, report_date,
    revenue_actual, gaap_ni, gaap_eps_diluted_as_reported,
    dil_shares_as_reported, ng_ni, ng_eps_as_reported,
    cumulative_split_factor,
    revenue_consensus, ng_eps_consensus_as_reported,
    ni_consensus_estimate,
    revenue_consensus_low, revenue_consensus_high,
    ng_eps_consensus_low,  ng_eps_consensus_high,
    num_analysts_revenue, num_analysts_eps,
    consensus_last_refreshed, consensus_source,
    actuals_filing_date, data_quality_flags
) VALUES (
    %(symbol)s, %(fiscal_year)s, %(fiscal_period)s, %(quarter_label)s,
    %(period_end)s, %(report_date)s,
    %(revenue_actual)s, %(gaap_ni)s, %(gaap_eps_diluted_as_reported)s,
    %(dil_shares_as_reported)s, %(ng_ni)s, %(ng_eps_as_reported)s,
    %(cumulative_split_factor)s,
    %(revenue_consensus)s, %(ng_eps_consensus_as_reported)s,
    %(ni_consensus_estimate)s,
    %(revenue_consensus_low)s, %(revenue_consensus_high)s,
    %(ng_eps_consensus_low)s,  %(ng_eps_consensus_high)s,
    %(num_analysts_revenue)s, %(num_analysts_eps)s,
    %(consensus_last_refreshed)s, %(consensus_source)s,
    %(actuals_filing_date)s, %(data_quality_flags)s
)
ON CONFLICT (symbol, fiscal_year, fiscal_period) DO UPDATE SET
    quarter_label = EXCLUDED.quarter_label,
    period_end = EXCLUDED.period_end,
    report_date = EXCLUDED.report_date,
    revenue_actual = EXCLUDED.revenue_actual,
    gaap_ni = EXCLUDED.gaap_ni,
    gaap_eps_diluted_as_reported = EXCLUDED.gaap_eps_diluted_as_reported,
    dil_shares_as_reported = EXCLUDED.dil_shares_as_reported,
    ng_ni = EXCLUDED.ng_ni,
    ng_eps_as_reported = EXCLUDED.ng_eps_as_reported,
    cumulative_split_factor = EXCLUDED.cumulative_split_factor,
    revenue_consensus = EXCLUDED.revenue_consensus,
    ng_eps_consensus_as_reported = EXCLUDED.ng_eps_consensus_as_reported,
    ni_consensus_estimate = EXCLUDED.ni_consensus_estimate,
    revenue_consensus_low  = EXCLUDED.revenue_consensus_low,
    revenue_consensus_high = EXCLUDED.revenue_consensus_high,
    ng_eps_consensus_low   = EXCLUDED.ng_eps_consensus_low,
    ng_eps_consensus_high  = EXCLUDED.ng_eps_consensus_high,
    num_analysts_revenue = EXCLUDED.num_analysts_revenue,
    num_analysts_eps     = EXCLUDED.num_analysts_eps,
    consensus_last_refreshed = EXCLUDED.consensus_last_refreshed,
    consensus_source = EXCLUDED.consensus_source,
    actuals_filing_date = EXCLUDED.actuals_filing_date,
    data_quality_flags = EXCLUDED.data_quality_flags,
    updated_at = NOW();
"""

CONSENSUS_HISTORY_SQL = """
INSERT INTO consensus_history (
    symbol, target_fiscal_year, target_fiscal_period, snapshot_date, source,
    revenue_est_as_reported, eps_est_as_reported, cumulative_split_factor
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (symbol, target_fiscal_year, target_fiscal_period, snapshot_date, source)
DO NOTHING;
"""

VALIDATION_LOG_SQL = """
INSERT INTO validation_log (
    table_name, record_key, check_name, expected_value, observed_value,
    passed, severity, validated_by
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s);
"""


def fetch_company_meta(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT symbol, fiscal_year_end_month, reports_official_non_gaap, default_non_gaap_basis FROM company")
        return {r[0]: {
            "fiscal_year_end_month": r[1],
            "reports_official_non_gaap": r[2] or "",
            "default_non_gaap_basis": r[3] or "",
        } for r in cur.fetchall()}


def fetch_corporate_actions(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT symbol, action_type, ex_date, ratio FROM corporate_action")
        return [{"symbol": r[0], "action_type": r[1], "ex_date": r[2],
                 "ratio": float(r[3]) if r[3] is not None else None}
                for r in cur.fetchall()]


# ---- Main ----------------------------------------------------------------
def main():
    p = argparse.ArgumentParser(prog="fetch_fmp_to_pg.py")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--symbol")
    g.add_argument("--symbols", help="comma-separated")
    g.add_argument("--mag7", action="store_true")
    g.add_argument("--stocks", action="store_true",
                   help="all tickers from Stocks.base (10_Stocks/Stocks/*.md)")
    p.add_argument("--markets", default="US",
                   help="markets to include with --stocks (default US; "
                        "crypto/futures/HK have no quarterly earnings here)")
    p.add_argument("--years", type=int, default=3,
                   help="how many years back (default 3)")
    p.add_argument("--dry-run", action="store_true",
                   help="run fetch + validation, print summary, but DON'T write to PG")
    p.add_argument("--force-on-warn", action="store_true",
                   help="proceed even if L2 warnings exist (errors always abort)")
    args = p.parse_args()

    load_dotenv()
    api_key = os.environ.get("FMP_API_KEY")
    if not api_key:
        sys.exit("FMP_API_KEY not set in .env")

    if args.stocks:
        mkts = tuple(m.strip() for m in args.markets.split(",") if m.strip())
        symbols, skipped = load_stock_universe(mkts)
        if not symbols:
            sys.exit(f"no symbols from Stocks.base for markets {args.markets}")
        print(f"{BOLD}universe from Stocks.base: {len(symbols)} symbols "
              f"(markets={','.join(mkts)}){RESET}")
        if skipped:
            bym = dict(Counter(m or "(none)" for _, m in skipped))
            print(f"  {DIM}skipped {len(skipped)} non-equity (no earnings): {bym}{RESET}")
    elif args.mag7:
        symbols = MAG7
    elif args.symbols:
        symbols = [s.strip() for s in args.symbols.split(",")]
    else:
        symbols = [args.symbol]

    to_date = dt.date.today().isoformat()
    from_date = (dt.date.today() - dt.timedelta(days=365 * args.years + 30)).isoformat()

    # Connect to PG to read company + corporate_action (and write at the end)
    psycopg = _ensure_psycopg()
    pg_cfg = {
        "host": os.environ.get("PGHOST", "localhost"),
        "port": int(os.environ.get("PGPORT", "5432")),
        "dbname": os.environ.get("PGDATABASE"),
        "user": os.environ.get("PGUSER"),
        "password": os.environ.get("PGPASSWORD"),
    }
    print(f"{BOLD}Connecting to PG {pg_cfg['host']}:{pg_cfg['port']} db={pg_cfg['dbname']}{RESET}")
    try:
        conn = psycopg.connect(**pg_cfg, connect_timeout=8)
    except Exception as e:
        sys.exit(f"{RED}PG connect failed: {e}{RESET}")

    try:
        company_meta = fetch_company_meta(conn)
        corp_actions = fetch_corporate_actions(conn)
        if not company_meta:
            sys.exit(f"{RED}company table is empty. Run schema/002_seed_mag7.sql first.{RESET}")

        all_records = []  # list of (rec, report)
        for sym in symbols:
            if sym not in company_meta:
                # Not seeded in the company table — use sensible defaults so
                # --stocks works for the whole Base. FYE=Dec covers most US
                # large caps; past quarters take fiscal_year/period straight
                # from the income statement, so only forward-quarter derivation
                # and non-GAAP basis fall back to defaults (flagged as warns).
                company_meta[sym] = {"fiscal_year_end_month": 12,
                                     "reports_official_non_gaap": "",
                                     "default_non_gaap_basis": ""}
                print(f"\n{BOLD}{sym}{RESET} {DIM}(default meta: FYE=Dec, not in "
                      f"company table){RESET}")
            else:
                print(f"\n{BOLD}{sym}{RESET}")
            try:
                e_rows = fetch_earnings(sym, from_date, to_date, api_key)
                i_rows = fetch_income_quarterly(sym, args.years * 4 + 4, api_key)
                est_rows = fetch_financial_estimates(sym, api_key,
                                                     limit=args.years * 4 + 8)
            except Exception as ex:
                print(f"  {RED}FETCH FAILED: {ex}{RESET}")
                continue
            print(f"  fetched: {len(e_rows)} earnings, {len(i_rows)} income, "
                  f"{len(est_rows)} forward estimates")
            for rec, report in build_quarter_records(
                sym, e_rows, i_rows, est_rows, company_meta, corp_actions,
                dt.date.today()):
                all_records.append((rec, report))

        # ---- Validation gate -------------------------------------------------
        print(f"\n{BOLD}=== Validation summary ==={RESET}")
        total = len(all_records)
        errors = sum(1 for _, r in all_records if r.has_errors)
        warnings = sum(1 for _, r in all_records if r.has_warnings)
        print(f"records: {total}  errors: {errors}  warnings: {warnings}")

        # Data-quality flag rollup (rows that will be MARKED in PG, not dropped)
        flag_counts = Counter(
            f for rec, _ in all_records for f in rec.get("data_quality_flags", []))
        flagged_rows = sum(1 for rec, _ in all_records if rec.get("data_quality_flags"))
        if flagged_rows:
            print(f"{YELLOW}data-quality: {flagged_rows} row(s) flagged "
                  f"(written + marked in data_quality_flags): {dict(flag_counts)}{RESET}")
        if errors:
            print(f"\n{RED}{BOLD}=== BLOCKING ERRORS ==={RESET}")
            for rec, report in all_records:
                if not report.has_errors:
                    continue
                print(f"  {report.summary()}")
                for c in report.checks:
                    if c.is_blocking():
                        print(f"    {RED}{c.name}{RESET}: {c.message}")
            print(f"\n{RED}ABORTING. Fix errors or add an exception before retry.{RESET}")
            sys.exit(2)
        if warnings and not args.force_on_warn:
            print(f"\n{YELLOW}Warnings present. Re-run with --force-on-warn to proceed.{RESET}")
            for rec, report in all_records:
                if not report.has_warnings:
                    continue
                print(f"  {report.summary()}")
                for c in report.checks:
                    if not c.passed and c.severity == "warn":
                        print(f"    {YELLOW}{c.name}{RESET}: {c.message}")
            sys.exit(3)

        if args.dry_run:
            print(f"\n{GREEN}--dry-run OK: would have written {total} rows "
                  f"({flagged_rows} flagged). Nothing written.{RESET}")
            return

        # ---- Persist ---------------------------------------------------------
        with conn.cursor() as cur:
            for rec, report in all_records:
                cur.execute(UPSERT_SQL, rec)
                # Append to consensus_history
                cur.execute(CONSENSUS_HISTORY_SQL, (
                    rec["symbol"], rec["fiscal_year"], rec["fiscal_period"],
                    dt.date.today(), "FMP_earnings_company",
                    rec["revenue_consensus"],
                    rec["ng_eps_consensus_as_reported"],
                    rec["cumulative_split_factor"],
                ))
                # Log every check result
                key = f"{rec['symbol']}/{rec['fiscal_year']}/{rec['fiscal_period']}"
                for c in report.checks:
                    cur.execute(VALIDATION_LOG_SQL, (
                        "earnings_quarter", key, c.name,
                        c.expected, c.observed,
                        c.passed, c.severity, "auto_l2_l4_invariant",
                    ))
            conn.commit()
        print(f"\n{GREEN}wrote {total} rows. validation_log appended.{RESET}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
