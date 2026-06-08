"""
test_connect.py — Sanity-check connectivity to a DolphinDB cluster.

Tests all 3 endpoints (controller / data / compute) in your cluster:
  * Controller  (port 8900 by default) — cluster orchestration
  * Data node   (port 8902 by default) — where DFS tables live; we'll write here
  * Compute node(port 8903 by default) — optional, for compute-heavy queries

How to run (any OS):
    1. Put this file in an empty folder.
    2. Copy `.env.example` -> `.env` and fill in your DDB credentials.
    3. python test_connect.py

The script auto-installs `dolphindb` on first run, reads .env without any
extra dependency, and prints a clean PASS/FAIL summary per node.
"""
import os
import sys
import subprocess
from pathlib import Path


# Force UTF-8 stdout so Windows GBK consoles can print box-drawing chars.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def _ensure(pkg):
    try:
        return __import__(pkg)
    except ImportError:
        print(f"[setup] installing {pkg} ...")
        subprocess.check_call([sys.executable, "-m", "pip", "install",
                               "-q", pkg])
        return __import__(pkg)


_ensure("dolphindb")
import dolphindb as ddb


# ---- Tiny .env loader (no python-dotenv dep) ----------------------------
def load_dotenv():
    here = Path(__file__).resolve().parent
    p = next((d / ".env" for d in (here, *here.parents) if (d / ".env").is_file()),
             here / ".env")
    if not p.exists():
        print("WARNING: no .env file found next to this script.\n"
              "         Copy .env.example -> .env and fill in your values,\n"
              "         or set the env vars in your shell before running.")
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


# ---- Colors -------------------------------------------------------------
GREEN = "\033[32m"; RED = "\033[31m"; CYAN = "\033[36m"
DIM = "\033[2m"; BOLD = "\033[1m"; RESET = "\033[0m"


def _ok(msg):   print(f"{GREEN}OK   {RESET}{msg}")
def _fail(msg): print(f"{RED}FAIL {RESET}{msg}")
def _info(msg): print(f"{DIM}     {msg}{RESET}")


def probe_node(label, host, port, user, password, timeout_s=8):
    """Try to connect, run a few diagnostic queries, return True/False."""
    print(f"\n{BOLD}── {label}  {host}:{port} ──{RESET}")
    s = ddb.Session()
    try:
        connected = s.connect(host, port, user, password, reconnect=False)
        if not connected:
            _fail(f"connect() returned False (check host/port/credentials)")
            return False
        _ok("TCP + auth ok")
    except Exception as e:
        _fail(f"connect raised: {type(e).__name__}: {e}")
        return False

    checks = [
        ("version()",           "DDB version"),
        ("getNodeAlias()",      "node alias"),
        ("getControllerAlias()", "controller alias"),
        ("getDataNodes()",      "data node list"),
    ]
    for script, label in checks:
        try:
            result = s.run(script)
            _ok(f"{label}: {result}")
        except Exception as e:
            _info(f"{label}: skipped ({type(e).__name__}: {str(e)[:80]})")

    # Verify we can run a trivial computation
    try:
        r = s.run("a = 1..5; sum(a)")
        if r == 15:
            _ok("compute roundtrip works (sum(1..5)=15)")
        else:
            _fail(f"compute roundtrip wrong: got {r}, expected 15")
    except Exception as e:
        _fail(f"compute roundtrip raised: {e}")

    # Show what DFS databases already exist (handy to see before we write)
    try:
        dbs = s.run("getClusterDFSDatabases()")
        if dbs is None or len(dbs) == 0:
            _info("DFS databases: none yet")
        else:
            _info(f"DFS databases: {list(dbs)}")
    except Exception as e:
        _info(f"DFS list: skipped ({type(e).__name__})")

    try:
        s.close()
    except Exception:
        pass
    return True


def main():
    load_dotenv()

    host = os.environ.get("DDB_HOST")
    user = os.environ.get("DDB_USER")
    pwd  = os.environ.get("DDB_PASSWORD")
    ports = {
        "Controller": os.environ.get("DDB_CONTROLLER_PORT"),
        "Data node":  os.environ.get("DDB_DATA_PORT"),
        "Compute":    os.environ.get("DDB_COMPUTE_PORT"),
    }
    cluster_mode = os.environ.get("DDB_CLUSTER_MODE", "unknown")

    print(f"{BOLD}DolphinDB connectivity test{RESET}")
    _info(f"host = {host}   cluster_mode = {cluster_mode}")
    _info(f"user = {user}")

    missing = [k for k, v in ports.items() if not v] + \
              ([] if host else ["DDB_HOST"]) + \
              ([] if user else ["DDB_USER"]) + \
              ([] if pwd else ["DDB_PASSWORD"])
    if missing:
        _fail(f"missing config: {missing}")
        print("Copy .env.example -> .env and fill it in.")
        sys.exit(2)

    results = {}
    for label, port in ports.items():
        results[label] = probe_node(label, host, int(port), user, pwd)

    print(f"\n{BOLD}── Summary ──{RESET}")
    for label, ok in results.items():
        (_ok if ok else _fail)(f"{label}: {'CONNECTED' if ok else 'FAILED'}")

    if not all(results.values()):
        print(f"\n{DIM}Hint: in a typical FMP -> DDB pipeline we write to the "
              f"DATA node (8902). Controller (8900) is for cluster admin. "
              f"Compute (8903) is optional. As long as the Data node is "
              f"green, you can move on to the next step.{RESET}")

    sys.exit(0 if results.get("Data node") else 1)


if __name__ == "__main__":
    main()
