"""
verify_after_insert.py — read DDB back and sanity-check the FMP ingest.

Checks:
  * per-symbol row counts + date ranges
  * AAPL dividend-adjusted anchors (total-return口径):
      2016-06-02 close ~ 22.25     (matches user's broker)
      2020-08-31 4:1 split day smooth, pct ~ +3.4% (no -75% cliff)
  * MA columns populated where enough history exists
  * partition scheme (printed so a future fresh-install can match it)

    python verify_after_insert.py
"""
import os
import sys
import subprocess
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def _ensure(pkg):
    try:
        return __import__(pkg)
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", pkg])
        return __import__(pkg)


_ensure("dolphindb")
import dolphindb as ddb


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


GREEN = "\033[32m"; RED = "\033[31m"; DIM = "\033[2m"; BOLD = "\033[1m"; RESET = "\033[0m"
DB = "dfs://market_daily"; TB = "us_daily_kline"


def main():
    load_dotenv()
    host = os.environ.get("DDB_HOST", "localhost")
    port = int(os.environ.get("DDB_DATA_PORT", "8902"))
    user = os.environ.get("DDB_USER", "admin")
    pwd  = os.environ.get("DDB_PASSWORD", "")

    s = ddb.Session()
    if not s.connect(host, port, user, pwd, reconnect=False):
        print(f"{RED}cannot connect {host}:{port}{RESET}"); sys.exit(1)
    h = f'loadTable("{DB}", "{TB}")'

    print(f"{BOLD}per-symbol summary{RESET}")
    summ = s.run(f'select count(*) as rows, min(date) as first, max(date) as last, '
                 f'min(close) as min_close, max(close) as max_close '
                 f'from {h} group by symbol order by symbol')
    print(summ)
    total = s.run(f'exec count(*) from {h}')
    print(f"\ngrand total rows: {total:,}")

    # AAPL anchors
    print(f"\n{BOLD}AAPL total-return anchors{RESET}")
    for d, expect in [("2016.06.02", 22.25), ("2020.08.31", 125.15),
                      ("2026.06.02", None)]:
        try:
            r = s.run(f'select date, close, pct_change, ma20, ma250 from {h} '
                      f'where symbol=`AAPL, date={d}')
            if r is None or len(r) == 0:
                print(f"  {d}: (no row)")
                continue
            close = r["close"][0]
            tag = ""
            if expect is not None:
                ok = abs(close - expect) / expect < 0.02
                tag = f"{GREEN}OK{RESET}" if ok else f"{RED}OFF (expect ~{expect}){RESET}"
            print(f"  {d}: close={close}  pct={r['pct_change'][0]}  "
                  f"ma20={r['ma20'][0]}  ma250={r['ma250'][0]}  {tag}")
        except Exception as e:
            print(f"  {d}: query failed: {e}")

    # MA coverage
    print(f"\n{BOLD}MA250 coverage (rows with ma250 not null){RESET}")
    cov = s.run(f'select count(ma250) as with_ma250, count(*) as total '
                f'from {h} where symbol=`AAPL')
    print(cov)

    # Partition scheme (capture for fresh-install reproducibility)
    print(f"\n{BOLD}partition scheme{RESET}")
    try:
        print(s.run(f'schema(database("{DB}")).partitionType'))
        print(s.run(f'schema(database("{DB}")).partitionSchema'))
    except Exception as e:
        print(f"  (could not read: {e})")

    s.close()
    print(f"\n{GREEN}done.{RESET}")


if __name__ == "__main__":
    main()
