"""
fetch_fmp_to_ddb.py — FMP daily K-line -> DolphinDB (dividend-adjusted / total-return).

Architecture (per CLAUDE.md §🗄️): Python only FETCHES + ORCHESTRATES.
All column computation (pct_change / turnover / market_cap / MA20-250) runs
in DolphinDB via dos_scripts/load_and_compute.dos.

Pipeline per run:
  1. For each symbol: REST-fetch dividend-adjusted OHLCV + quarterly shares.
  2. Interpolate shares to each trading day (light lookup, not an indicator).
  3. Build one in-memory staging DataFrame [symbol, date, OHLC, volume, shares].
  4. Upload `staging` to DDB; run load_and_compute.dos (computes + appends).

Usage (on the machine where DolphinDB runs):
    cp .env.example .env          # fill FMP_API_KEY + DDB_*
    python fetch_fmp_to_ddb.py --stocks --years 10 --save-ddb   # whole Base
    python fetch_fmp_to_ddb.py --symbol AAPL --years 10 --save-ddb

--stocks reads the ticker universe from Stocks.base (10_Stocks/Stocks/*.md);
--symbol / --symbols still work for ad-hoc one-offs.
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

FMP_BASE = "https://financialmodelingprep.com/stable"


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
# The "Stocks.base" database is backed by one markdown note per ticker; the
# `code` + `market` frontmatter is the source of truth for the symbol list.
def _find_stocks_dir():
    """Walk up from this script to find the vault's 10_Stocks/Stocks folder."""
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


# Per-market FMP symbol suffix (US needs none; HK uses the 4-digit code + .HK).
_MARKET_SUFFIX = {"US": "", "HK": ".HK"}


def load_universe(markets=("US", "HK")):
    """Read the ticker universe from the Stocks.base notes.

    Returns (pairs, skipped):
      pairs   = list of (fetch_symbol, db_symbol) for the requested markets.
                HK fetches as `0700.HK` but is STORED as `0700` (matches the
                Base note's code so DDB and the vault line up).
      skipped = list of (code, market) excluded — other markets, or crypto /
                futures that need a different FMP endpoint than this script.
    """
    sdir = _find_stocks_dir()
    if sdir is None:
        sys.exit("could not locate 10_Stocks/Stocks (Stocks.base source) above "
                 "this script")
    want = set(markets)
    pairs, skipped, seen = [], [], set()
    for f in sorted(sdir.glob("*.md")):
        fm = _parse_frontmatter(f.read_text(encoding="utf-8"))
        if fm.get("type") != "stock":
            continue
        code = (fm.get("code") or "").strip()
        mkt = (fm.get("market") or "").strip()
        if not code or code in seen:
            continue
        if mkt not in want or mkt not in _MARKET_SUFFIX:
            skipped.append((code, mkt))
            continue
        seen.add(code)
        pairs.append((code + _MARKET_SUFFIX[mkt], code))
    return pairs, skipped


# ---- FMP REST ------------------------------------------------------------
# FMP "stable" API path forms differ by deployment; the chart endpoints use a
# slash before the variant (historical-price-eod/dividend-adjusted) while some
# older docs show all-hyphens. We probe candidates and cache whichever works.
PRICE_ENDPOINTS = [
    "historical-price-eod/dividend-adjusted",
    "historical-price-eod-dividend-adjusted",
]
EV_ENDPOINTS = [
    "enterprise-values",
]
_WORKING = {}  # cache: kind -> endpoint that returned non-404


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
    """Try candidate endpoint paths (skip 404s), retry transient errors."""
    # If we already found the working path for this kind, use it directly.
    order = ([_WORKING[kind]] if kind in _WORKING else []) + \
            [c for c in candidates if c != _WORKING.get(kind)]
    last = None
    for endpoint in order:
        for i in range(retries):
            try:
                data = _get_once(endpoint, params, api_key)
                if kind not in _WORKING:
                    _WORKING[kind] = endpoint
                    print(f"  [endpoint] using /{endpoint}")
                return data
            except _NotFound:
                last = f"404 on /{endpoint}"
                break  # don't retry a 404 — move to next candidate path
            except Exception as e:
                last = e
                time.sleep(2 ** i)
    raise RuntimeError(f"FMP request failed (tried {order}): {last}")


def fetch_prices(symbol, from_date, to_date, api_key):
    data = _get("price", PRICE_ENDPOINTS,
                {"symbol": symbol, "from": from_date, "to": to_date}, api_key)
    if isinstance(data, dict) and "Error Message" in data:
        raise RuntimeError(f"{symbol} prices: {data['Error Message']}")
    return data or []


def fetch_shares(symbol, api_key, limit=45):
    data = _get("ev", EV_ENDPOINTS,
                {"symbol": symbol, "period": "quarter", "limit": limit}, api_key)
    if isinstance(data, dict) and "Error Message" in data:
        raise RuntimeError(f"{symbol} shares: {data['Error Message']}")
    return data or []


# ---- Pure transform (unit-testable, no network / no DDB) -----------------
def interp_shares(trade_date, share_points):
    """Linear interpolation of shares on a date.
    share_points: sorted list of (date_obj, shares). Constant extrapolation
    outside the range. Returns float or None if no points."""
    if not share_points:
        return None
    if trade_date <= share_points[0][0]:
        return float(share_points[0][1])
    if trade_date >= share_points[-1][0]:
        return float(share_points[-1][1])
    for i in range(len(share_points) - 1):
        d0, n0 = share_points[i]
        d1, n1 = share_points[i + 1]
        if d0 <= trade_date <= d1:
            span = (d1 - d0).days
            if span == 0:
                return float(n0)
            frac = (trade_date - d0).days / span
            return float(n0) + (float(n1) - float(n0)) * frac
    return float(share_points[-1][1])


def build_staging_rows(symbol, price_records, ev_records):
    """Turn raw FMP responses into staging rows for one symbol.
    Returns list of dicts: symbol/date/open/high/low/close/volume/shares.
    Pure function — feed it MCP/REST JSON and it works offline."""
    share_points = sorted(
        (dt.datetime.strptime(r["date"], "%Y-%m-%d").date(),
         r.get("numberOfShares"))
        for r in ev_records if r.get("numberOfShares")
    )
    rows = []
    for r in sorted(price_records, key=lambda x: x["date"]):
        d = dt.datetime.strptime(r["date"], "%Y-%m-%d").date()
        rows.append({
            "symbol": symbol,
            "date": d,
            "open":  r["adjOpen"],
            "high":  r["adjHigh"],
            "low":   r["adjLow"],
            "close": r["adjClose"],
            "volume": int(r["volume"]),
            "shares": interp_shares(d, share_points) or 0.0,
        })
    return rows


# ---- DDB orchestration ---------------------------------------------------
def write_to_ddb(rows, replace_schema=False):
    import pandas as pd
    import dolphindb as ddb

    host = os.environ.get("DDB_HOST", "localhost")
    port = int(os.environ.get("DDB_DATA_PORT", "8902"))
    user = os.environ.get("DDB_USER", "admin")
    pwd  = os.environ.get("DDB_PASSWORD", "")

    s = ddb.Session()
    if not s.connect(host, port, user, pwd, reconnect=False):
        raise RuntimeError(f"cannot connect to DDB {host}:{port}")

    here = Path(__file__).parent / "dos_scripts"
    if replace_schema:
        print(s.run((here / "ensure_schema.dos").read_text(encoding="utf-8")))

    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])  # -> NANOTIMESTAMP on upload
    print(f"uploading staging: {len(df):,} rows, "
          f"{df['symbol'].nunique()} symbols")
    s.upload({"staging": df})

    result = s.run((here / "load_and_compute.dos").read_text(encoding="utf-8"))
    print("\n=== per-symbol landed in DDB ===")
    print(result)
    s.close()


# ---- main ----------------------------------------------------------------
def main():
    p = argparse.ArgumentParser(prog="fetch_fmp_to_ddb.py")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--symbol")
    g.add_argument("--symbols", help="comma-separated")
    g.add_argument("--stocks", action="store_true",
                   help="fetch the whole universe from Stocks.base "
                        "(10_Stocks/Stocks/*.md)")
    p.add_argument("--markets", default="US,HK",
                   help="comma-separated markets to include with --stocks "
                        "(default US,HK; crypto/futures need other endpoints)")
    p.add_argument("--years", type=int, default=10)
    p.add_argument("--from-date")
    p.add_argument("--to-date")
    p.add_argument("--save-ddb", action="store_true",
                   help="write to DolphinDB (omit for a dry-run preview)")
    p.add_argument("--ensure-schema", action="store_true",
                   help="create the DFS table if missing (no-op if exists)")
    p.add_argument("--dump-json", help="write staging rows to this JSON "
                   "(offline inspection, no DDB)")
    args = p.parse_args()

    load_dotenv()
    api_key = os.environ.get("FMP_API_KEY")
    if not api_key and not args.dump_json:
        sys.exit("FMP_API_KEY not set (.env). Required unless only dumping a "
                 "previously fetched payload.")

    # pairs: (fetch_symbol, db_symbol). They differ only for HK (`0700.HK` vs `0700`).
    if args.stocks:
        mkts = tuple(m.strip() for m in args.markets.split(",") if m.strip())
        pairs, skipped = load_universe(mkts)
        if not pairs:
            sys.exit(f"no symbols from Stocks.base for markets {args.markets}")
        print(f"universe from Stocks.base: {len(pairs)} symbols "
              f"(markets={','.join(mkts)})")
        if skipped:
            bym = dict(Counter(m or "(none)" for _, m in skipped))
            print(f"  skipped {len(skipped)} not in markets / unsupported "
                  f"endpoint: {bym}")
    elif args.symbols:
        pairs = [(x.strip(), x.strip()) for x in args.symbols.split(",") if x.strip()]
    else:
        pairs = [(args.symbol, args.symbol)]

    to_date = args.to_date or dt.date.today().isoformat()
    if args.from_date:
        from_date = args.from_date
    else:
        from_date = (dt.date.today() -
                     dt.timedelta(days=365 * args.years + 10)).isoformat()

    all_rows = []
    for fetch_sym, db_sym in pairs:
        label = db_sym if fetch_sym == db_sym else f"{db_sym} ({fetch_sym})"
        try:
            prices = fetch_prices(fetch_sym, from_date, to_date, api_key)
            shares = fetch_shares(fetch_sym, api_key)
            rows = build_staging_rows(db_sym, prices, shares)
            all_rows.extend(rows)
            print(f"  {label}: {len(rows):,} rows "
                  f"({rows[0]['date']} -> {rows[-1]['date']})" if rows
                  else f"  {label}: 0 rows")
        except Exception as e:
            print(f"  {label}: FAILED — {e}", file=sys.stderr)
        time.sleep(0.2)

    if not all_rows:
        sys.exit("no rows fetched")

    print(f"\ntotal staging rows: {len(all_rows):,}")

    if args.dump_json:
        out = [{**r, "date": r["date"].isoformat()} for r in all_rows]
        Path(args.dump_json).write_text(json.dumps(out), encoding="utf-8")
        print(f"dumped to {args.dump_json}")

    if args.save_ddb:
        write_to_ddb(all_rows, replace_schema=args.ensure_schema)
    else:
        print("(dry run — pass --save-ddb to write to DolphinDB)")


if __name__ == "__main__":
    main()
