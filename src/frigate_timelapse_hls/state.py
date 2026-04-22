from __future__ import annotations

import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path

from .models import SessionMode, SessionState, SessionStatus, SourceClip

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS day_sessions (
    camera TEXT NOT NULL,
    day TEXT NOT NULL,
    session_start TEXT NOT NULL,
    session_end TEXT NOT NULL,
    status TEXT NOT NULL,
    mode TEXT NOT NULL,
    playlist_path TEXT NOT NULL,
    segment_dir TEXT NOT NULL,
    last_discovered_clip_id TEXT,
    last_ingested_clip_id TEXT,
    discovered_clip_count INTEGER NOT NULL,
    ingested_clip_count INTEGER NOT NULL,
    ffmpeg_pid INTEGER,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (camera, day)
);

CREATE TABLE IF NOT EXISTS ingested_source_clips (
    camera TEXT NOT NULL,
    day TEXT NOT NULL,
    clip_id TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    start_time TEXT NOT NULL,
    duration_seconds REAL NOT NULL,
    trim_start_seconds REAL NOT NULL,
    ingested_at TEXT NOT NULL,
    PRIMARY KEY (camera, day, clip_id)
);
"""


class StateStore:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as connection:
            connection.executescript(SCHEMA)

    @contextmanager
    def connection(self) -> Generator[sqlite3.Connection]:
        connection: sqlite3.Connection = sqlite3.connect(self._db_path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def upsert_day_session(self, session: SessionState) -> None:
        with self.connection() as connection:
            connection.execute(
                """
                INSERT INTO day_sessions (
                    camera, day, session_start, session_end, status, mode,
                    playlist_path, segment_dir, last_discovered_clip_id,
                    last_ingested_clip_id, discovered_clip_count, ingested_clip_count,
                    ffmpeg_pid, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(camera, day) DO UPDATE SET
                    session_start=excluded.session_start,
                    session_end=excluded.session_end,
                    status=excluded.status,
                    mode=excluded.mode,
                    playlist_path=excluded.playlist_path,
                    segment_dir=excluded.segment_dir,
                    last_discovered_clip_id=excluded.last_discovered_clip_id,
                    last_ingested_clip_id=excluded.last_ingested_clip_id,
                    discovered_clip_count=excluded.discovered_clip_count,
                    ingested_clip_count=excluded.ingested_clip_count,
                    ffmpeg_pid=excluded.ffmpeg_pid,
                    updated_at=excluded.updated_at
                """,
                (
                    session.camera,
                    session.day.isoformat(),
                    session.session_start.isoformat(),
                    session.session_end.isoformat(),
                    session.status.value,
                    session.mode.value,
                    str(session.playlist_path),
                    str(session.segment_dir),
                    session.last_discovered_clip_id,
                    session.last_ingested_clip_id,
                    session.discovered_clip_count,
                    session.ingested_clip_count,
                    session.ffmpeg_pid,
                    session.updated_at.isoformat(),
                ),
            )

    def get_day_session(self, *, camera: str, day: date) -> SessionState | None:
        with self.connection() as connection:
            row = connection.execute(
                """
                SELECT camera, day, session_start, session_end, status, mode,
                       playlist_path, segment_dir, last_discovered_clip_id,
                       last_ingested_clip_id, discovered_clip_count,
                       ingested_clip_count, ffmpeg_pid, updated_at
                FROM day_sessions
                WHERE camera = ? AND day = ?
                """,
                (camera, day.isoformat()),
            ).fetchone()
        if row is None:
            return None
        return SessionState(
            camera=row["camera"],
            day=date.fromisoformat(row["day"]),
            session_start=datetime.fromisoformat(row["session_start"]),
            session_end=datetime.fromisoformat(row["session_end"]),
            status=SessionStatus(row["status"]),
            mode=SessionMode(row["mode"]),
            playlist_path=Path(row["playlist_path"]),
            segment_dir=Path(row["segment_dir"]),
            last_discovered_clip_id=row["last_discovered_clip_id"],
            last_ingested_clip_id=row["last_ingested_clip_id"],
            discovered_clip_count=row["discovered_clip_count"],
            ingested_clip_count=row["ingested_clip_count"],
            ffmpeg_pid=row["ffmpeg_pid"],
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def reset_day_session(self, *, camera: str, day: date) -> None:
        with self.connection() as connection:
            connection.execute(
                "DELETE FROM ingested_source_clips WHERE camera = ? AND day = ?",
                (camera, day.isoformat()),
            )
            connection.execute(
                "DELETE FROM day_sessions WHERE camera = ? AND day = ?",
                (camera, day.isoformat()),
            )

    def list_ingested_clip_ids(self, *, camera: str, day: date) -> set[str]:
        with self.connection() as connection:
            rows = connection.execute(
                """
                SELECT clip_id
                FROM ingested_source_clips
                WHERE camera = ? AND day = ?
                """,
                (camera, day.isoformat()),
            ).fetchall()
        return {str(row["clip_id"]) for row in rows}

    def list_ingested_source_ids(self, *, camera: str, day: date) -> set[str]:
        with self.connection() as connection:
            rows = connection.execute(
                """
                SELECT DISTINCT relative_path
                FROM ingested_source_clips
                WHERE camera = ? AND day = ?
                """,
                (camera, day.isoformat()),
            ).fetchall()
        return {str(row["relative_path"]) for row in rows}

    def mark_clip_ingested(
        self,
        *,
        camera: str,
        day: date,
        clip: SourceClip,
        ingested_at: datetime,
    ) -> None:
        with self.connection() as connection:
            connection.execute(
                """
                INSERT INTO ingested_source_clips (
                    camera, day, clip_id, relative_path, start_time,
                    duration_seconds, trim_start_seconds, ingested_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(camera, day, clip_id) DO UPDATE SET
                    ingested_at=excluded.ingested_at
                """,
                (
                    camera,
                    day.isoformat(),
                    clip.ingest_id,
                    clip.relative_path.as_posix(),
                    clip.start_time.isoformat(),
                    clip.duration_seconds,
                    clip.trim_start_seconds,
                    ingested_at.isoformat(),
                ),
            )
