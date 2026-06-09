-- =============================================================================
-- 004_add_forward_consensus.sql
-- Capture forward-looking consensus (next-quarter NI estimate + analyst
-- counts), and a flag so future quarters can be filtered cleanly.
-- =============================================================================
\set ON_ERROR_STOP on
SET client_encoding TO 'UTF8';
BEGIN;

-- NI estimate directly from /analyst/financial-estimates (more reliable than
-- deriving as eps * shares when shares are unknown for future quarters).
-- Stored in $B for consistency with other money columns.
ALTER TABLE earnings_quarter
    ADD COLUMN IF NOT EXISTS ni_consensus_estimate NUMERIC,
    ADD COLUMN IF NOT EXISTS revenue_consensus_low  NUMERIC,
    ADD COLUMN IF NOT EXISTS revenue_consensus_high NUMERIC,
    ADD COLUMN IF NOT EXISTS ng_eps_consensus_low   NUMERIC,
    ADD COLUMN IF NOT EXISTS ng_eps_consensus_high  NUMERIC,
    ADD COLUMN IF NOT EXISTS num_analysts_revenue   INT
        CHECK (num_analysts_revenue IS NULL OR num_analysts_revenue >= 0);

COMMENT ON COLUMN earnings_quarter.ni_consensus_estimate IS
'Direct NI consensus from /analyst/financial-estimates (netIncomeAvg), $B. '
'Use this for future quarters where dil_shares is unknown.';

-- The view selects eq.*, and ADD COLUMN above changed the column order in
-- eq.* expansion. CREATE OR REPLACE VIEW rejects column-name changes at
-- existing positions, so we DROP and recreate.
DROP VIEW IF EXISTS earnings_quarter_with_metrics;
CREATE VIEW earnings_quarter_with_metrics AS
SELECT
    eq.*,

    -- NI Consensus: prefer direct estimate; fall back to derived
    COALESCE(
        eq.ni_consensus_estimate,
        eq.ng_eps_consensus_current * eq.dil_shares_current / 1000.0
    )::NUMERIC AS ni_consensus_b,

    -- Beat/Miss
    ((eq.revenue_actual - eq.revenue_consensus)
        / NULLIF(ABS(eq.revenue_consensus), 0))::NUMERIC AS rev_beat_miss_pct,
    ((eq.gaap_ni - COALESCE(
            eq.ni_consensus_estimate,
            eq.ng_eps_consensus_current * eq.dil_shares_current / 1000.0))
        / NULLIF(ABS(COALESCE(
            eq.ni_consensus_estimate,
            eq.ng_eps_consensus_current * eq.dil_shares_current / 1000.0)), 0)
    )::NUMERIC AS gaap_ni_beat_miss_pct,
    ((eq.ng_eps_current - eq.ng_eps_consensus_current)
        / NULLIF(ABS(eq.ng_eps_consensus_current), 0))::NUMERIC AS ng_eps_beat_miss_pct,

    -- QoQ
    (eq.revenue_actual / NULLIF(LAG(eq.revenue_actual) OVER w, 0) - 1)::NUMERIC AS rev_qoq_pct,
    (eq.gaap_ni / NULLIF(LAG(eq.gaap_ni) OVER w, 0) - 1)::NUMERIC AS gaap_ni_qoq_pct,
    (eq.gaap_eps_diluted_current
        / NULLIF(LAG(eq.gaap_eps_diluted_current) OVER w, 0) - 1)::NUMERIC AS gaap_eps_qoq_pct,
    (eq.ng_ni / NULLIF(LAG(eq.ng_ni) OVER w, 0) - 1)::NUMERIC AS ng_ni_qoq_pct,
    (eq.ng_eps_current / NULLIF(LAG(eq.ng_eps_current) OVER w, 0) - 1)::NUMERIC AS ng_eps_qoq_pct,

    -- YoY
    (eq.revenue_actual / NULLIF(LAG(eq.revenue_actual, 4) OVER w, 0) - 1)::NUMERIC AS rev_yoy_pct,
    (eq.gaap_ni / NULLIF(LAG(eq.gaap_ni, 4) OVER w, 0) - 1)::NUMERIC AS gaap_ni_yoy_pct,
    (eq.gaap_eps_diluted_current
        / NULLIF(LAG(eq.gaap_eps_diluted_current, 4) OVER w, 0) - 1)::NUMERIC AS gaap_eps_yoy_pct,
    (eq.ng_ni / NULLIF(LAG(eq.ng_ni, 4) OVER w, 0) - 1)::NUMERIC AS ng_ni_yoy_pct,
    (eq.ng_eps_current / NULLIF(LAG(eq.ng_eps_current, 4) OVER w, 0) - 1)::NUMERIC AS ng_eps_yoy_pct,

    -- Convenience flag: is this a future (un-released) quarter?
    (eq.revenue_actual IS NULL AND eq.gaap_ni IS NULL)::BOOLEAN AS is_upcoming

FROM earnings_quarter eq
WINDOW w AS (PARTITION BY eq.symbol ORDER BY eq.fiscal_year, eq.fiscal_period);

COMMIT;
