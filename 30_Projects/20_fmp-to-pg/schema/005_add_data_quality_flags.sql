-- 005_add_data_quality_flags.sql
-- ---------------------------------------------------------------------------
-- Per-row data-quality markers. When ingesting the broad Stocks.base universe
-- (not just curated MAG7), a long tail of tickers has genuine FMP data
-- anomalies — e.g. diluted-share vs EPS-basis mismatches for SPAC / miner /
-- recent-IPO names, or tiny negative revenue. Rather than abort the whole
-- batch, the ingest WRITES those rows and records which validation checks they
-- tripped here, so they stay queryable and filterable.
--
-- Empty array = clean row. Populated by fetch_fmp_to_pg.py from the L2-L4
-- checks at 'warn'/'error' severity (near-zero EPS noise is 'info' → unflagged).
--
-- Find suspect rows:
--   SELECT symbol, quarter_label, data_quality_flags
--   FROM earnings_quarter
--   WHERE data_quality_flags <> '{}'
--   ORDER BY symbol;
-- Exclude them from a clean view:
--   ... WHERE data_quality_flags = '{}';

ALTER TABLE earnings_quarter
    ADD COLUMN IF NOT EXISTS data_quality_flags TEXT[] NOT NULL DEFAULT '{}';

COMMENT ON COLUMN earnings_quarter.data_quality_flags IS
    'Validation checks this row failed at warn/error severity (e.g. '
    '{l2_ni_eq_eps_x_shares}). Empty = clean. See schema/005.';
