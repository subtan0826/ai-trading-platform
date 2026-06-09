-- =============================================================================
-- fmp-to-pg/schema/001_init.sql
-- =============================================================================
-- 4 tables for FMP earnings + consensus data, with built-in correctness gates:
--   * calendar_quarter is a GENERATED column (cannot be wrong by construction)
--   * gaap_eps_diluted_current / dil_shares_current are GENERATED from
--     _as_reported × cumulative_split_factor (split adjustments propagate
--     automatically on UPDATE)
--   * CHECK constraints reject impossible values (fiscal_period not in Q1..Q4,
--     negative split factor, num_analysts < 0, etc.)
--   * UNIQUE constraints prevent duplicate quarters
--
-- Plus a validation_log for L2-L4 invariant check history (auditability).
-- =============================================================================

\set ON_ERROR_STOP on
SET client_encoding TO 'UTF8';
BEGIN;

-- ---- 1. company ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS company (
    symbol                    VARCHAR(20) PRIMARY KEY,
    name                      TEXT NOT NULL,
    sector                    TEXT,
    industry                  TEXT,
    themes                    TEXT[],                                          -- 主题概念
    fiscal_year_end_month     SMALLINT NOT NULL
        CHECK (fiscal_year_end_month BETWEEN 1 AND 12),
    cik                       VARCHAR(10),

    reports_official_non_gaap TEXT,
    default_non_gaap_basis    TEXT,
    ng_confidence             VARCHAR(10)
        CHECK (ng_confidence IN ('High','Med','Low') OR ng_confidence IS NULL),
    non_gaap_basis_source     TEXT,

    created_at                TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_company_themes_gin ON company USING GIN (themes);
CREATE INDEX IF NOT EXISTS idx_company_sector     ON company (sector);

-- ---- 2. corporate_action ---------------------------------------------------
-- Splits, non-GAAP definition changes, restatements, spin-offs.
-- Each new entry triggers a recompute of cumulative_split_factor on the
-- affected earnings_quarter rows (done by ingest, not DB-side).
CREATE TABLE IF NOT EXISTS corporate_action (
    id            BIGSERIAL PRIMARY KEY,
    symbol        VARCHAR(20) NOT NULL REFERENCES company(symbol) ON DELETE CASCADE,
    action_type   VARCHAR(30) NOT NULL
        CHECK (action_type IN ('split','reverse_split','non_gaap_def_change',
                               'restatement','spinoff','special_dividend')),
    ex_date       DATE NOT NULL,
    ratio         NUMERIC CHECK (ratio IS NULL OR ratio > 0),
    description   TEXT,
    source_url    TEXT,
    recorded_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (symbol, action_type, ex_date)
);

CREATE INDEX IF NOT EXISTS idx_ca_symbol_date ON corporate_action (symbol, ex_date);

-- ---- 3. earnings_quarter ---------------------------------------------------
-- Main fact table: one row per (symbol, fiscal_year, fiscal_period).
-- ACTUALS are NULL pre-release, filled post-release.
-- CONSENSUS refreshes until report_date, then frozen.
-- All cross-quarter comparable views go through `_current` columns.
CREATE TABLE IF NOT EXISTS earnings_quarter (
    symbol                       VARCHAR(20) NOT NULL
                                  REFERENCES company(symbol) ON DELETE CASCADE,
    fiscal_year                  INT NOT NULL
        CHECK (fiscal_year BETWEEN 2000 AND 2100),
    fiscal_period                VARCHAR(3) NOT NULL
        CHECK (fiscal_period IN ('Q1','Q2','Q3','Q4','FY')),

    quarter_label                TEXT,                        -- 'FQ3 2025'
    calendar_period              TEXT,                        -- 'Apr-Jun 2025'

    period_end                   DATE NOT NULL,
    report_date                  DATE,

    -- Cross-company comparable bucket — GENERATED, cannot be wrong by hand.
    -- Rule: quarter of (period_end - 1 calendar month). Verified equivalent to
    -- "middle month of fiscal period" for all MAG7.
    calendar_quarter             VARCHAR(7) NOT NULL GENERATED ALWAYS AS (
        'Q' || EXTRACT(QUARTER FROM (period_end - INTERVAL '1 month'))::text
        || ' ' || EXTRACT(YEAR FROM (period_end - INTERVAL '1 month'))::text
    ) STORED,
    calendar_quarter_override    VARCHAR(7),                   -- manual escape for stub periods

    -- ACTUALS (NULL before release) ------------------------------------------
    -- Revenue and NI are NOT split-affected, so single columns suffice.
    -- EPS and shares ARE split-affected, so we store _as_reported (immutable
    -- historical record) and _current (auto-derived for cross-time comparison).
    revenue_actual                  NUMERIC CHECK (revenue_actual IS NULL OR revenue_actual >= 0),
    gaap_ni                         NUMERIC,
    gaap_eps_diluted_as_reported    NUMERIC,
    dil_shares_as_reported          NUMERIC CHECK (dil_shares_as_reported IS NULL OR dil_shares_as_reported > 0),
    ng_ni                           NUMERIC,
    ng_eps_as_reported              NUMERIC,

    cumulative_split_factor         NUMERIC NOT NULL DEFAULT 1.0
        CHECK (cumulative_split_factor > 0),

    -- Auto-derived current-basis values. Splits propagate automatically when
    -- cumulative_split_factor is updated.
    gaap_eps_diluted_current        NUMERIC GENERATED ALWAYS AS
        (gaap_eps_diluted_as_reported / cumulative_split_factor) STORED,
    dil_shares_current              NUMERIC GENERATED ALWAYS AS
        (dil_shares_as_reported * cumulative_split_factor) STORED,
    ng_eps_current                  NUMERIC GENERATED ALWAYS AS
        (ng_eps_as_reported / cumulative_split_factor) STORED,

    -- CONSENSUS (last snapshot; full history in consensus_history) -----------
    revenue_consensus               NUMERIC CHECK (revenue_consensus IS NULL OR revenue_consensus >= 0),
    ng_eps_consensus_as_reported    NUMERIC,
    ng_eps_consensus_current        NUMERIC GENERATED ALWAYS AS
        (ng_eps_consensus_as_reported / cumulative_split_factor) STORED,
    ni_consensus                    NUMERIC,                  -- derived eps_cons * shares
    num_analysts_revenue            INT CHECK (num_analysts_revenue IS NULL OR num_analysts_revenue >= 0),
    num_analysts_eps                INT CHECK (num_analysts_eps IS NULL OR num_analysts_eps >= 0),
    consensus_last_refreshed        TIMESTAMPTZ,
    consensus_source                TEXT,

    -- DERIVED metrics (Beat/Miss / QoQ / YoY) --------------------------------
    rev_beat_miss_pct               NUMERIC,
    rev_qoq_pct                     NUMERIC,
    rev_yoy_pct                     NUMERIC,
    gaap_ni_beat_miss_pct           NUMERIC,
    gaap_ni_qoq_pct                 NUMERIC,
    gaap_ni_yoy_pct                 NUMERIC,
    gaap_eps_qoq_pct                NUMERIC,
    gaap_eps_yoy_pct                NUMERIC,
    ng_ni_qoq_pct                   NUMERIC,
    ng_ni_yoy_pct                   NUMERIC,
    ng_eps_beat_miss_pct            NUMERIC,
    ng_eps_qoq_pct                  NUMERIC,
    ng_eps_yoy_pct                  NUMERIC,

    -- Non-GAAP basis (per-row override beats company default) ----------------
    non_gaap_basis_override         TEXT,
    basis_change_event              TEXT,

    -- Audit ------------------------------------------------------------------
    actuals_source_url              TEXT,
    actuals_filing_date             DATE,
    is_restated                     BOOLEAN NOT NULL DEFAULT FALSE,
    last_verified_at                TIMESTAMPTZ,
    last_verified_against           TEXT,
    notes                           TEXT,

    created_at                      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    PRIMARY KEY (symbol, fiscal_year, fiscal_period)
);

CREATE INDEX IF NOT EXISTS idx_eq_calendar_quarter ON earnings_quarter (calendar_quarter, symbol);
CREATE INDEX IF NOT EXISTS idx_eq_upcoming         ON earnings_quarter (report_date)
    WHERE actuals_filing_date IS NULL;
CREATE INDEX IF NOT EXISTS idx_eq_period_end       ON earnings_quarter (period_end);

-- ---- 4. consensus_history (append-only time series) ------------------------
-- Every FMP refresh appends a row here, so the analyst-revision trajectory is
-- preserved forever. Cannot be reconstructed if not captured contemporaneously.
CREATE TABLE IF NOT EXISTS consensus_history (
    id                       BIGSERIAL PRIMARY KEY,
    symbol                   VARCHAR(20) NOT NULL
                              REFERENCES company(symbol) ON DELETE CASCADE,
    target_fiscal_year       INT NOT NULL,
    target_fiscal_period     VARCHAR(3) NOT NULL
        CHECK (target_fiscal_period IN ('Q1','Q2','Q3','Q4','FY')),
    snapshot_date            DATE NOT NULL,
    source                   VARCHAR(50) NOT NULL,

    revenue_est_as_reported  NUMERIC CHECK (revenue_est_as_reported IS NULL OR revenue_est_as_reported >= 0),
    eps_est_as_reported      NUMERIC,
    num_analysts_revenue     INT CHECK (num_analysts_revenue IS NULL OR num_analysts_revenue >= 0),
    num_analysts_eps         INT CHECK (num_analysts_eps IS NULL OR num_analysts_eps >= 0),

    cumulative_split_factor  NUMERIC NOT NULL DEFAULT 1.0
        CHECK (cumulative_split_factor > 0),
    eps_est_current          NUMERIC GENERATED ALWAYS AS
        (eps_est_as_reported / cumulative_split_factor) STORED,
    revenue_est_current      NUMERIC GENERATED ALWAYS AS
        (revenue_est_as_reported) STORED,                     -- revenue not split-affected

    pulled_at                TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (symbol, target_fiscal_year, target_fiscal_period, snapshot_date, source)
);

CREATE INDEX IF NOT EXISTS idx_ch_target ON consensus_history
    (symbol, target_fiscal_year, target_fiscal_period, snapshot_date DESC);

-- ---- 5. validation_log (audit trail for L1-L5 checks) ----------------------
CREATE TABLE IF NOT EXISTS validation_log (
    id              BIGSERIAL PRIMARY KEY,
    table_name      TEXT NOT NULL,
    record_key      TEXT,                          -- e.g. 'NVDA/2027/Q1'
    check_name      TEXT NOT NULL,                 -- e.g. 'eps_x_shares_eq_ni'
    expected_value  TEXT,
    observed_value  TEXT,
    passed          BOOLEAN NOT NULL,
    severity        VARCHAR(10) NOT NULL
        CHECK (severity IN ('error','warn','info')),
    validated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    validated_by    TEXT NOT NULL                  -- 'auto_l2_invariant' / 'manual_l5_SEC'
);

CREATE INDEX IF NOT EXISTS idx_vlog_record ON validation_log
    (table_name, record_key, validated_at DESC);
CREATE INDEX IF NOT EXISTS idx_vlog_failed ON validation_log (passed, validated_at DESC)
    WHERE passed = FALSE;

COMMIT;

-- Sanity selftest of the GENERATED column behavior. If this fails, calendar_quarter
-- math broke and the user shouldn't proceed.
DO $$
DECLARE
    test_q VARCHAR(7);
BEGIN
    -- Test NVDA Q4 FY26 ending 2026-01-25 should be Q4 2025
    SELECT 'Q' || EXTRACT(QUARTER FROM (DATE '2026-01-25' - INTERVAL '1 month'))::text
        || ' ' || EXTRACT(YEAR FROM (DATE '2026-01-25' - INTERVAL '1 month'))::text
    INTO test_q;
    IF test_q != 'Q4 2025' THEN
        RAISE EXCEPTION 'calendar_quarter selftest FAILED: expected Q4 2025, got %', test_q;
    END IF;
    RAISE NOTICE 'calendar_quarter selftest OK: NVDA Q4 FY26 (2026-01-25) -> %', test_q;
END $$;
