"""
validate.py — data correctness checks. Pure functions, no DB / network needed.

Layers (each independently runnable):

  L1  Schema-level   — handled by Postgres CHECK / UNIQUE / FK constraints
  L2  Invariants     — math facts within a single row (NI = EPS x shares, etc.)
  L3  Cross-endpoint — same (symbol, period) from two FMP endpoints agrees
  L4  Year/quarter   — calendar_quarter matches the period_end month independently
  L5  SEC EDGAR      — gold standard, runs on user's machine (see verify_sec.py)

A check returns (passed: bool, severity: 'error'|'warn'|'info', message: str).
The ingest aggregates these and ABORTS the run if any 'error' fails — never
silently writes bad data.
"""
from __future__ import annotations
import datetime as dt
from dataclasses import dataclass, field
from typing import Optional, List, Tuple

EPS_TOLERANCE = 0.05       # was 0.02 — accommodates FMP's 2-decimal storage of small post-split EPS
REVENUE_YOY_MAX = 2.0      # > 200% YoY flag (unless documented action)
REVENUE_YOY_MIN = -0.50    # < -50% YoY flag

# Standard month -> calendar quarter
MONTH_TO_QUARTER = {1:1, 2:1, 3:1, 4:2, 5:2, 6:2,
                    7:3, 8:3, 9:3, 10:4, 11:4, 12:4}


@dataclass
class CheckResult:
    name: str
    passed: bool
    severity: str          # 'error' | 'warn' | 'info'
    message: str
    expected: Optional[str] = None
    observed: Optional[str] = None

    def is_blocking(self) -> bool:
        return (not self.passed) and self.severity == 'error'


@dataclass
class ValidationReport:
    record_key: str
    checks: List[CheckResult] = field(default_factory=list)

    def add(self, c: CheckResult):
        self.checks.append(c)

    @property
    def has_errors(self) -> bool:
        return any(c.is_blocking() for c in self.checks)

    @property
    def has_warnings(self) -> bool:
        return any(not c.passed and c.severity == 'warn' for c in self.checks)

    def summary(self) -> str:
        e = sum(1 for c in self.checks if c.is_blocking())
        w = sum(1 for c in self.checks if not c.passed and c.severity == 'warn')
        return f"{self.record_key}: {len(self.checks)} checks, {e} errors, {w} warnings"


# =============================================================================
# L2 - Math invariants on a single row
# =============================================================================

def l2_ni_equals_eps_times_shares(ni_billions, eps, shares_millions,
                                  tolerance=EPS_TOLERANCE) -> CheckResult:
    """gaap_ni (USD billions) / dil_shares (millions) should equal eps_diluted.
    Downgraded to 'warn' (not error): FMP stores epsDiluted with only 2-decimal
    precision, so post-split low-priced EPS often shows 2-7% rounding noise."""
    name = "l2_ni_eq_eps_x_shares"
    if ni_billions is None or eps is None or shares_millions is None:
        return CheckResult(name, True, 'info', "skipped: missing inputs")
    if eps == 0 or shares_millions == 0:
        return CheckResult(name, True, 'info', "skipped: zero divisor")
    derived = (ni_billions * 1e9) / (shares_millions * 1e6)
    err = abs(derived - eps) / abs(eps)
    abs_diff = abs(derived - eps)
    passed = err < tolerance
    # Never a blocking 'error' — a broad universe legitimately diverges here
    # (FMP diluted-share vs EPS-basis mismatches for SPAC/miner/recent-IPO
    # names). Material divergence (>30% relative AND >$0.10 absolute) is a
    # 'warn' that lands in earnings_quarter.data_quality_flags so the row is
    # marked as suspect; tiny near-zero noise (the relative error explodes when
    # EPS≈0) is 'info' and not flagged.
    if passed:
        severity = 'info'
    elif err > 0.30 and abs_diff > 0.10:
        severity = 'warn'
    else:
        severity = 'info'
    return CheckResult(
        name, passed, severity,
        f"NI/shares = {derived:.4f} vs EPS = {eps:.4f} "
        f"(err {err:.2%}, |Δ| {abs_diff:.4f}, tol {tolerance:.0%})",
        expected=f"{eps:.4f}", observed=f"{derived:.4f}",
    )


def l2_revenue_yoy_in_range(rev_this, rev_year_ago,
                            max_yoy=REVENUE_YOY_MAX,
                            min_yoy=REVENUE_YOY_MIN) -> CheckResult:
    """Revenue YoY beyond [-50%, +200%] without documented action is suspicious."""
    name = "l2_revenue_yoy_range"
    if rev_this is None or rev_year_ago in (None, 0):
        return CheckResult(name, True, 'info', "skipped: missing inputs")
    yoy = (rev_this - rev_year_ago) / abs(rev_year_ago)
    passed = min_yoy <= yoy <= max_yoy
    return CheckResult(
        name, passed,
        'warn' if not passed else 'info',     # warn not error — could be legit
        f"YoY revenue = {yoy:+.1%} (band {min_yoy:+.0%} to {max_yoy:+.0%})",
        expected=f"[{min_yoy:+.0%}, {max_yoy:+.0%}]", observed=f"{yoy:+.2%}",
    )


def l2_split_factor_positive(factor) -> CheckResult:
    name = "l2_split_factor_positive"
    if factor is None or factor <= 0:
        return CheckResult(name, False, 'error',
                          f"cumulative_split_factor must be > 0, got {factor}",
                          observed=str(factor))
    return CheckResult(name, True, 'info', f"factor = {factor}")


def l2_revenue_nonneg(rev) -> CheckResult:
    name = "l2_revenue_nonneg"
    if rev is None:
        return CheckResult(name, True, 'info', "skipped: missing")
    if rev < 0:
        # 'warn' not 'error': a few FMP small-cap quarters report tiny negative
        # revenue (contra-revenue / data quirk). We don't block the whole batch;
        # the row is flagged in earnings_quarter.data_quality_flags and the
        # ingest nulls the impossible value (PG L1 CHECK rejects negative).
        return CheckResult(name, False, 'warn',
                          f"revenue must be >= 0, got {rev}", observed=str(rev))
    return CheckResult(name, True, 'info', f"revenue = {rev}")


# =============================================================================
# L3 - Cross-endpoint consistency
# =============================================================================

def l3_eps_actual_matches(earnings_eps_actual, income_eps_diluted,
                          reports_official_non_gaap: str,
                          tolerance=EPS_TOLERANCE) -> CheckResult:
    """
    Cross-check /calendar/earnings-company.epsActual against
    /income-statement.epsDiluted. For "GAAP-only" companies these SHOULD be
    equal — but in practice they diverge in quarters with material one-time
    items (AAPL Q4 FY24 EU tax charge, AMZN 2022 Rivian writedown). These
    are real and informative, not data errors. Downgraded to 'warn'.
    """
    name = "l3_eps_actual_cross_endpoint"
    if earnings_eps_actual is None or income_eps_diluted is None:
        return CheckResult(name, True, 'info', "skipped: missing inputs")
    if reports_official_non_gaap not in ("No",):
        return CheckResult(name, True, 'info',
                          f"skipped: company reports non-GAAP "
                          f"({reports_official_non_gaap})")
    if income_eps_diluted == 0:
        return CheckResult(name, True, 'info', "skipped: zero divisor")
    err = abs(earnings_eps_actual - income_eps_diluted) / abs(income_eps_diluted)
    passed = err < tolerance
    # 'warn' (not 'error'): epsActual often differs from GAAP epsDiluted for
    # quarters with one-time items (e.g. AAPL FQ4 24 EU tax, AMZN 2022 Rivian).
    severity = 'info' if passed else 'warn'
    return CheckResult(
        name, passed, severity,
        f"epsActual {earnings_eps_actual} vs epsDiluted {income_eps_diluted} "
        f"(err {err:.2%}) - likely one-time item",
        expected=str(income_eps_diluted), observed=str(earnings_eps_actual),
    )


# =============================================================================
# L4 - Year / quarter / calendar_quarter sanity
# =============================================================================

def l4_calendar_quarter(period_end: dt.date, expected_calendar_quarter: str
                        ) -> CheckResult:
    """
    Independently compute calendar_quarter from period_end using the
    "-1 calendar month" rule and verify it matches the expected label
    (e.g. 'Q2 2025'). Catches any off-by-one in date math.
    """
    name = "l4_calendar_quarter_mapping"
    # -1 calendar month, handling month boundaries
    y, m = period_end.year, period_end.month
    if m == 1:
        m2, y2 = 12, y - 1
    else:
        m2, y2 = m - 1, y
    q = MONTH_TO_QUARTER[m2]
    computed = f"Q{q} {y2}"
    passed = computed == expected_calendar_quarter
    return CheckResult(
        name, passed,
        'error' if not passed else 'info',
        f"period_end {period_end} -> {computed} "
        f"(expected {expected_calendar_quarter})",
        expected=expected_calendar_quarter, observed=computed,
    )


def l4_fiscal_period_consistent(fiscal_period: str, period_end: dt.date,
                                fiscal_year_end_month: int) -> CheckResult:
    """
    The fiscal period (Q1/Q2/Q3/Q4) of a row should be consistent with the
    company's fiscal calendar AND the period_end date. E.g. AAPL fiscal_year_end
    = September, so Q1 ends in December, Q2 in March, Q3 in June, Q4 in Sep.
    Allows ±1 month for 52/53-week year drift.
    """
    name = "l4_fiscal_period_month"
    if fiscal_period not in ("Q1", "Q2", "Q3", "Q4"):
        return CheckResult(name, True, 'info', f"skipped: period={fiscal_period}")
    q_num = int(fiscal_period[1])
    # For a company whose FY ends in month M, Qn ends in month ((M + 3*(n-Q4))-1) mod 12 + 1
    # Simpler: count quarters forward from FY start
    # FY start month = (M % 12) + 1
    fy_start = (fiscal_year_end_month % 12) + 1
    # Q1 ends fy_start + 2, Q2 ends fy_start + 5, ... (mod 12)
    expected_end_month = ((fy_start - 1) + 3 * q_num - 1) % 12 + 1
    actual_month = period_end.month
    # Allow exact match or ±1 (Saturday fiscal-end / 52-53 week variance)
    diff = min((actual_month - expected_end_month) % 12,
               (expected_end_month - actual_month) % 12)
    passed = diff <= 1
    return CheckResult(
        name, passed,
        'warn' if not passed else 'info',     # warn not error — could be 53-week year
        f"fiscal {fiscal_period} period_end={period_end} (month {actual_month}); "
        f"FY ends month {fiscal_year_end_month}; expected end month "
        f"{expected_end_month} (±1 ok)",
        expected=f"month {expected_end_month}±1", observed=f"month {actual_month}",
    )


def l4_period_end_before_report_date(period_end: dt.date,
                                     report_date: Optional[dt.date]
                                     ) -> CheckResult:
    """Report date should be AFTER period_end (companies report after the
    quarter closes). Anything more than 120 days after is suspicious."""
    name = "l4_report_after_period_end"
    if report_date is None:
        return CheckResult(name, True, 'info', "skipped: report_date null")
    gap = (report_date - period_end).days
    if gap < 0:
        return CheckResult(name, False, 'error',
                          f"report_date {report_date} BEFORE period_end {period_end}",
                          observed=str(gap))
    if gap > 120:
        return CheckResult(name, False, 'warn',
                          f"report_date {report_date} is {gap} days after period_end "
                          f"{period_end} (unusual; normal is 20-60)",
                          observed=str(gap))
    return CheckResult(name, True, 'info', f"gap = {gap} days (normal)")


# =============================================================================
# Aggregate
# =============================================================================

def run_all_checks(*,
    symbol: str, fiscal_year: int, fiscal_period: str,
    period_end: dt.date, report_date: Optional[dt.date],
    expected_calendar_quarter: str,
    fiscal_year_end_month: int,
    revenue_actual: Optional[float],
    gaap_ni: Optional[float],
    gaap_eps_diluted: Optional[float],
    dil_shares: Optional[float],
    cumulative_split_factor: float,
    earnings_endpoint_eps_actual: Optional[float],
    reports_official_non_gaap: str,
    revenue_year_ago: Optional[float] = None,
    ni_to_common: Optional[float] = None,
) -> ValidationReport:
    key = f"{symbol}/{fiscal_year}/{fiscal_period}"
    r = ValidationReport(key)

    r.add(l2_split_factor_positive(cumulative_split_factor))
    r.add(l2_revenue_nonneg(revenue_actual))
    # EPS reconciles against net income ATTRIBUTABLE TO COMMON (after preferred
    # dividends / NCI / mandatory-convertible adjustments), i.e. FMP's
    # bottomLineNetIncome — NOT total netIncome. Fall back to gaap_ni when the
    # caller has no to-common figure (keeps the self-test / clean-cap path).
    r.add(l2_ni_equals_eps_times_shares(
        ni_to_common if ni_to_common is not None else gaap_ni,
        gaap_eps_diluted, dil_shares))
    if revenue_year_ago is not None:
        r.add(l2_revenue_yoy_in_range(revenue_actual, revenue_year_ago))

    r.add(l3_eps_actual_matches(earnings_endpoint_eps_actual,
                                gaap_eps_diluted, reports_official_non_gaap))

    r.add(l4_calendar_quarter(period_end, expected_calendar_quarter))
    r.add(l4_fiscal_period_consistent(fiscal_period, period_end,
                                      fiscal_year_end_month))
    r.add(l4_period_end_before_report_date(period_end, report_date))

    return r


# =============================================================================
# Quick self-test (runnable: python validate.py)
# =============================================================================

if __name__ == "__main__":
    # Real AAPL FQ3 2025: period_end 2025-06-28, report 2025-07-31
    # Revenue 94.036B, NI 23.434B, EPS Diluted 1.57, shares 14,948.179M
    r = run_all_checks(
        symbol="AAPL", fiscal_year=2025, fiscal_period="Q3",
        period_end=dt.date(2025, 6, 28),
        report_date=dt.date(2025, 7, 31),
        expected_calendar_quarter="Q2 2025",
        fiscal_year_end_month=9,
        revenue_actual=94.036,
        gaap_ni=23.434,
        gaap_eps_diluted=1.57,
        dil_shares=14948.179,
        cumulative_split_factor=1.0,
        earnings_endpoint_eps_actual=1.57,
        reports_official_non_gaap="No",
        revenue_year_ago=81.797,  # AAPL FQ3 FY23 (Apr-Jun 2023)
    )
    print(r.summary())
    for c in r.checks:
        flag = 'OK ' if c.passed else f'{c.severity.upper():<5}'
        print(f"  {flag} {c.name:<35}  {c.message}")
    assert not r.has_errors, "AAPL FQ3 2025 should pass all checks"

    # NVDA Q4 FY26: period_end 2026-01-25, calendar_quarter must be Q4 2025
    r = run_all_checks(
        symbol="NVDA", fiscal_year=2026, fiscal_period="Q4",
        period_end=dt.date(2026, 1, 25),
        report_date=dt.date(2026, 2, 25),
        expected_calendar_quarter="Q4 2025",
        fiscal_year_end_month=1,
        revenue_actual=68.127,
        gaap_ni=42.960,
        gaap_eps_diluted=1.76,
        dil_shares=24432.0,
        cumulative_split_factor=1.0,
        earnings_endpoint_eps_actual=1.62,  # NVDA non-GAAP, differs from GAAP - should skip L3
        reports_official_non_gaap="Yes (def changed Q1 FY27)",
    )
    print(); print(r.summary())
    for c in r.checks:
        flag = 'OK ' if c.passed else f'{c.severity.upper():<5}'
        print(f"  {flag} {c.name:<35}  {c.message}")
    assert not r.has_errors, "NVDA Q4 FY26 should pass all checks"

    # Negative test: deliberately wrong calendar_quarter
    r = run_all_checks(
        symbol="NVDA", fiscal_year=2026, fiscal_period="Q4",
        period_end=dt.date(2026, 1, 25),
        report_date=dt.date(2026, 2, 25),
        expected_calendar_quarter="Q1 2026",   # WRONG on purpose
        fiscal_year_end_month=1,
        revenue_actual=68.127, gaap_ni=42.960, gaap_eps_diluted=1.76,
        dil_shares=24432.0, cumulative_split_factor=1.0,
        earnings_endpoint_eps_actual=1.62,
        reports_official_non_gaap="Yes (def changed Q1 FY27)",
    )
    print(); print("NEGATIVE TEST (wrong calendar_quarter):", r.summary())
    assert r.has_errors, "should fail on calendar_quarter mismatch"
    print("  -> correctly detected calendar_quarter mismatch")

    print("\nAll self-tests pass.")
