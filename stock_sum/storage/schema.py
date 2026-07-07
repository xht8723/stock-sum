"""SQLite schema definition."""

from __future__ import annotations

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS collection_runs (
    run_id TEXT PRIMARY KEY,
    collector_id TEXT NOT NULL,
    source_type TEXT,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    collected_count INTEGER NOT NULL DEFAULT 0,
    inserted_count INTEGER NOT NULL DEFAULT 0,
    updated_count INTEGER NOT NULL DEFAULT 0,
    error_text TEXT
);

CREATE TABLE IF NOT EXISTS raw_item_index (
    source_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    canonical_url TEXT,
    collected_at TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    latest_seen_at TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    PRIMARY KEY (source_type, source_id)
);

CREATE TABLE IF NOT EXISTS raw_x_posts (
    status_id TEXT NOT NULL,
    handle TEXT NOT NULL,
    author_handle TEXT,
    author_name TEXT,
    posted_at_text TEXT,
    url TEXT,
    text TEXT NOT NULL,
    reply_count INTEGER,
    repost_count INTEGER,
    like_count INTEGER,
    quote_count INTEGER,
    view_count INTEGER,
    raw_json TEXT NOT NULL,
    collected_at TEXT NOT NULL,
    posted_at_utc TEXT,
    PRIMARY KEY (handle, status_id)
);

CREATE TABLE IF NOT EXISTS raw_x_post_media (
    status_id TEXT NOT NULL,
    media_key TEXT,
    media_type TEXT,
    media_url TEXT NOT NULL,
    alt_text TEXT,
    raw_json TEXT NOT NULL,
    PRIMARY KEY (status_id, media_url)
);

CREATE TABLE IF NOT EXISTS raw_reddit_posts (
    post_id TEXT NOT NULL,
    subreddit TEXT NOT NULL,
    fullname TEXT,
    title TEXT NOT NULL,
    author TEXT,
    url TEXT,
    permalink TEXT,
    selftext TEXT NOT NULL,
    score INTEGER,
    ups INTEGER,
    upvote_ratio REAL,
    num_comments INTEGER,
    thumbnail_url TEXT,
    created_at_text TEXT,
    raw_json TEXT NOT NULL,
    collected_at TEXT NOT NULL,
    created_at_utc TEXT,
    PRIMARY KEY (subreddit, post_id)
);

CREATE TABLE IF NOT EXISTS raw_reddit_comments (
    comment_id TEXT NOT NULL,
    post_id TEXT NOT NULL,
    parent_id TEXT,
    author TEXT,
    body TEXT NOT NULL,
    score INTEGER,
    ups INTEGER,
    url TEXT,
    created_at_text TEXT,
    depth INTEGER,
    raw_json TEXT NOT NULL,
    collected_at TEXT NOT NULL,
    created_at_utc TEXT,
    PRIMARY KEY (post_id, comment_id)
);

CREATE TABLE IF NOT EXISTS raw_reddit_post_media (
    post_id TEXT NOT NULL,
    media_type TEXT,
    media_url TEXT NOT NULL,
    source_field TEXT,
    raw_json TEXT NOT NULL,
    PRIMARY KEY (post_id, media_url)
);

CREATE TABLE IF NOT EXISTS downloaded_media (
    remote_url_hash TEXT PRIMARY KEY,
    remote_url TEXT NOT NULL UNIQUE,
    local_path TEXT NOT NULL,
    content_type TEXT NOT NULL,
    byte_size INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    downloaded_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS raw_provider_api_responses (
    response_id TEXT PRIMARY KEY,
    collection_run_id TEXT NOT NULL,
    collector_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    request_arguments_json TEXT NOT NULL,
    raw_response_text TEXT NOT NULL,
    parsed_rows_json TEXT NOT NULL,
    row_count INTEGER NOT NULL,
    fetched_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_provider_api_responses_run
ON raw_provider_api_responses (collection_run_id);

CREATE TABLE IF NOT EXISTS raw_adanos_trending_responses (
    response_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    platform TEXT NOT NULL,
    category TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    request_args_json TEXT NOT NULL,
    raw_response_text TEXT NOT NULL,
    error_text TEXT,
    status TEXT NOT NULL,
    row_count INTEGER NOT NULL,
    fetched_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_adanos_trending_responses_job
ON raw_adanos_trending_responses (job_id, platform, category);

CREATE TABLE IF NOT EXISTS raw_adanos_trending_stocks (
    job_id TEXT NOT NULL,
    platform TEXT NOT NULL,
    rank INTEGER NOT NULL,
    window_from TEXT NOT NULL,
    window_to TEXT NOT NULL,
    ticker TEXT NOT NULL,
    company_name TEXT,
    trend TEXT,
    mentions INTEGER,
    bullish_pct INTEGER,
    bearish_pct INTEGER,
    sentiment_score REAL,
    buzz_score REAL,
    trend_history_json TEXT NOT NULL,
    raw_json TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (job_id, platform, rank, ticker)
);

CREATE INDEX IF NOT EXISTS idx_adanos_trending_stocks_ticker
ON raw_adanos_trending_stocks (ticker, fetched_at);

CREATE TABLE IF NOT EXISTS raw_adanos_trending_sectors (
    job_id TEXT NOT NULL,
    platform TEXT NOT NULL,
    rank INTEGER NOT NULL,
    window_from TEXT NOT NULL,
    window_to TEXT NOT NULL,
    sector TEXT NOT NULL,
    top_tickers_json TEXT NOT NULL,
    trend TEXT,
    mentions INTEGER,
    bullish_pct INTEGER,
    bearish_pct INTEGER,
    sentiment_score REAL,
    buzz_score REAL,
    trend_history_json TEXT NOT NULL,
    raw_json TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (job_id, platform, rank, sector)
);

CREATE INDEX IF NOT EXISTS idx_adanos_trending_sectors_sector
ON raw_adanos_trending_sectors (sector, fetched_at);

CREATE TABLE IF NOT EXISTS raw_house_ptr_filings (
    doc_id TEXT PRIMARY KEY,
    year INTEGER NOT NULL,
    name TEXT,
    prefix TEXT,
    first_name TEXT,
    last_name TEXT,
    suffix TEXT,
    display_name TEXT,
    name_normalized TEXT,
    status TEXT,
    state TEXT,
    filing_date TEXT,
    filing_date_utc TEXT,
    pdf_url TEXT,
    raw_xml_json TEXT NOT NULL,
    tables_json TEXT NOT NULL,
    extraction_status TEXT NOT NULL,
    extraction_error TEXT,
    collected_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS raw_house_ptr_trade_rows (
    doc_id TEXT NOT NULL,
    table_index INTEGER NOT NULL,
    row_index INTEGER NOT NULL,
    asset TEXT,
    asset_type_code TEXT,
    asset_type_label TEXT,
    stock_ticker TEXT,
    transaction_type TEXT,
    transaction_date TEXT,
    transaction_date_utc TEXT,
    transaction_action TEXT,
    amount TEXT,
    raw_cells_json TEXT NOT NULL,
    raw_json TEXT NOT NULL,
    PRIMARY KEY (doc_id, table_index, row_index)
);

CREATE INDEX IF NOT EXISTS idx_house_ptr_filings_recent
ON raw_house_ptr_filings (filing_date DESC, collected_at DESC);

CREATE INDEX IF NOT EXISTS idx_house_ptr_filings_name
ON raw_house_ptr_filings (name_normalized);

CREATE INDEX IF NOT EXISTS idx_house_ptr_trade_rows_transaction_date
ON raw_house_ptr_trade_rows (transaction_date_utc);

CREATE TABLE IF NOT EXISTS raw_sec_13f_datasets (
    dataset_id TEXT PRIMARY KEY,
    label TEXT,
    download_url TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    byte_size INTEGER,
    row_counts_json TEXT NOT NULL,
    downloaded_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS raw_sec_13f_submissions (
    dataset_id TEXT NOT NULL,
    accession_number TEXT NOT NULL,
    filing_date TEXT,
    filing_date_utc TEXT,
    submission_type TEXT,
    cik TEXT,
    period_of_report TEXT,
    period_of_report_utc TEXT,
    raw_json TEXT NOT NULL,
    PRIMARY KEY (dataset_id, accession_number)
);

CREATE TABLE IF NOT EXISTS raw_sec_13f_coverpages (
    dataset_id TEXT NOT NULL,
    accession_number TEXT NOT NULL,
    manager_name TEXT,
    manager_name_normalized TEXT,
    report_type TEXT,
    form_13f_file_number TEXT,
    raw_json TEXT NOT NULL,
    PRIMARY KEY (dataset_id, accession_number)
);

CREATE TABLE IF NOT EXISTS raw_sec_13f_other_managers (
    dataset_id TEXT NOT NULL,
    accession_number TEXT NOT NULL,
    row_key TEXT NOT NULL,
    raw_json TEXT NOT NULL,
    PRIMARY KEY (dataset_id, accession_number, row_key)
);

CREATE TABLE IF NOT EXISTS raw_sec_13f_signatures (
    dataset_id TEXT NOT NULL,
    accession_number TEXT NOT NULL,
    raw_json TEXT NOT NULL,
    PRIMARY KEY (dataset_id, accession_number)
);

CREATE TABLE IF NOT EXISTS raw_sec_13f_summary_pages (
    dataset_id TEXT NOT NULL,
    accession_number TEXT NOT NULL,
    raw_json TEXT NOT NULL,
    PRIMARY KEY (dataset_id, accession_number)
);

CREATE TABLE IF NOT EXISTS raw_sec_13f_other_managers2 (
    dataset_id TEXT NOT NULL,
    accession_number TEXT NOT NULL,
    row_key TEXT NOT NULL,
    raw_json TEXT NOT NULL,
    PRIMARY KEY (dataset_id, accession_number, row_key)
);

CREATE TABLE IF NOT EXISTS raw_sec_13f_info_tables (
    dataset_id TEXT NOT NULL,
    accession_number TEXT NOT NULL,
    info_table_sk TEXT NOT NULL,
    issuer TEXT,
    issuer_normalized TEXT,
    title_of_class TEXT,
    cusip TEXT,
    figi TEXT,
    value INTEGER,
    ssh_prn_amt INTEGER,
    ssh_prn_type TEXT,
    put_call TEXT,
    investment_discretion TEXT,
    other_manager TEXT,
    voting_auth_sole INTEGER,
    voting_auth_shared INTEGER,
    voting_auth_none INTEGER,
    raw_json TEXT NOT NULL,
    PRIMARY KEY (dataset_id, accession_number, info_table_sk)
);

CREATE INDEX IF NOT EXISTS idx_sec_13f_submissions_period
ON raw_sec_13f_submissions (period_of_report_utc, filing_date_utc);

CREATE INDEX IF NOT EXISTS idx_sec_13f_submissions_cik
ON raw_sec_13f_submissions (cik);

CREATE INDEX IF NOT EXISTS idx_sec_13f_coverpages_manager
ON raw_sec_13f_coverpages (manager_name_normalized);

CREATE INDEX IF NOT EXISTS idx_sec_13f_info_issuer
ON raw_sec_13f_info_tables (issuer_normalized);

CREATE INDEX IF NOT EXISTS idx_sec_13f_info_cusip
ON raw_sec_13f_info_tables (cusip);

CREATE INDEX IF NOT EXISTS idx_sec_13f_info_figi
ON raw_sec_13f_info_tables (figi);

CREATE INDEX IF NOT EXISTS idx_sec_13f_info_value
ON raw_sec_13f_info_tables (value);

CREATE TABLE IF NOT EXISTS llm_analysis_runs (
    analysis_run_id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    chunk_count INTEGER NOT NULL DEFAULT 0,
    succeeded_count INTEGER NOT NULL DEFAULT 0,
    failed_count INTEGER NOT NULL DEFAULT 0,
    instructions TEXT,
    error_text TEXT
);

CREATE TABLE IF NOT EXISTS llm_x_post_analyses (
    analysis_run_id TEXT NOT NULL,
    handle TEXT NOT NULL,
    status_id TEXT NOT NULL,
    source_ref TEXT NOT NULL,
    url TEXT,
    posted_at_text TEXT,
    sentiment TEXT NOT NULL,
    tags_json TEXT NOT NULL,
    tickers_json TEXT NOT NULL DEFAULT '[]',
    summary TEXT NOT NULL,
    interpretation TEXT NOT NULL,
    importance TEXT NOT NULL DEFAULT 'medium',
    confidence TEXT NOT NULL,
    raw_response_json TEXT NOT NULL,
    analyzed_at TEXT NOT NULL,
    PRIMARY KEY (analysis_run_id, status_id)
);

CREATE TABLE IF NOT EXISTS llm_reddit_post_analyses (
    analysis_run_id TEXT NOT NULL,
    subreddit TEXT NOT NULL,
    post_id TEXT NOT NULL,
    source_ref TEXT NOT NULL,
    title TEXT NOT NULL,
    url TEXT,
    created_at_text TEXT,
    sentiment TEXT NOT NULL,
    tags_json TEXT NOT NULL,
    tickers_json TEXT NOT NULL DEFAULT '[]',
    summary TEXT NOT NULL,
    interpretation TEXT NOT NULL,
    importance TEXT NOT NULL DEFAULT 'medium',
    confidence TEXT NOT NULL,
    comment_sentiment_counts_json TEXT NOT NULL,
    raw_response_json TEXT NOT NULL,
    analyzed_at TEXT NOT NULL,
    PRIMARY KEY (analysis_run_id, post_id)
);

CREATE TABLE IF NOT EXISTS llm_x_post_tickers (
    analysis_run_id TEXT NOT NULL,
    handle TEXT NOT NULL,
    status_id TEXT NOT NULL,
    ticker TEXT NOT NULL,
    source_ref TEXT NOT NULL,
    analyzed_at TEXT NOT NULL,
    PRIMARY KEY (analysis_run_id, status_id, ticker)
);

CREATE TABLE IF NOT EXISTS llm_reddit_post_tickers (
    analysis_run_id TEXT NOT NULL,
    subreddit TEXT NOT NULL,
    post_id TEXT NOT NULL,
    ticker TEXT NOT NULL,
    source_ref TEXT NOT NULL,
    analyzed_at TEXT NOT NULL,
    PRIMARY KEY (analysis_run_id, post_id, ticker)
);

CREATE TABLE IF NOT EXISTS llm_reddit_comment_analyses (
    analysis_run_id TEXT NOT NULL,
    subreddit TEXT NOT NULL,
    post_id TEXT NOT NULL,
    comment_id TEXT NOT NULL,
    source_ref TEXT NOT NULL,
    parent_id TEXT,
    sentiment TEXT NOT NULL,
    summary TEXT NOT NULL,
    confidence TEXT NOT NULL,
    raw_response_json TEXT NOT NULL,
    analyzed_at TEXT NOT NULL,
    PRIMARY KEY (analysis_run_id, post_id, comment_id)
);

CREATE INDEX IF NOT EXISTS idx_llm_analysis_runs_finished
ON llm_analysis_runs (finished_at);

CREATE INDEX IF NOT EXISTS idx_llm_x_post_analyses_handle
ON llm_x_post_analyses (handle);

CREATE INDEX IF NOT EXISTS idx_llm_reddit_post_analyses_subreddit
ON llm_reddit_post_analyses (subreddit);

CREATE INDEX IF NOT EXISTS idx_llm_x_post_tickers_ticker
ON llm_x_post_tickers (ticker);

CREATE INDEX IF NOT EXISTS idx_llm_reddit_post_tickers_ticker
ON llm_reddit_post_tickers (ticker);
"""
