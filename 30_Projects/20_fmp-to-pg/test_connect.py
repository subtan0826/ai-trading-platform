"""
test_connect.py — PostgreSQL connectivity smoke test.

Auto-installs psycopg, reads .env, runs SELECT version(), lists tables.

    cp .env.example .env  &&  python test_connect.py
"""
import os, sys, subprocess
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def _ensure(pkg, import_name=None):
    try:
        return __import__(import_name or pkg)
    except ImportError:
        print(f"[setup] installing {pkg} ...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", pkg])
        return __import__(import_name or pkg)


_ensure("psycopg[binary]", "psycopg")
import psycopg


def load_dotenv():
    p = Path(__file__).with_name(".env")
    if not p.exists():
        print("WARNING: no .env (copy .env.example -> .env first).")
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


GREEN="\033[32m"; RED="\033[31m"; DIM="\033[2m"; BOLD="\033[1m"; RESET="\033[0m"


def main():
    load_dotenv()
    cfg = {
        "host":     os.environ.get("PGHOST", "localhost"),
        "port":     int(os.environ.get("PGPORT", "5432")),
        "dbname":   os.environ.get("PGDATABASE"),
        "user":     os.environ.get("PGUSER"),
        "password": os.environ.get("PGPASSWORD"),
    }
    if not cfg["dbname"] or not cfg["user"] or not cfg["password"]:
        print(f"{RED}missing PG config (PGDATABASE/PGUSER/PGPASSWORD).{RESET}")
        sys.exit(1)
    print(f"{BOLD}PostgreSQL connectivity test{RESET}")
    print(f"{DIM}  host={cfg['host']}:{cfg['port']}  db={cfg['dbname']}  user={cfg['user']}{RESET}")

    try:
        with psycopg.connect(**cfg, connect_timeout=8) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT version()")
                ver = cur.fetchone()[0]
                print(f"{GREEN}OK   {RESET}TCP + auth ok")
                print(f"{GREEN}OK   {RESET}server: {ver.split(',')[0]}")
                cur.execute("SELECT current_database(), current_user, current_setting('server_version_num')")
                db, usr, ver_num = cur.fetchone()
                print(f"{GREEN}OK   {RESET}database={db}  user={usr}  version_num={ver_num}")
                if int(ver_num) < 130000:
                    print(f"{RED}WARN {RESET}PG version < 13 — GENERATED columns unavailable, schema will fail")

                cur.execute(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema='public' ORDER BY table_name")
                tables = [r[0] for r in cur.fetchall()]
                if tables:
                    print(f"{DIM}     existing tables in public: {tables}{RESET}")
                else:
                    print(f"{DIM}     no tables in public yet (run schema/001_init.sql to create them){RESET}")
        print(f"\n{GREEN}done.{RESET}")
        return 0
    except psycopg.OperationalError as e:
        print(f"{RED}FAIL {RESET}cannot connect: {e}")
        return 1
    except Exception as e:
        print(f"{RED}FAIL {RESET}{type(e).__name__}: {e}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
