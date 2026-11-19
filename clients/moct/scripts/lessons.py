#!/usr/bin/env python3
"""Store short, user-approved acceptance checks locally. Uses only the stdlib."""

import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile


SCOPES = ("code", "data", "general", "interface", "other", "visual", "writing")
TABLE = "moct_lessons"
MAX_FIELD = 240


def field(value):
    if not isinstance(value, str):
        raise ValueError("Ожидается текст условия или проверки.")
    value = " ".join(value.split())
    if not 1 <= len(value) <= MAX_FIELD or not value.isprintable():
        raise ValueError("Используйте от 1 до 240 печатных символов на поле.")
    return value


def save(path, scope, note):
    if not isinstance(note, dict) or set(note) != {"when", "check"} or scope not in SCOPES:
        raise ValueError("Нужны область и JSON только с полями when и check.")
    when, check = field(note["when"]), field(note["check"])
    key = hashlib.sha256("\x00".join((scope, when, check)).encode("utf-8")).hexdigest()[:20]
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
    except FileExistsError:
        pass
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("""CREATE TABLE IF NOT EXISTS moct_lessons (
            id TEXT PRIMARY KEY, scope TEXT NOT NULL, applies TEXT NOT NULL, criterion TEXT NOT NULL
        )""")
        db.execute("INSERT INTO moct_lessons VALUES (?, ?, ?, ?) ON CONFLICT(id) DO NOTHING",
                   (key, scope, when, check))
    return {"id": key, "scope": scope, "when": when, "check": check}


def has_table(db):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                      (TABLE,)).fetchone() is not None


def list_notes(path, scope=None):
    if not path.exists():
        return []
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        if not has_table(db):
            return []
        query = 'SELECT id, scope, applies AS "when", criterion AS "check" FROM moct_lessons'
        args = ()
        if scope is not None:
            query += " WHERE scope IN (?, 'general')"
            args = (scope,)
        db.row_factory = sqlite3.Row
        return [dict(row) for row in db.execute(query + " ORDER BY rowid", args)]


def forget(path, key):
    if not path.exists():
        return False
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=rw", uri=True)) as db, db:
        return has_table(db) and db.execute("DELETE FROM moct_lessons WHERE id = ?", (key,)).rowcount == 1


def default_db(home=None):
    home = Path.home() if home is None else Path(home)
    path = home / ".moct" / "lessons.sqlite3"
    legacy = home / ".most" / "lessons.sqlite3"
    if path.exists() or not legacy.is_file():
        return path
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, filename = tempfile.mkstemp(prefix=".migration-", dir=path.parent)
    os.close(fd)
    temporary = Path(filename)
    try:
        with closing(sqlite3.connect(legacy.resolve().as_uri() + "?mode=ro", uri=True)) as source:
            with closing(sqlite3.connect(temporary)) as destination, destination:
                source.backup(destination)
                if destination.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='most_lessons'").fetchone():
                    destination.execute("ALTER TABLE most_lessons RENAME TO moct_lessons")
        try:
            os.link(temporary, path)
        except FileExistsError:
            pass
    finally:
        temporary.unlink(missing_ok=True)
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path)
    actions = parser.add_subparsers(dest="action", required=True)
    actions.add_parser("list").add_argument("--scope", choices=SCOPES)
    actions.add_parser("save").add_argument("--scope", required=True, choices=SCOPES)
    actions.add_parser("forget").add_argument("id")
    args = parser.parse_args()
    try:
        args.db = args.db if args.db is not None else default_db()
        if args.action == "save":
            raw = sys.stdin.buffer.read(4097)
            if len(raw) > 4096:
                raise ValueError("Слишком длинная запись.")
            result = save(args.db, args.scope, json.loads(raw.decode("utf-8")))
        elif args.action == "list":
            result = list_notes(args.db, args.scope)
        else:
            result = {"removed": forget(args.db, args.id)}
    except (OSError, ValueError, sqlite3.Error, RecursionError) as exc:
        parser.exit(1, f"moct: {exc}\n")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
