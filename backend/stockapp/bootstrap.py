"""创建本地应用角色/数据库；管理员密码只从环境或交互输入读取。"""
import getpass
import os
from pathlib import Path
import secrets

import psycopg
from psycopg import sql

from .config import ROOT


def main():
    env_path = ROOT / ".env"
    password = os.environ.get("STOCK_BOOTSTRAP_PASSWORD") or getpass.getpass("PostgreSQL 管理员密码: ")
    with psycopg.connect(host="localhost", port=5432, dbname="postgres", user="postgres",
                         password=password, autocommit=True, connect_timeout=5) as conn:
        exists = conn.execute("SELECT 1 FROM pg_database WHERE datname='stock_alei'").fetchone()
        role = conn.execute("SELECT 1 FROM pg_roles WHERE rolname='stock_alei_app'").fetchone()
        if env_path.exists():
            if not exists or not role:
                raise RuntimeError("本地配置已存在但数据库或角色缺失，请检查，未覆盖配置。")
        else:
            if role:
                raise RuntimeError("应用角色已存在且本地配置缺失，未重置其密码，请提供现有配置。")
            app_password = secrets.token_urlsafe(36)
            conn.execute(sql.SQL("CREATE ROLE stock_alei_app LOGIN PASSWORD {}").format(sql.Literal(app_password)))
            if not exists:
                conn.execute("CREATE DATABASE stock_alei OWNER stock_alei_app ENCODING 'UTF8'")
            else:
                conn.execute("GRANT CONNECT ON DATABASE stock_alei TO stock_alei_app")
            env_path.write_text("STOCK_DB_HOST=localhost\nSTOCK_DB_PORT=5432\nSTOCK_DB_NAME=stock_alei\n"
                                "STOCK_DB_USER=stock_alei_app\nSTOCK_DB_PASSWORD=" + app_password + "\n", encoding="utf-8")
            print("数据库已检查，应用角色与本地私密配置已准备。")
    with psycopg.connect(host="localhost", port=5432, dbname="stock_alei", user="postgres", password=password) as conn:
        conn.execute("GRANT USAGE,CREATE ON SCHEMA public TO stock_alei_app")
    from .db import migrate
    migrate()
    print("应用数据库迁移完成。管理员密码未保存。")


if __name__ == "__main__":
    main()

