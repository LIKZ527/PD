import os
from contextlib import contextmanager

import pymysql


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise ValueError(f"Missing required env var: {name}")
    return value


def _get_db_config() -> dict:
    return {
        "host": _require_env("MYSQL_HOST"),
        "port": int(_require_env("MYSQL_PORT")),
        "user": _require_env("MYSQL_USER"),
        "password": _require_env("MYSQL_PASSWORD"),
        "database": _require_env("MYSQL_DATABASE"),
        "charset": os.getenv("MYSQL_CHARSET", "utf8mb4"),
        "autocommit": True,
        "cursorclass": pymysql.cursors.DictCursor,
    }


@contextmanager
def get_conn():
    config = _get_db_config()
    connection = pymysql.connect(**config)
    try:
        yield connection
    finally:
        connection.close()


@contextmanager
def get_conn_tuple():
    """与 DictCursor 的 get_conn 并列：TL 比价迁移代码使用元组游标（row[0] 等）。"""
    config = {k: v for k, v in _get_db_config().items() if k != "cursorclass"}
    connection = pymysql.connect(**config)
    try:
        yield connection
    finally:
        connection.close()
