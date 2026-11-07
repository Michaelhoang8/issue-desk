import sqlite3
from pathlib import Path
from contextlib import closing
SCHEMA = "CREATE TABLE IF NOT EXISTS tickets (\n id INTEGER PRIMARY KEY, title TEXT NOT NULL, description TEXT NOT NULL,\n priority TEXT NOT NULL CHECK(priority IN ('low','medium','high')),\n status TEXT NOT NULL DEFAULT 'backlog' CHECK(status IN ('backlog','active','done')),\n created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')), assignee TEXT NOT NULL DEFAULT '', due_date TEXT NOT NULL DEFAULT '', version INTEGER NOT NULL DEFAULT 1, archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN (0,1)));\nCREATE TABLE IF NOT EXISTS events (\n id INTEGER PRIMARY KEY, ticket_id INTEGER NOT NULL REFERENCES tickets(id),\n detail TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')));"

def connect(path):
    db = sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    return db

def initialize(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with closing(connect(path)) as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.executescript(SCHEMA)

def rows(cursor):
    return [dict(row) for row in cursor.fetchall()]
