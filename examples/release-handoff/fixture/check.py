"""Run a schema or migration check against an in-memory SQLite database."""
from contextlib import closing
from pathlib import Path
import sqlite3
import sys


def check(mode):
    with closing(sqlite3.connect(":memory:")) as db:
        db.executescript(Path("schema.sql").read_text(encoding="utf-8"))
        db.execute("INSERT INTO invoices (id, cents) VALUES (?, ?)", (1, 950))
        if mode == "schema":
            rows = db.execute("SELECT id, cents FROM invoices").fetchall()
            expected = [(1, 950)]
        elif mode == "migration":
            db.executescript(Path("migration.sql").read_text(encoding="utf-8"))
            rows = db.execute("SELECT id, cents, approved FROM invoices").fetchall()
            expected = [(1, 950, 0)]
        else:
            raise ValueError(f"Unknown check {mode!r}")
    if rows != expected:
        raise ValueError(f"{mode} expected {expected!r}, got {rows!r}")
    print(f"{mode} passed")


if __name__ == "__main__":
    check(sys.argv[1])
