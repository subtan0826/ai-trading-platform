"""inspect_db.py — read-only inspector for the PG state.

Lists tables, row counts, latest validation_log failures, recent ingest.
"""
import os, sys, subprocess
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def _ensure(pkg, im=None):
    try:
        return __import__(im or pkg)
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", pkg])
        return __import__(im or pkg)


_ensure("psycopg[binary]", "psycopg")
import psycopg


def load_dotenv():
    p = Path(__file__).with_name(".env")
    if not p.exists(): return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


GREEN="\033[32m"; RED="\033[31m"; DIM="\033[2m"; BOLD="\033[1m"; RESET="\033[0m"


def main():
    load_dotenv()
    cfg = {
        "host": os.environ.get("PGHOST","localhost"),
        "port": int(os.environ.get("PGPORT","5432")),
        "dbname": os.environ.get("PGDATABASE"),
        "user": os.environ.get("PGUSER"),
        "password": os.environ.get("PGPASSWORD"),
    }
    with psycopg.connect(**cfg, connect_timeout=8) as conn:
        with conn.cursor() as cur:
            print(f"{BOLD}Tables in public:{RESET}")
            cur.execute("""
                SELECT table_name FROM information_schema.tables
                WHERE table_schema='public' ORDER BY table_name
            """)
            tables = [r[0] for r in cur.fetchall()]
            for t in tables:
                cur.execute(f'SELECT count(*) FROM "{t}"')
                n = cur.fetchone()[0]
                print(f"  {t:<22} {n:>10,} rows")
            print()

            if 'company' in tables:
                cur.execute("SELECT symbol, name, themes FROM company ORDER BY symbol")
                print(f"{BOLD}company:{RESET}")
                for r in cur.fetchall():
                    print(f"  {r[0]:<7} {r[1]:<28} themes={r[2]}")
                print()

            if 'corporate_action' in tables:
                cur.execute("""
                    SELECT symbol, action_type, ex_date, ratio, description
                    FROM corporate_action ORDER BY symbol, ex_date
                """)
                print(f"{BOLD}corporate_action:{RESET}")
                for r in cur.fetchall():
                    print(f"  {r[0]:<7} {r[1]:<22} {r[2]}  ratio={r[3]}  {r[4]}")
                print()

            if 'earnings_quarter' in tables:
                cur.execute("""
                    SELECT symbol, count(*), min(period_end), max(period_end)
                    FROM earnings_quarter GROUP BY symbol ORDER BY symbol
                """)
                print(f"{BOLD}earnings_quarter — per symbol:{RESET}")
                for r in cur.fetchall():
                    print(f"  {r[0]:<7} rows={r[1]:>3}  {r[2]} -> {r[3]}")

                # Cross-quarter alignment check
                cur.execute("""
                    SELECT calendar_quarter, count(distinct symbol), array_agg(distinct symbol ORDER BY symbol)
                    FROM earnings_quarter
                    WHERE actuals_filing_date IS NOT NULL
                    GROUP BY calendar_quarter ORDER BY calendar_quarter DESC LIMIT 8
                """)
                print(f"\n{BOLD}calendar_quarter alignment (most recent 8):{RESET}")
                for r in cur.fetchall():
                    print(f"  {r[0]:<8} {r[1]} symbols  {r[2]}")
                print()

            # Peek at the view that exposes Beat/Miss/QoQ/YoY on demand
            cur.execute("""
                SELECT table_name FROM information_schema.views
                WHERE table_schema='public'
                  AND table_name='earnings_quarter_with_metrics'
            """)
            if cur.fetchone():
                print(f"{BOLD}earnings_quarter_with_metrics (view) — sample {RESET}")
                cur.execute("""
                    SELECT symbol, quarter_label, calendar_quarter,
                           ROUND((rev_beat_miss_pct*100)::numeric, 2)     AS rev_beat,
                           ROUND((ng_eps_beat_miss_pct*100)::numeric, 2)  AS ng_eps_beat,
                           ROUND((rev_yoy_pct*100)::numeric, 1)           AS rev_yoy,
                           ROUND((ng_eps_yoy_pct*100)::numeric, 1)        AS ng_eps_yoy
                    FROM earnings_quarter_with_metrics
                    WHERE revenue_actual IS NOT NULL
                    ORDER BY period_end DESC LIMIT 8
                """)
                rows = cur.fetchall()
                print(f"  {'Sym':<7}{'Quarter':<13}{'CalQ':<9}"
                      f"{'RevBeat%':>10}{'EPSBeat%':>10}{'RevYoY%':>10}{'EPSYoY%':>10}")
                for r in rows:
                    print(f"  {r[0]:<7}{(r[1] or ''):<13}{(r[2] or ''):<9}"
                          f"{(str(r[3]) if r[3] is not None else '-'):>10}"
                          f"{(str(r[4]) if r[4] is not None else '-'):>10}"
                          f"{(str(r[5]) if r[5] is not None else '-'):>10}"
                          f"{(str(r[6]) if r[6] is not None else '-'):>10}")
                print()

            if 'validation_log' in tables:
                cur.execute("""
                    SELECT severity, count(*) FROM validation_log
                    WHERE passed=FALSE GROUP BY severity
                """)
                print(f"{BOLD}validation_log — recent failures:{RESET}")
                for r in cur.fetchall():
                    color = RED if r[0]=='error' else ''
                    print(f"  {color}{r[0]}{RESET}: {r[1]:,}")

                cur.execute("""
                    SELECT record_key, check_name, observed_value, validated_at
                    FROM validation_log WHERE passed=FALSE
                    ORDER BY validated_at DESC LIMIT 10
                """)
                print(f"\n  Latest 10 failures:")
                for r in cur.fetchall():
                    print(f"    {r[0]:<22} {r[1]:<35} got={r[2]}  at {r[3]}")


if __name__ == "__main__":
    main()
