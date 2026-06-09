-- =============================================================================
-- 003_drop_derived_make_view.sql
-- Cleanup: drop 13+2 perpetually-empty columns from earnings_quarter and
-- expose all derived metrics through a view instead.
-- Safe to run on a populated database (data is preserved).
-- =============================================================================
\set ON_ERROR_STOP on
SET client_encoding TO 'UTF8';
BEGIN;

-- ---- 1. Drop the 13 derived metric columns -------------------------------
ALTER TABLE earnings_quarter
    DROP COLUMN IF EXISTS rev_beat_miss_pct,
    DROP COLUMN IF EXISTS rev_qoq_pct,
    DROP COLUMN IF EXISTS rev_yoy_pct,
    DROP COLUMN IF EXISTS gaap_ni_beat_miss_pct,
    DROP COLUMN IF EXISTS gaap_ni_qoq_pct,
    DROP COLUMN IF EXISTS gaap_ni_yoy_pct,
    DROP COLUMN IF EXISTS gaap_eps_qoq_pct,
    DROP COLUMN IF EXISTS gaap_eps_yoy_pct,
    DROP COLUMN IF EXISTS ng_ni_qoq_pct,
    DROP COLUMN IF EXISTS ng_ni_yoy_pct,
    DROP COLUMN IF EXISTS ng_eps_beat_miss_pct,
    DROP COLUMN IF EXISTS ng_eps_qoq_pct,
    DROP COLUMN IF EXISTS ng_eps_yoy_pct;

-- ---- 2. Drop ni_consensus (always-empty; derive in view) -----------------
ALTER TABLE earnings_quarter
    DROP COLUMN IF EXISTS ni_consensus;

-- ---- 3. Drop num_analysts_revenue (always-empty; we keep num_analysts_eps) -
ALTER TABLE earnings_quarter
    DROP COLUMN IF EXISTS num_analysts_revenue;

-- ---- 4. Convert calendar_period to GENERATED -----------------------------
-- Old column was text written by ingest. We drop and re-add as generated
-- so it CANNOT drift from period_end.
ALTER TABLE earnings_quarter DROP COLUMN IF EXISTS calendar_period;

-- Use CASE WHEN (immutable) instead of TO_CHAR (locale-dependent, mutable).
-- PG refuses non-immutable expressions in STORED generated columns.
ALTER TABLE earnings_quarter ADD COLUMN calendar_period TEXT
    GENERATED ALWAYS AS (
        CASE EXTRACT(MONTH FROM (period_end - INTERVAL '2 months'))::int
            WHEN  1 THEN 'Jan' WHEN  2 THEN 'Feb' WHEN  3 THEN 'Mar'
            WHEN  4 THEN 'Apr' WHEN  5 THEN 'May' WHEN  6 THEN 'Jun'
            WHEN  7 THEN 'Jul' WHEN  8 THEN 'Aug' WHEN  9 THEN 'Sep'
            WHEN 10 THEN 'Oct' WHEN 11 THEN 'Nov' WHEN 12 THEN 'Dec'
        END
        || '-' ||
        CASE EXTRACT(MONTH FROM period_end)::int
            WHEN  1 THEN 'Jan' WHEN  2 THEN 'Feb' WHEN  3 THEN 'Mar'
            WHEN  4 THEN 'Apr' WHEN  5 THEN 'May' WHEN  6 THEN 'Jun'
            WHEN  7 THEN 'Jul' WHEN  8 THEN 'Aug' WHEN  9 THEN 'Sep'
            WHEN 10 THEN 'Oct' WHEN 11 THEN 'Nov' WHEN 12 THEN 'Dec'
        END
        || ' ' ||
        EXTRACT(YEAR FROM period_end)::text
    ) STORED;

-- ---- 5. The view that brings back all derived metrics --------------------
-- All Beat/Miss/QoQ/YoY computed on demand via window functions. Indexed
-- columns make this fast even for full-table scans.
CREATE OR REPLACE VIEW earnings_quarter_with_metrics AS
SELECT
    eq.*,

    -- NI Consensus (derived: non-GAAP EPS consensus * dil shares, in $B)
    (eq.ng_eps_consensus_current * eq.dil_shares_current / 1000.0)::NUMERIC
        AS ni_consensus_b,

    -- ---- Beat/Miss --------------------------------------------------------
    ((eq.revenue_actual - eq.revenue_consensus)
        / NULLIF(ABS(eq.revenue_consensus), 0))::NUMERIC
        AS rev_beat_miss_pct,

    -- GAAP NI vs (non-GAAP-based) NI Consensus — apples-to-oranges by design
    -- (FMP doesn't provide GAAP NI consensus; user accepted this convention)
    ((eq.gaap_ni - (eq.ng_eps_consensus_current * eq.dil_shares_current / 1000.0))
        / NULLIF(ABS(eq.ng_eps_consensus_current * eq.dil_shares_current / 1000.0), 0)
    )::NUMERIC AS gaap_ni_beat_miss_pct,

    ((eq.ng_eps_current - eq.ng_eps_consensus_current)
        / NULLIF(ABS(eq.ng_eps_consensus_current), 0))::NUMERIC
        AS ng_eps_beat_miss_pct,

    -- ---- QoQ (vs immediately prior fiscal period by symbol) --------------
    (eq.revenue_actual / NULLIF(LAG(eq.revenue_actual) OVER w, 0) - 1)::NUMERIC
        AS rev_qoq_pct,
    (eq.gaap_ni / NULLIF(LAG(eq.gaap_ni) OVER w, 0) - 1)::NUMERIC
        AS gaap_ni_qoq_pct,
    (eq.gaap_eps_diluted_current
        / NULLIF(LAG(eq.gaap_eps_diluted_current) OVER w, 0) - 1)::NUMERIC
        AS gaap_eps_qoq_pct,
    (eq.ng_ni / NULLIF(LAG(eq.ng_ni) OVER w, 0) - 1)::NUMERIC
        AS ng_ni_qoq_pct,
    (eq.ng_eps_current / NULLIF(LAG(eq.ng_eps_current) OVER w, 0) - 1)::NUMERIC
        AS ng_eps_qoq_pct,

    -- ---- YoY (vs 4 fiscal periods earlier by symbol) ---------------------
    (eq.revenue_actual / NULLIF(LAG(eq.revenue_actual, 4) OVER w, 0) - 1)::NUMERIC
        AS rev_yoy_pct,
    (eq.gaap_ni / NULLIF(LAG(eq.gaap_ni, 4) OVER w, 0) - 1)::NUMERIC
        AS gaap_ni_yoy_pct,
    (eq.gaap_eps_diluted_current
        / NULLIF(LAG(eq.gaap_eps_diluted_current, 4) OVER w, 0) - 1)::NUMERIC
        AS gaap_eps_yoy_pct,
    (eq.ng_ni / NULLIF(LAG(eq.ng_ni, 4) OVER w, 0) - 1)::NUMERIC
        AS ng_ni_yoy_pct,
    (eq.ng_eps_current / NULLIF(LAG(eq.ng_eps_current, 4) OVER w, 0) - 1)::NUMERIC
        AS ng_eps_yoy_pct

FROM earnings_quarter eq
WINDOW w AS (PARTITION BY eq.symbol ORDER BY eq.fiscal_year, eq.fiscal_period);

COMMENT ON VIEW earnings_quarter_with_metrics IS
'All Beat/Miss/QoQ/YoY derived metrics on top of earnings_quarter. '
'QoQ = vs immediately prior fiscal period (assumes contiguous data). '
'YoY = vs 4 fiscal periods earlier. '
'Beat/Miss uses abs() in denominator (sign-safe for negative EPS).';

COMMIT;

-- ---- Verification (run inside transaction-less area; just SELECT) --------
-- Show: column count went down; calendar_period still works; view has data.
DO $$
DECLARE
    n_cols INT;
    n_rows INT;
    sample_calendar TEXT;
BEGIN
    SELECT count(*) INTO n_cols
    FROM information_schema.columns
    WHERE table_name = 'earnings_quarter' AND table_schema = 'public';

    SELECT count(*) INTO n_rows FROM earnings_quarter;

    SELECT calendar_period INTO sample_calendar
    FROM earnings_quarter
    ORDER BY period_end DESC LIMIT 1;

    RAISE NOTICE 'earnings_quarter now has % columns (was ~38).', n_cols;
    RAISE NOTICE 'earnings_quarter has % rows (data preserved).', n_rows;
    IF sample_calendar IS NOT NULL THEN
        RAISE NOTICE 'calendar_period sample (latest row): %', sample_calendar;
    END IF;
END $$;
