"""
seed_company_from_base.py — populate the `company` table for the whole
Stocks.base US universe, so fetch_fmp_to_pg --stocks no longer falls back to
default (FYE=Dec) metadata.

What it fills, and from where:
  * fiscal_year_end_month  <- month of the FMP Q4 income-statement period_end
       (FMP profile.fiscalYearEnd is empty; the Q4 quarter-end month IS the FYE
        month — verified: AAPL=9, COST=8, NVDA=1, MSFT=6). Fallback 12.
  * name / sector / industry / cik  <- FMP /profile (name falls back to the
       Stocks.base note name, then the symbol).

What it does NOT fill: reports_official_non_gaap / default_non_gaap_basis — that
is a qualitative SEC-filing fact, not derivable from FMP numbers. Left NULL
(the ingest then derives non-GAAP NI from epsActual x shares). Curate per
company later if you need exact non-GAAP basis.

ON CONFLICT (symbol) DO NOTHING: the hand-curated MAG7 rows are preserved;
re-running is safe and only adds missing companies.

    python seed_company_from_base.py --dry-run          # derive + print, no write
    python seed_company_from_base.py --dry-run --limit 5
    python seed_company_from_base.py                    # upsert into company
"""
import argparse
import sys

from fetch_fmp_to_pg import (
    load_dotenv, load_stock_universe, _find_stocks_dir, _parse_frontmatter,
    fetch_income_quarterly, _get_once, _ensure_psycopg,
    GREEN, YELLOW, DIM, BOLD, RESET,
)
import os


def vault_names():
    """code -> Chinese name from the Stocks.base notes (fallback for company.name)."""
    sdir = _find_stocks_dir()
    out = {}
    if sdir is None:
        return out
    for f in sorted(sdir.glob("*.md")):
        fm = _parse_frontmatter(f.read_text(encoding="utf-8"))
        if fm.get("type") == "stock" and fm.get("code"):
            out[fm["code"].strip()] = (fm.get("name") or "").strip()
    return out


def derive_fye_month(symbol, api_key):
    """FYE month = month of the most recent Q4 income-statement period_end."""
    inc = fetch_income_quarterly(symbol, 8, api_key)
    q4 = [r for r in inc if r.get("period") == "Q4" and r.get("date")]
    if q4:
        try:
            return int(q4[0]["date"][5:7])
        except (ValueError, IndexError):
            pass
    return 12


def fetch_profile(symbol, api_key):
    try:
        d = _get_once("profile", {"symbol": symbol}, api_key)
        return d[0] if isinstance(d, list) and d else {}
    except Exception:
        return {}


SEED_SQL = """
INSERT INTO company (symbol, name, sector, industry, fiscal_year_end_month, cik)
VALUES (%(symbol)s, %(name)s, %(sector)s, %(industry)s,
        %(fiscal_year_end_month)s, %(cik)s)
ON CONFLICT (symbol) DO NOTHING;
"""


def main():
    p = argparse.ArgumentParser(prog="seed_company_from_base.py")
    p.add_argument("--dry-run", action="store_true",
                   help="derive + print, do not write to PG")
    p.add_argument("--limit", type=int, help="only the first N symbols (testing)")
    args = p.parse_args()

    load_dotenv()
    api_key = os.environ.get("FMP_API_KEY")
    if not api_key:
        sys.exit("FMP_API_KEY not set in .env")

    codes, _ = load_stock_universe(("US",))
    if args.limit:
        codes = codes[:args.limit]
    names = vault_names()
    print(f"{BOLD}seeding {len(codes)} US companies from Stocks.base{RESET}")

    rows = []
    for sym in codes:
        fye = derive_fye_month(sym, api_key)
        prof = fetch_profile(sym, api_key)
        rec = {
            "symbol": sym,
            "name": prof.get("companyName") or names.get(sym) or sym,
            "sector": prof.get("sector") or None,
            "industry": prof.get("industry") or None,
            "fiscal_year_end_month": fye,
            "cik": (prof.get("cik") or None),
        }
        rows.append(rec)
        tag = "" if fye == 12 else f"{YELLOW}FYE={fye}{RESET}"
        print(f"  {sym:6} {rec['name'][:34]:34} fye={fye:2}  {rec['sector'] or '':22} {tag}")

    non_dec = sum(1 for r in rows if r["fiscal_year_end_month"] != 12)
    print(f"\n{BOLD}derived {len(rows)} rows; {non_dec} have non-Dec fiscal year{RESET}")

    if args.dry_run:
        print(f"{DIM}--dry-run: nothing written.{RESET}")
        return

    psycopg = _ensure_psycopg()
    pg_cfg = {
        "host": os.environ.get("PGHOST", "localhost"),
        "port": int(os.environ.get("PGPORT", "5432")),
        "dbname": os.environ.get("PGDATABASE"),
        "user": os.environ.get("PGUSER"),
        "password": os.environ.get("PGPASSWORD"),
    }
    conn = psycopg.connect(**pg_cfg, connect_timeout=8)
    inserted = 0
    try:
        with conn.cursor() as cur:
            for r in rows:
                cur.execute(SEED_SQL, r)
                inserted += cur.rowcount      # 1 if inserted, 0 if already present
            conn.commit()
    finally:
        conn.close()
    print(f"\n{GREEN}inserted {inserted} new companies "
          f"({len(rows) - inserted} already present, preserved).{RESET}")


if __name__ == "__main__":
    main()
