from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
import json
from pathlib import Path
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .config import settings


def json_default(value):
    if isinstance(value, (date, datetime, UUID, Path)):
        return str(value)
    if isinstance(value, Decimal):
        return float(value)
    raise TypeError(type(value).__name__)


def dumps(value):
    return json.dumps(value, ensure_ascii=False, default=json_default, allow_nan=False)


def jb(value):
    return Jsonb(value, dumps=dumps)


@contextmanager
def connect():
    cfg = settings()
    with psycopg.connect(host=cfg.db_host, port=cfg.db_port, dbname=cfg.db_name,
                         user=cfg.db_user, password=cfg.db_password.get_secret_value(),
                         connect_timeout=5, row_factory=dict_row) as conn:
        yield conn


def rows(query, params=()):
    with connect() as conn:
        return conn.execute(query, params).fetchall()


def one(query, params=()):
    with connect() as conn:
        return conn.execute(query, params).fetchone()


def execute(query, params=()):
    with connect() as conn:
        return conn.execute(query, params).rowcount


def migrate():
    with connect() as conn:
        conn.execute(Path(__file__).with_name("schema.sql").read_text(encoding="utf-8"))


def latest_version():
    return one("SELECT COALESCE(max(id),0) AS version FROM data_batches WHERE status='published'")["version"]


def records(dataset, *, code=None, start=None, end=None, version=None):
    filters, args = ["r.dataset=%s", "b.status='published'", "r.batch_id<=%s"], [dataset, latest_version() if version is None else version]
    for column, operator, value in (("code", "=", code), ("period", ">=", start), ("period", "<=", end)):
        if value is not None:
            filters.append(f"r.{column}{operator}%s")
            args.append(value)
    return rows("SELECT * FROM (SELECT DISTINCT ON (r.record_key) r.* FROM source_records r "
                "JOIN data_batches b ON b.id=r.batch_id WHERE " + " AND ".join(filters) +
                " ORDER BY r.record_key,r.batch_id DESC) q ORDER BY period,code,record_key", args)


def payloads(dataset, **kwargs):
    return [r["payload"] for r in records(dataset, **kwargs)]


def payload_page(dataset, *, code=None, version=None, page=1, size=100):
    filters = ["r.dataset=%s", "b.status='published'", "r.batch_id<=%s"]
    args = [dataset, latest_version() if version is None else version]
    if code:
        filters.append("r.code=%s")
        args.append(code)
    where = " AND ".join(filters)
    latest = ("SELECT DISTINCT ON(r.record_key) r.record_key,r.period,r.code,r.payload "
              "FROM source_records r JOIN data_batches b ON b.id=r.batch_id WHERE " + where +
              " ORDER BY r.record_key,r.batch_id DESC")
    with connect() as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        total = conn.execute("SELECT count(DISTINCT r.record_key) AS total FROM source_records r "
                             "JOIN data_batches b ON b.id=r.batch_id WHERE " + where, args).fetchone()["total"]
        data = conn.execute("SELECT payload FROM (" + latest +
                            ") q ORDER BY period,code,record_key LIMIT %s OFFSET %s",
                            args + [size, (page-1)*size]).fetchall()
    return {"total": total, "data": [row["payload"] for row in data]}


def stream_payloads(dataset, *, code=None, version=None):
    filters, args = ["r.dataset=%s", "b.status='published'", "r.batch_id<=%s"], [dataset, latest_version() if version is None else version]
    if code:
        filters.append("r.code=%s")
        args.append(code)
    query = "SELECT payload FROM (SELECT DISTINCT ON(r.record_key) r.record_key,r.period,r.code,r.payload FROM source_records r JOIN data_batches b ON b.id=r.batch_id WHERE " + " AND ".join(filters) + " ORDER BY r.record_key,r.batch_id DESC) q ORDER BY period,code,record_key"
    with connect() as conn, conn.cursor(name="stock_export") as cursor:
        cursor.execute(query, args)
        for row in cursor:
            yield row["payload"]

