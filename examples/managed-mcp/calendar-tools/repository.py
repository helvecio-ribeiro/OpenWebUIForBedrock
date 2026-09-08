import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

DATABASE_PATH = Path(
    os.getenv('CALENDAR_DATABASE_PATH', Path(__file__).parent / 'data/shared-calendar.db')
).expanduser()


class CalendarRepository:
    """SQLite repository for one shared calendar with recoverable deletion."""

    def __init__(self, path: str | Path = DATABASE_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._migrate()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA foreign_keys=ON')
        connection.execute('PRAGMA journal_mode=WAL')
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _migrate(self) -> None:
        with self.connect() as db:
            db.executescript(
                '''
                CREATE TABLE IF NOT EXISTS events (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    start TEXT NOT NULL,
                    end TEXT,
                    all_day INTEGER NOT NULL DEFAULT 0,
                    location TEXT NOT NULL DEFAULT '',
                    reminder_minutes INTEGER NOT NULL DEFAULT 10,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    deleted_at TEXT
                );
                CREATE INDEX IF NOT EXISTS events_time_idx ON events(start, end);
                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    snapshot TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                '''
            )

    @staticmethod
    def _event(row: sqlite3.Row) -> dict[str, Any]:
        event = dict(row)
        event['all_day'] = bool(event['all_day'])
        return event

    def search(self, query=None, start=None, end=None, count=20) -> list[dict[str, Any]]:
        clauses = ['deleted_at IS NULL']
        values: list[Any] = []
        if query:
            clauses.append("lower(title || ' ' || description || ' ' || location) LIKE ?")
            values.append(f'%{query.lower()}%')
        if start:
            clauses.append('start >= ?')
            values.append(start)
        if end:
            clauses.append('start <= ?')
            values.append(end)
        values.append(max(1, min(int(count), 100)))
        with self.connect() as db:
            rows = db.execute(
                f"SELECT * FROM events WHERE {' AND '.join(clauses)} ORDER BY start LIMIT ?",
                values,
            ).fetchall()
        return [self._event(row) for row in rows]

    def get(self, event_id: str, include_deleted=False) -> dict[str, Any] | None:
        suffix = '' if include_deleted else ' AND deleted_at IS NULL'
        with self.connect() as db:
            row = db.execute(f'SELECT * FROM events WHERE id=?{suffix}', (event_id,)).fetchone()
        return self._event(row) if row else None

    def create(self, **fields) -> dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        event = {
            'id': str(uuid.uuid4()), 'title': fields['title'], 'description': fields.get('description') or '',
            'start': fields['start'], 'end': fields.get('end'), 'all_day': bool(fields.get('all_day')),
            'location': fields.get('location') or '', 'reminder_minutes': fields.get('reminder_minutes', 10),
            'created_at': now, 'updated_at': now, 'deleted_at': None,
        }
        with self.connect() as db:
            db.execute(
                'INSERT INTO events VALUES (:id,:title,:description,:start,:end,:all_day,:location,:reminder_minutes,:created_at,:updated_at,:deleted_at)',
                event,
            )
            self._audit(db, event, 'created')
        return event

    def update(self, event_id: str, **changes) -> dict[str, Any]:
        event = self.get(event_id)
        if not event:
            raise KeyError('Event not found')
        allowed = {'title', 'description', 'start', 'end', 'all_day', 'location', 'reminder_minutes'}
        event.update({key: value for key, value in changes.items() if key in allowed and value is not None})
        event['updated_at'] = datetime.now(timezone.utc).isoformat()
        with self.connect() as db:
            db.execute(
                '''UPDATE events SET title=:title,description=:description,start=:start,end=:end,
                all_day=:all_day,location=:location,reminder_minutes=:reminder_minutes,
                updated_at=:updated_at WHERE id=:id AND deleted_at IS NULL''', event,
            )
            self._audit(db, event, 'updated')
        return event

    def delete(self, event_id: str) -> dict[str, Any]:
        event = self.get(event_id)
        if not event:
            raise KeyError('Event not found')
        event['deleted_at'] = datetime.now(timezone.utc).isoformat()
        with self.connect() as db:
            db.execute('UPDATE events SET deleted_at=?,updated_at=? WHERE id=?', (event['deleted_at'], event['deleted_at'], event_id))
            self._audit(db, event, 'deleted')
        return event

    @staticmethod
    def _audit(db, event, action):
        db.execute('INSERT INTO audit_log(event_id,action,snapshot,created_at) VALUES(?,?,?,?)',
                   (event['id'], action, json.dumps(event), datetime.now(timezone.utc).isoformat()))
