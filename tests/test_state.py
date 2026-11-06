from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from frigate_timelapse_hls.models import SessionMode, SessionState, SessionStatus, SourceClip
from frigate_timelapse_hls.state import StateStore


def session(camera: str, day: date, status: SessionStatus) -> SessionState:
    start = datetime.combine(day, datetime.min.time(), tzinfo=UTC)
    return SessionState(
        camera=camera,
        day=day,
        session_start=start,
        session_end=start + timedelta(hours=12),
        status=status,
        mode=(
            SessionMode.COMPLETED
            if status == SessionStatus.COMPLETED
            else SessionMode.LIVE_FOLLOW
        ),
        playlist_path=Path("/output") / camera / str(day) / "index.m3u8",
        segment_dir=Path("/output") / camera / str(day),
        last_discovered_clip_id="clip.mp4",
        last_ingested_clip_id="clip.mp4",
        discovered_clip_count=1,
        ingested_clip_count=1,
        ffmpeg_pid=None,
        updated_at=start,
    )


def test_prune_keeps_summaries_and_all_potentially_resumable_clip_records(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.sqlite3")
    today = date(2026, 11, 7)
    sessions = [
        session("cam", today - timedelta(days=index + 1), status)
        for index, status in enumerate(SessionStatus)
    ] + [
        session("cam", today, SessionStatus.COMPLETED),
        session("cam", today + timedelta(days=1), SessionStatus.COMPLETED),
        session("other", today - timedelta(days=1), SessionStatus.COMPLETED),
    ]
    for state in sessions:
        store.upsert_day_session(state)
    orphan = session("cam", today - timedelta(days=10), SessionStatus.COMPLETED)
    for state in [*sessions, orphan]:
        clip = SourceClip(
            path=tmp_path / "clip.mp4",
            camera=state.camera,
            start_time=state.session_start,
            duration_seconds=10,
            relative_path=Path(str(state.day)) / "clip.mp4",
        )
        store.mark_clip_ingested(
            camera=state.camera, day=state.day, clip=clip, ingested_at=state.updated_at
        )

    assert store.prune_completed_clip_history(camera="cam", before_day=today) == 1

    for state in sessions:
        assert store.get_day_session(camera=state.camera, day=state.day) == state
    for state in [*sessions, orphan]:
        should_prune = (
            state in sessions
            and state.camera == "cam"
            and state.day < today
            and state.status == SessionStatus.COMPLETED
        )
        remaining = store.list_ingested_source_ids(camera=state.camera, day=state.day)
        assert bool(remaining) != should_prune
    assert store.prune_completed_clip_history(camera="cam", before_day=today) == 0


@pytest.mark.parametrize("already_deleted", [False, True])
def test_prune_reclaims_disk_space_and_recovers_interrupted_compaction(
    tmp_path: Path, already_deleted: bool
) -> None:
    path = tmp_path / "state.sqlite3"
    store = StateStore(path)
    state = session("cam", date(2026, 11, 6), SessionStatus.COMPLETED)
    store.upsert_day_session(state)
    with store.connection() as connection:
        connection.executemany(
            "INSERT INTO ingested_source_clips VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                (
                    state.camera,
                    str(state.day),
                    f"{'x' * 200}-{index}",
                    f"recordings/{index}.mp4",
                    state.session_start.isoformat(),
                    10.0,
                    0.0,
                    state.updated_at.isoformat(),
                )
                for index in range(2000)
            ),
        )
    original_size = path.stat().st_size
    if already_deleted:
        with store.connection() as connection:
            connection.execute("DELETE FROM ingested_source_clips")

    removed = store.prune_completed_clip_history(camera="cam", before_day=date(2026, 11, 7))

    assert removed == (0 if already_deleted else 2000)
    assert path.stat().st_size < original_size / 4
    assert store.get_day_session(camera=state.camera, day=state.day) == state
    with store.connection() as connection:
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA freelist_count").fetchone()[0] == 0
