"""scraper.db._is_connection_broken darf keine Transaktion offen lassen."""

import psycopg2
from psycopg2.extensions import TRANSACTION_STATUS_IDLE, TRANSACTION_STATUS_INTRANS

from scraper.db import _is_connection_broken


class _Cur:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql):
        if self.conn.fail:
            raise psycopg2.OperationalError("server closed the connection")
        self.conn.status = TRANSACTION_STATUS_INTRANS

    def fetchone(self):
        return (1,)


class _Conn:
    closed = 0

    def __init__(self, status, fail=False):
        self.status, self.fail, self.rollbacks = status, fail, 0

    def get_transaction_status(self):
        return self.status

    def cursor(self):
        return _Cur(self)

    def rollback(self):
        self.rollbacks += 1
        self.status = TRANSACTION_STATUS_IDLE


def test_ping_closes_transaction_it_opened():
    c = _Conn(TRANSACTION_STATUS_IDLE)
    assert _is_connection_broken(c) is False
    assert c.rollbacks == 1 and c.status == TRANSACTION_STATUS_IDLE


def test_ping_keeps_foreign_transaction():
    """Läuft schon eine Transaktion (ungespeicherte Arbeit), nicht zurückrollen."""
    c = _Conn(TRANSACTION_STATUS_INTRANS)
    assert _is_connection_broken(c) is False
    assert c.rollbacks == 0


def test_broken_connection_detected():
    assert _is_connection_broken(_Conn(TRANSACTION_STATUS_IDLE, fail=True)) is True
    assert _is_connection_broken(None) is True
