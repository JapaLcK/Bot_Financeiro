"""
db/connection.py — Conexão com o banco de dados PostgreSQL.
"""
import os
import psycopg
from psycopg.rows import dict_row


def get_conn():
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL não está definido.")
    return psycopg.connect(database_url, row_factory=dict_row)
