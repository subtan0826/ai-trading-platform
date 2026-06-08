"""
inspect_db.py — READ-ONLY inspection of dfs://market_daily before any delete.

Lists every table in the database, its schema, row count, symbol count, and
date range. Touches nothing. Run this first so you can see exactly what a
"clear" would destroy.

    python inspect_db.py
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


GREEN = "\033[32m"; RED = "\033[31m"; DIM = "\033[2m"; BOLD = "\033[1m"; RESET = "\033[0m"

DB = "dfs://market_daily"


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
    print(f"{BOLD}Inspecting {DB}  (READ ONLY — nothing is modified){RESET}\n")

    # Does the DB exist?
    try:
        exists = s.run(f'existsDatabase("{DB}")')
    except Exception as e:
        print(f"{RED}existsDatabase failed: {e}{RESET}")
        sys.exit(1)
    if not exists:
        print(f"{DIM}{DB} does not exist. Nothing to inspect.{RESET}")
        sys.exit(0)

    # List tables in the database
    try:
        tables = s.run(f'getTables(database("{DB}"))')
    except Exception as e:
        print(f"{DIM}getTables failed ({e}); trying schema fallback...{RESET}")
        tables = []
    if tables is None:
        tables = []
    tables = list(tables)

    if not tables:
        print(f"{DIM}No tables found in {DB}.{RESET}")
        sys.exit(0)

    print(f"Tables in {DB}: {tables}\n")

    for t in tables:
        print(f"{BOLD}── Table: {t} ──{RESET}")
        handle = f'loadTable("{DB}", "{t}")'
        # Schema
        try:
            schema = s.run(f'schema({handle}).colDefs')
            print(f"{DIM}columns:{RESET}")
            # schema is a table with name/typeString columns
            names = schema["name"]
            types = schema["typeString"]
            for n, ty in zip(names, types):
                print(f"     {n:<16} {ty}")
        except Exception as e:
            print(f"{DIM}  schema read failed: {e}{RESET}")
        # Stats
        try:
            n_rows = s.run(f'exec count(*) from {handle}')
            print(f"  rows: {n_rows:,}")
        except Exception as e:
            print(f"  rows: (failed: {e})")
        try:
            n_sym = s.run(f'exec count(distinct symbol) from {handle}')
            print(f"  distinct symbols: {n_sym}")
        except Exception:
            pass
        try:
            dr = s.run(f'select min(date) as first, max(date) as last from {handle}')
            print(f"  date range: {dr['first'][0]} -> {dr['last'][0]}")
        except Exception:
            pass
        # Sample symbols
        try:
            syms = s.run(f'exec distinct symbol from {handle} limit 20')
            print(f"  sample symbols: {list(syms)}")
        except Exception:
            pass
        print()

    s.close()
    print(f"{GREEN}Done. Nothing was modified.{RESET}")


if __name__ == "__main__":
    main()
