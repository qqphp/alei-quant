CREATE TABLE IF NOT EXISTS schema_migrations (
    version integer PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS jobs (
    id uuid PRIMARY KEY, kind text NOT NULL CHECK(kind IN ('collect','backtest')),
    status text NOT NULL DEFAULT 'queued'
      CHECK(status IN ('queued','running','completed','failed','cancelled','interrupted')),
    config jsonb NOT NULL, progress integer NOT NULL DEFAULT 0,
    total integer NOT NULL DEFAULT 0, checkpoint jsonb NOT NULL DEFAULT '{}',
    cancel_requested boolean NOT NULL DEFAULT false,
    error text, created_at timestamptz NOT NULL DEFAULT now(),
    started_at timestamptz, finished_at timestamptz, heartbeat_at timestamptz,
    runner_pid integer, runner_version text NOT NULL DEFAULT '0.1.0'
);
CREATE INDEX IF NOT EXISTS jobs_queue ON jobs(status, created_at);
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS peak_rss bigint NOT NULL DEFAULT 0;
INSERT INTO schema_migrations(version) VALUES(6) ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS job_logs (
    id bigserial PRIMARY KEY, job_id uuid NOT NULL REFERENCES jobs(id),
    level text NOT NULL DEFAULT 'info', message text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS job_logs_by_job ON job_logs(job_id,id);
CREATE TABLE IF NOT EXISTS data_batches (
    id bigserial PRIMARY KEY, job_id uuid REFERENCES jobs(id),
    method text NOT NULL, arguments jsonb NOT NULL,
    status text NOT NULL DEFAULT 'staging' CHECK(status IN ('staging','published','failed')),
    fields jsonb NOT NULL DEFAULT '[]', row_count integer NOT NULL DEFAULT 0,
    changed_count integer NOT NULL DEFAULT 0, coverage jsonb NOT NULL DEFAULT '{}',
    created_at timestamptz NOT NULL DEFAULT now(), published_at timestamptz
);
CREATE TABLE IF NOT EXISTS source_records (
    id bigserial PRIMARY KEY, dataset text NOT NULL, record_key text NOT NULL,
    code text, period text, payload jsonb NOT NULL, content_hash text NOT NULL,
    batch_id bigint NOT NULL REFERENCES data_batches(id),
    UNIQUE(batch_id,dataset,record_key)
);
CREATE INDEX IF NOT EXISTS records_version_key ON source_records(dataset,record_key,batch_id DESC);
CREATE INDEX IF NOT EXISTS records_series ON source_records(dataset,code,period,batch_id DESC);
CREATE TABLE IF NOT EXISTS universe_snapshots (
    batch_id bigint PRIMARY KEY REFERENCES data_batches(id),
    day date NOT NULL, codes text[] NOT NULL,
    CHECK(cardinality(codes)>0)
);
CREATE INDEX IF NOT EXISTS universe_snapshot_day ON universe_snapshots(day,batch_id DESC);
-- 仅迁移原始行全部保存在同一批次的旧快照，不能从跨批残留行猜测成员集合。
INSERT INTO universe_snapshots(batch_id,day,codes)
SELECT b.id,(b.arguments->>'day')::date,array_agg(r.code ORDER BY r.code)
FROM data_batches b JOIN source_records r ON r.batch_id=b.id AND r.dataset='universe'
WHERE b.method='query_all_stock' AND b.status='published' AND b.row_count=b.changed_count
  AND NOT EXISTS(SELECT 1 FROM schema_migrations WHERE version=7)
GROUP BY b.id HAVING count(*)=b.row_count AND count(DISTINCT r.code)=b.row_count
ON CONFLICT DO NOTHING;
INSERT INTO schema_migrations(version) VALUES(7) ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS request_usage (
    day date PRIMARY KEY, requests integer NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS strategies (
    id text NOT NULL, version text NOT NULL, digest text NOT NULL,
    manifest jsonb NOT NULL, source_path text NOT NULL, enabled boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(id,version,digest)
);
CREATE TABLE IF NOT EXISTS backtest_runs (
    id uuid PRIMARY KEY REFERENCES jobs(id), manifest jsonb NOT NULL,
    result jsonb, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS experiments (
    id uuid PRIMARY KEY, name text NOT NULL,
    frozen jsonb NOT NULL, intervals jsonb NOT NULL, candidates jsonb NOT NULL,
    selected integer, locked_at timestamptz, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS experiment_runs (
    experiment_id uuid NOT NULL REFERENCES experiments(id), candidate integer NOT NULL,
    stage text NOT NULL CHECK(stage IN ('train','validation','out_of_sample')),
    run_id uuid NOT NULL REFERENCES backtest_runs(id),
    PRIMARY KEY(experiment_id,candidate,stage)
);
INSERT INTO schema_migrations(version) VALUES(5) ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS watchlist (
    code text PRIMARY KEY, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS saved_filters (
    id uuid PRIMARY KEY, name text NOT NULL, config jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS records_latest_quote ON source_records(dataset,code,period DESC,batch_id DESC);
CREATE TABLE IF NOT EXISTS latest_quotes (
    code text PRIMARY KEY, period date NOT NULL, payload jsonb NOT NULL,
    batch_id bigint NOT NULL REFERENCES data_batches(id),
    close numeric GENERATED ALWAYS AS (CASE WHEN payload->>'close' ~ '^[+-]?[0-9]+([.][0-9]+)?$' THEN (payload->>'close')::numeric END) STORED,
    pct_chg numeric GENERATED ALWAYS AS (CASE WHEN payload->>'pctChg' ~ '^[+-]?[0-9]+([.][0-9]+)?$' THEN (payload->>'pctChg')::numeric END) STORED,
    pe_ttm numeric GENERATED ALWAYS AS (CASE WHEN payload->>'peTTM' ~ '^[+-]?[0-9]+([.][0-9]+)?$' THEN (payload->>'peTTM')::numeric END) STORED,
    pb_mrq numeric GENERATED ALWAYS AS (CASE WHEN payload->>'pbMRQ' ~ '^[+-]?[0-9]+([.][0-9]+)?$' THEN (payload->>'pbMRQ')::numeric END) STORED,
    amount numeric GENERATED ALWAYS AS (CASE WHEN payload->>'amount' ~ '^[+-]?[0-9]+([.][0-9]+)?$' THEN (payload->>'amount')::numeric END) STORED
);
INSERT INTO latest_quotes(code,period,payload,batch_id)
SELECT DISTINCT ON(r.code) r.code,r.period::date,r.payload,r.batch_id
FROM source_records r JOIN data_batches b ON b.id=r.batch_id
WHERE r.dataset='bars:d' AND b.status='published' AND NOT EXISTS(SELECT 1 FROM schema_migrations WHERE version=2)
ORDER BY r.code,r.period DESC,r.batch_id DESC
ON CONFLICT(code) DO UPDATE SET period=excluded.period,payload=excluded.payload,batch_id=excluded.batch_id;
INSERT INTO schema_migrations(version) VALUES(1) ON CONFLICT DO NOTHING;
INSERT INTO schema_migrations(version) VALUES(2) ON CONFLICT DO NOTHING;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS retry_after timestamptz;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS schedule_key text;
CREATE UNIQUE INDEX IF NOT EXISTS jobs_schedule_key ON jobs(schedule_key) WHERE schedule_key IS NOT NULL;
CREATE TABLE IF NOT EXISTS sync_settings (
    id integer PRIMARY KEY CHECK(id=1), config jsonb NOT NULL
);
INSERT INTO sync_settings(id,config) VALUES(1,'{"enabled":true,"codes":["sh.600000","sz.000001","sh.600519"],"periods":["w","m"],"minutes":[]}') ON CONFLICT DO NOTHING;
DO $$ BEGIN
  IF NOT EXISTS(SELECT 1 FROM schema_migrations WHERE version=3) THEN
    ALTER TABLE jobs DROP CONSTRAINT jobs_status_check;
    ALTER TABLE jobs ADD CONSTRAINT jobs_status_check CHECK(status IN ('queued','running','completed','failed','cancelled','interrupted','waiting'));
    INSERT INTO schema_migrations(version) VALUES(3);
  END IF;
END $$;
CREATE TABLE IF NOT EXISTS dataset_stats (
    dataset text PRIMARY KEY, revisions bigint NOT NULL, latest text
);
INSERT INTO dataset_stats(dataset,revisions,latest)
SELECT r.dataset,count(*),max(r.period) FROM source_records r JOIN data_batches b ON b.id=r.batch_id
WHERE b.status='published' AND NOT EXISTS(SELECT 1 FROM schema_migrations WHERE version=4) GROUP BY r.dataset
ON CONFLICT(dataset) DO UPDATE SET revisions=excluded.revisions,latest=excluded.latest;
INSERT INTO schema_migrations(version) VALUES(4) ON CONFLICT DO NOTHING;

-- 当前日期覆盖指针；历史回测仍按不可变 source_records 与运行版本读取。
CREATE TABLE IF NOT EXISTS daily_bar_index (
    day date NOT NULL, code text NOT NULL,
    source_id bigint NOT NULL REFERENCES source_records(id),
    batch_id bigint NOT NULL REFERENCES data_batches(id),
    PRIMARY KEY(day,code)
);
INSERT INTO daily_bar_index(day,code,source_id,batch_id)
SELECT DISTINCT ON(r.period,r.code) r.period::date,r.code,r.id,r.batch_id
FROM source_records r JOIN data_batches b ON b.id=r.batch_id
WHERE r.dataset='bars:d' AND b.status='published'
  AND NOT EXISTS(SELECT 1 FROM schema_migrations WHERE version=8)
ORDER BY r.period,r.code,r.batch_id DESC
ON CONFLICT(day,code) DO UPDATE SET source_id=excluded.source_id,batch_id=excluded.batch_id
WHERE excluded.batch_id>daily_bar_index.batch_id;
INSERT INTO schema_migrations(version) VALUES(8) ON CONFLICT DO NOTHING;

