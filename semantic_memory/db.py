r"""SQLite schema: facts (topics), code symbols, calls, references, embeddings.

Fact ids live inside the topic range (config.TOPIC_SPAN), symbol ids start at
config.SYMBOL_ID_BASE. FTS5 and vectors exist for both kinds, but in separate
tables, so code search does not interfere with fact search.
"""

import os
import sqlite3

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);

CREATE TABLE IF NOT EXISTS fact (
  id INTEGER PRIMARY KEY, topic TEXT, num INTEGER, text TEXT
);
CREATE VIRTUAL TABLE IF NOT EXISTS fact_fts USING fts5(
  text, topic UNINDEXED, tokenize='unicode61 remove_diacritics 2'
);
CREATE TABLE IF NOT EXISTS fact_vec (id INTEGER PRIMARY KEY, emb BLOB);

CREATE TABLE IF NOT EXISTS file (
  path TEXT PRIMARY KEY, lang TEXT, lines INTEGER, bytes INTEGER,
  hash TEXT, summary TEXT, imports TEXT
);
CREATE TABLE IF NOT EXISTS sym (
  id INTEGER PRIMARY KEY, file TEXT, name TEXT, qual TEXT, kind TEXT,
  start INTEGER, end INTEGER, sig TEXT, doc TEXT, body TEXT
);
CREATE VIRTUAL TABLE IF NOT EXISTS sym_fts USING fts5(
  text, file UNINDEXED, lang UNINDEXED, tokenize='unicode61 remove_diacritics 2'
);
CREATE TABLE IF NOT EXISTS sym_vec (id INTEGER PRIMARY KEY, emb BLOB);

CREATE TABLE IF NOT EXISTS call (
  sym_id INTEGER, callee TEXT, file TEXT, line INTEGER
);
CREATE TABLE IF NOT EXISTS ref (
  name TEXT, file TEXT, line INTEGER
);
CREATE INDEX IF NOT EXISTS ix_sym_name ON sym(name);
CREATE INDEX IF NOT EXISTS ix_sym_file ON sym(file);
CREATE INDEX IF NOT EXISTS ix_call_callee ON call(callee);
CREATE INDEX IF NOT EXISTS ix_ref_name ON ref(name);
"""


def connect(db_path=None):
    path = db_path or config.db_path(os.getcwd())
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=5000")
    con.executescript(SCHEMA)
    con.commit()
    return con


def meta_get(con, k):
    row = con.execute("SELECT v FROM meta WHERE k = ?", (k,)).fetchone()
    return row[0] if row else None


def meta_set(con, k, v):
    con.execute("INSERT OR REPLACE INTO meta (k, v) VALUES (?, ?)", (k, v))
