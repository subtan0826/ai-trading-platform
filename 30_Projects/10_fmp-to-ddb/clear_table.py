"""
clear_table.py — Safely empty dfs://market_daily/us_daily_kline.

Option A: delete all rows, KEEP the table structure (schema + partitions).

Safety gates, in order:
  1. Connect + confirm table exists, show current row count.
  2. BACKUP: export every row to a timestamped CSV next to this script.
  3. VERIFY backup row count == table row count. Abort if mismatch.
  4. Require the user to type the row count to confirm (typo-proof).
  5. delete from pt  (rows gone, schema stays).
  6. Re-read count to prove it's now 0.

Nothing is deleted until the backup is verified and you confirm.

    python clear_table.py
"""
import os
import sys
import csv
import subprocess
import datetime as dt
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def _ensure(pkg):
    try:
        return __import__(pkg)
    except ImportError:
        print(f"[setup] installing {pkg} ...")
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


GREEN = "\033[32m"; RED = "\033[31m"; YELLOW = "\033[33m"
DIM = "\033[2m"; BOLD = "\033[1m"; RESET = "\033[0m"

DB = "dfs://market_daily"
TABLE = "us_daily_kline"


def main():
    load_dotenv()
    host = os.environ.get("DDB_HOST", "localhost")
    port = int(os.environ.get("DDB_DATA_PORT", "8902"))
    user = os.environ.get("DDB_USER", "admin")
    pwd  = os.environ.get("DDB_PASSWORD", "")

    s = ddb.Session()
    if not s.connect(host, port, user, pwd, reconnect=False):
        print(f"{RED}cannot connect to {host}:{port}{RESET}")
        sys.exit(1)

    handle = f'loadTable("{DB}", "{TABLE}")'

    # ---- Gate 1: existence + count ----
    if not s.run(f'existsTable("{DB}", "{TABLE}")'):
        print(f"{RED}{DB}/{TABLE} does not exist. Nothing to do.{RESET}")
        sys.exit(0)
    n_rows = s.run(f'exec count(*) from {handle}')
    print(f"{BOLD}Target: {DB}/{TABLE}{RESET}")
    print(f"  current rows: {n_rows:,}")
    if n_rows == 0:
        print(f"{GREEN}Already empty. Nothing to do.{RESET}")
        sys.exit(0)

    # ---- Gate 2: backup to CSV ----
    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = Path(__file__).with_name(f"backup_{TABLE}_{ts}.csv")
    print(f"\n{YELLOW}Backing up {n_rows:,} rows to:{RESET}")
    print(f"  {backup_path}")
    df = s.run(f'select * from {handle}')   # pull full table to pandas
    df.to_csv(backup_path, index=False)
    print(f"{GREEN}  backup written.{RESET}")

    # ---- Gate 3: verify backup row count ----
    with open(backup_path, "r", encoding="utf-8") as f:
        backup_rows = sum(1 for _ in f) - 1   # minus header
    print(f"  backup rows: {backup_rows:,}  (table: {n_rows:,})")
    if backup_rows != n_rows:
        print(f"{RED}BACKUP ROW COUNT MISMATCH — aborting, nothing deleted.{RESET}")
        sys.exit(1)
    print(f"{GREEN}  backup verified.{RESET}")

    # ---- Gate 4: typed confirmation ----
    print(f"\n{YELLOW}{BOLD}About to DELETE ALL {n_rows:,} rows from "
          f"{DB}/{TABLE}.{RESET}")
    print(f"{DIM}(table structure / schema / partitions are kept; "
          f"backup saved above){RESET}")
    answer = input(f"Type the row count ({n_rows}) to confirm, or anything "
                   f"else to abort: ").strip()
    if answer != str(n_rows):
        print(f"{DIM}Aborted. Nothing deleted. Backup remains at "
              f"{backup_path.name}.{RESET}")
        sys.exit(0)

    # ---- Gate 5: delete ----
    s.run(f'delete from {handle}')
    # DFS delete is async-committed; force a recount
    after = s.run(f'exec count(*) from {handle}')
    print(f"\n{GREEN}Deleted.{RESET}  rows now: {after}")
    if after == 0:
        print(f"{GREEN}Table emptied successfully. Schema preserved.{RESET}")
        # Show the schema is still intact
        cols = s.run(f'schema({handle}).colDefs.name')
        print(f"{DIM}columns still present: {list(cols)}{RESET}")
    else:
        print(f"{YELLOW}rows remaining: {after} — delete may be committing "
              f"asynchronously; re-run inspect_db.py to confirm.{RESET}")

    s.close()


if __name__ == "__main__":
    main()
