from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum
from pathlib import Path


@dataclass(slots=True, frozen=True)
class SourceClip:
    path: Path
    camera: str
    start_time: datetime
    duration_seconds: float
    relative_path: Path
    trim_start_seconds: float = 0.0

    @property
    def end_time(self) -> datetime:
        return self.start_time + self.duration_delta

    @property
    def duration_delta(self):
        return timedelta(seconds=self.duration_seconds)

    @property
    def source_id(self) -> str:
        return self.relative_path.as_posix()

    @property
    def ingest_id(self) -> str:
        return (
            f"{self.relative_path.as_posix()}|"
            f"{self.trim_start_seconds:.3f}|"
            f"{self.duration_seconds:.3f}"
        )

class SessionMode(StrEnum):
    IDLE = "idle"
    CATCH_UP = "catch_up"
    LIVE_FOLLOW = "live_follow"
    COMPLETED = "completed"


class SessionStatus(StrEnum):
    STARTING = "starting"
    RUNNING = "running"
    STOPPED = "stopped"
    FAILED = "failed"
    COMPLETED = "completed"


@dataclass(slots=True, frozen=True)
class SessionState:
    camera: str
    day: date
    session_start: datetime
    session_end: datetime
    status: SessionStatus
    mode: SessionMode
    playlist_path: Path
    segment_dir: Path
    last_discovered_clip_id: str | None
    last_ingested_clip_id: str | None
    discovered_clip_count: int
    ingested_clip_count: int
    ffmpeg_pid: int | None
    updated_at: datetime


@dataclass(slots=True, frozen=True)
class SessionSnapshot:
    observed_at: datetime
    day: date
    session_start: datetime
    session_end: datetime
    query_end: datetime
    clips: tuple[SourceClip, ...]


@dataclass(slots=True, frozen=True)
class LoopResult:
    scanned_day: date
    clips_seen: int
    clips_pending: int
    clips_ingested: int
    session_mode: SessionMode
    worker_running: bool
