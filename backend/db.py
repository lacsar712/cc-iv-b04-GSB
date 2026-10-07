import os
import psycopg
from psycopg.rows import dict_row

DSN = os.environ.get("DATABASE_URL", "postgresql://app:app@localhost:54402/pvivscan")


def connect():
    return psycopg.connect(DSN, row_factory=dict_row)


SCHEMA = """
CREATE TABLE IF NOT EXISTS iv_scans (
    id bigserial PRIMARY KEY,
    box_code text NOT NULL DEFAULT '',
    string_code text NOT NULL,
    voc_v double precision NOT NULL,
    isc_a double precision NOT NULL,
    fill_factor double precision NOT NULL,
    status text NOT NULL DEFAULT 'pending',
    verdict text,
    reason text,
    created_by text NOT NULL,
    created_at timestamptz NOT NULL,
    processed_at timestamptz
);
ALTER TABLE iv_scans ADD COLUMN IF NOT EXISTS box_code text NOT NULL DEFAULT '';
CREATE INDEX IF NOT EXISTS idx_iv_scans_box_window
    ON iv_scans (box_code, created_at);

-- 同箱最弱串：一次重算的口径头（截止钟点与结果在同一事务写入）
CREATE TABLE IF NOT EXISTS box_weakest_runs (
    id bigserial PRIMARY KEY,
    box_code text NOT NULL,
    window_start timestamptz NOT NULL,
    window_end timestamptz NOT NULL,
    state text NOT NULL,
    baseline_voc double precision,
    weakest_string text,
    weakest_voc double precision,
    recomputed_by text NOT NULL,
    recomputed_at timestamptz NOT NULL,
    UNIQUE (box_code, window_start, window_end)
);

-- 同箱最弱串：钟段内各串“最近一条”读数
CREATE TABLE IF NOT EXISTS box_weakest_rows (
    id bigserial PRIMARY KEY,
    run_id bigint NOT NULL REFERENCES box_weakest_runs(id) ON DELETE CASCADE,
    string_code text NOT NULL,
    scan_id bigint,
    voc_v double precision NOT NULL,
    status text NOT NULL,
    is_latest_pending boolean NOT NULL DEFAULT false,
    delta_v double precision,
    is_weakest boolean NOT NULL DEFAULT false
);
CREATE INDEX IF NOT EXISTS idx_box_weakest_rows_run ON box_weakest_rows (run_id);

CREATE OR REPLACE FUNCTION notify_iv_scan() RETURNS trigger AS $$
BEGIN
  PERFORM pg_notify('iv_scan_new', NEW.id::text);
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS trg_iv_scan_notify ON iv_scans;
CREATE TRIGGER trg_iv_scan_notify
AFTER INSERT ON iv_scans
FOR EACH ROW EXECUTE FUNCTION notify_iv_scan();
"""
