# daily_update.ps1
# ---------------------------------------------------------------------------
# Daily data refresh, run by Windows Task Scheduler ON THIS MACHINE (DolphinDB
# and PostgreSQL are localhost — a cloud agent cannot reach them).
#   1) DolphinDB: per-symbol incremental daily K-line  (--incremental)
#   2) PostgreSQL: earnings + consensus snapshot        (--stocks)
# Both pull their universe from Stocks.base and are idempotent (safe to re-run).
# Logs to 30_Projects\_logs\daily_<timestamp>.log.
$ErrorActionPreference = "Continue"

$Repo = "D:\Subtan\ai-trading-platform"
$Py   = "C:\Users\clori\AppData\Local\Programs\Python\Python312\python.exe"
$Ddb  = Join-Path $Repo "30_Projects\10_fmp-to-ddb\fetch_fmp_to_ddb.py"
$Pg   = Join-Path $Repo "30_Projects\20_fmp-to-pg\fetch_fmp_to_pg.py"

$LogDir = Join-Path $Repo "30_Projects\_logs"
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Force -Path $LogDir | Out-Null }
$Log = Join-Path $LogDir ("daily_{0}.log" -f (Get-Date -Format "yyyy-MM-dd_HHmmss"))

function Log([string]$m) { $m | Tee-Object -FilePath $Log -Append }

Set-Location $Repo
Log ("===== daily update START {0} =====" -f (Get-Date -Format o))

Log "--- [1/2] DolphinDB incremental (daily K-line) ---"
& $Py $Ddb --stocks --incremental --save-ddb 2>&1 | Tee-Object -FilePath $Log -Append
$ddbExit = $LASTEXITCODE
Log ("--- DolphinDB exit = {0} ---" -f $ddbExit)

Log "--- [2/2] PostgreSQL --stocks (earnings + consensus) ---"
& $Py $Pg --stocks --force-on-warn 2>&1 | Tee-Object -FilePath $Log -Append
$pgExit = $LASTEXITCODE
Log ("--- PostgreSQL exit = {0} ---" -f $pgExit)

Log ("===== daily update DONE  ddb={0} pg={1}  {2} =====" -f $ddbExit, $pgExit, (Get-Date -Format o))

# Keep only the last 30 log files
Get-ChildItem $LogDir -Filter "daily_*.log" | Sort-Object LastWriteTime -Descending |
    Select-Object -Skip 30 | Remove-Item -Force -ErrorAction SilentlyContinue

if (($ddbExit -ne 0) -or ($pgExit -ne 0)) { exit 1 } else { exit 0 }
