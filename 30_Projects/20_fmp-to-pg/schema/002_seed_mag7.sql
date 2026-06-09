-- =============================================================================
-- 002_seed_mag7.sql — seed company + corporate_action for MAG7
-- Non-GAAP basis matrix follows CLAUDE.md §💰 rule 3 verbatim.
-- Corporate actions are HISTORICAL FACTS verified from SEC / company IR.
-- =============================================================================

\set ON_ERROR_STOP on
SET client_encoding TO 'UTF8';
BEGIN;

INSERT INTO company (symbol, name, sector, industry, themes,
                     fiscal_year_end_month, cik,
                     reports_official_non_gaap, default_non_gaap_basis,
                     ng_confidence, non_gaap_basis_source)
VALUES
('AAPL',  'Apple Inc.',           'Technology',
   'Consumer Electronics',
   ARRAY['AI Edge','Services','Vision Pro/AR','iPhone','Wearables'],
   9, '0000320193',
   'No', 'Apple reports GAAP only; non-GAAP = GAAP', 'High', 'SEC 10-Q'),

('MSFT',  'Microsoft Corporation','Technology',
   'Software—Infrastructure',
   ARRAY['AI Compute','Cloud Infra','Productivity','OpenAI Partnership'],
   6, '0000789019',
   'Partial (from Q1 FY2026)',
   'Excludes OpenAI equity-method impact from Q1 FY26; =GAAP before that',
   'High', 'microsoft.com/investor + SEC 8-K'),

('NVDA',  'NVIDIA Corporation',   'Technology',
   'Semiconductors',
   ARRAY['AI Compute','GPU Architecture','Data Center','Robotics Sim'],
   1, '0001045810',
   'Yes (def changed Q1 FY27)',
   'Pre-FY27: ex-SBC; Q1 FY27 onward: SBC no longer excluded',
   'Med', 'SEC 10-Q + 8-K reconciliation'),

('AMZN',  'Amazon.com, Inc.',     'Consumer Cyclical',
   'Internet Retail',
   ARRAY['Cloud Infra','E-commerce','AI Compute','Advertising','Logistics'],
   12, '0001018724',
   'No', 'Amazon reports GAAP only; non-GAAP = GAAP', 'High', 'SEC 10-Q'),

('GOOGL', 'Alphabet Inc.',        'Communication Services',
   'Internet Content & Information',
   ARRAY['AI Compute','Search Advertising','Cloud Infra','Waymo Robotaxi','Android'],
   12, '0001652044',
   'No', 'Alphabet reports GAAP only; non-GAAP = GAAP', 'High', 'SEC 10-Q'),

('META',  'Meta Platforms, Inc.', 'Communication Services',
   'Internet Content & Information',
   ARRAY['AI Compute','Advertising','AR/VR','Llama Foundation Model','Social'],
   12, '0001326801',
   'NO',
   'META does NOT report official non-GAAP NI/EPS; any seen elsewhere is analyst-adjusted',
   'Low', 'SEC 8-K ex-99.1'),

('TSLA',  'Tesla, Inc.',          'Consumer Cyclical',
   'Auto Manufacturers',
   ARRAY['Robotaxi','Optimus Humanoid','EV','Battery Storage','FSD'],
   12, '0001318605',
   'Yes',
   'Standard non-GAAP (primarily ex-SBC); diff vs GAAP usually <5%',
   'High', 'Tesla quarterly update + SEC 10-Q')
ON CONFLICT (symbol) DO UPDATE SET
    name = EXCLUDED.name,
    sector = EXCLUDED.sector,
    industry = EXCLUDED.industry,
    themes = EXCLUDED.themes,
    fiscal_year_end_month = EXCLUDED.fiscal_year_end_month,
    cik = EXCLUDED.cik,
    reports_official_non_gaap = EXCLUDED.reports_official_non_gaap,
    default_non_gaap_basis = EXCLUDED.default_non_gaap_basis,
    ng_confidence = EXCLUDED.ng_confidence,
    non_gaap_basis_source = EXCLUDED.non_gaap_basis_source,
    updated_at = NOW();


-- Known corporate actions (verified facts) ----------------------------------
INSERT INTO corporate_action (symbol, action_type, ex_date, ratio, description, source_url)
VALUES
-- AAPL splits
('AAPL', 'split', '2014-06-09', 7,
   'Apple 7-for-1 stock split',
   'https://www.apple.com/newsroom/2014/04/23Apple-Announces-Record-Quarterly-Earnings/'),
('AAPL', 'split', '2020-08-31', 4,
   'Apple 4-for-1 stock split',
   'https://www.apple.com/newsroom/2020/07/apple-reports-third-quarter-results/'),

-- TSLA splits
('TSLA', 'split', '2020-08-31', 5,
   'Tesla 5-for-1 stock split',
   'https://www.sec.gov/Archives/edgar/data/1318605/000119312520213595/'),
('TSLA', 'split', '2022-08-25', 3,
   'Tesla 3-for-1 stock split',
   'https://ir.tesla.com/_flysystem/s3/sec/000110465922095765/tm2222886d1_8k.pdf'),

-- NVDA splits
('NVDA', 'split', '2021-07-20', 4,
   'NVIDIA 4-for-1 stock split',
   'https://nvidianews.nvidia.com/news/nvidia-board-approves-four-for-one-stock-split'),
('NVDA', 'split', '2024-06-10', 10,
   'NVIDIA 10-for-1 stock split',
   'https://nvidianews.nvidia.com/news/nvidia-announces-financial-results-for-first-quarter-fiscal-2025'),

-- GOOGL split
('GOOGL', 'split', '2022-07-15', 20,
   'Alphabet 20-for-1 stock split',
   'https://abc.xyz/2022-q1-earnings-release.pdf'),

-- AMZN split
('AMZN', 'split', '2022-06-06', 20,
   'Amazon 20-for-1 stock split',
   'https://ir.aboutamazon.com/news-release/news-release-details/2022/Amazoncom-Announces-Financial-Results-First-Quarter-2022/'),

-- Non-GAAP definition changes
('NVDA', 'non_gaap_def_change', '2026-04-27', NULL,
   'NVIDIA non-GAAP no longer excludes stock-based compensation (effective Q1 FY27)',
   'https://nvidianews.nvidia.com/news/nvidia-announces-financial-results-for-first-quarter-fiscal-2027'),
('MSFT', 'non_gaap_def_change', '2025-07-01', NULL,
   'Microsoft begins reporting non-GAAP adjusted for OpenAI equity-method impact (Q1 FY26)',
   'https://www.microsoft.com/en-us/Investor/earnings/FY-2026-Q1/press-release-webcast')
ON CONFLICT (symbol, action_type, ex_date) DO UPDATE SET
    ratio = EXCLUDED.ratio,
    description = EXCLUDED.description,
    source_url = EXCLUDED.source_url;

COMMIT;
