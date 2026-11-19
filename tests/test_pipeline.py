from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, call, patch

import pytest

from frigate_timelapse_hls.config import Settings
from frigate_timelapse_hls.hls import LiveHLSPublisher
from frigate_timelapse_hls.ingest import ContinuousClipIngestor
from frigate_timelapse_hls.library import LibraryPublisher
from frigate_timelapse_hls.models import SessionMode, SessionState, SessionStatus, SourceClip
from frigate_timelapse_hls.pipeline import PipelineServices, TimelapsePipeline
from frigate_timelapse_hls.scanner import FrigateVodScanner
from frigate_timelapse_hls.state import StateStore
from frigate_timelapse_hls.worker import ContinuousFFmpegWorker


@pytest.fixture
def session_pipeline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[TimelapsePipeline, SessionState]:
    monkeypatch.setattr(Settings, "_config_path", tmp_path / "absent.toml")
    settings = Settings(
        app={"timezone": "Europe/Helsinki"},
        camera={"name": "cam"},
        paths={
            "recordings_source_root": tmp_path / "recordings",
            "state_db": tmp_path / "state.sqlite3",
            "output_root": tmp_path / "output",
        },
        frigate={"base_url": "http://unused", "recordings_root": "/recordings"},
        schedule={"start_time": "07:00", "end_time": "19:00"},
        thumbnail={"offset_seconds": 200},
    )
    start = datetime(2026, 11, 18, 8, tzinfo=settings.tzinfo)
    publisher = LiveHLSPublisher()
    paths = publisher.prepare_day_session(
        camera=settings.camera.name,
        day_label=start.date().isoformat(),
        output_root=settings.paths.output_root,
        reset=False,
    )
    (paths.segment_dir / "segment-000000.ts").write_bytes(b"media")
    paths.live_playlist_path.write_text("#EXTM3U\n#EXTINF:2,\nsegment-000000.ts\n#EXT-X-ENDLIST\n")
    publisher.publish_snapshot_playlist(paths)
    (paths.publish_dir / "session.json").write_text(
        json.dumps(
            {
                "schedule_start": start.isoformat(),
                "schedule_end": (start + timedelta(hours=12)).isoformat(),
                "render_speed": settings.timelapse.timelapse_speed,
                "poster_target": (start + timedelta(seconds=200)).isoformat(),
            }
        )
    )
    (paths.publish_dir / "poster.jpg").write_bytes(b"cached poster")
    (paths.publish_dir / "poster.json").write_text(
        json.dumps(
            {
                "generation_id": (paths.publish_dir / "generation.txt").read_text().strip(),
                "target": (start + timedelta(seconds=200)).isoformat(),
            }
        )
    )
    store = StateStore(settings.paths.state_db)
    clip = SourceClip(Path("/source.mp4"), "cam", start, 10, Path("source.mp4"))
    store.mark_clip_ingested(camera="cam", day=start.date(), clip=clip, ingested_at=start)
    state = SessionState(
        camera="cam",
        day=start.date(),
        session_start=start,
        session_end=start + timedelta(hours=12),
        status=SessionStatus.COMPLETED,
        mode=SessionMode.COMPLETED,
        playlist_path=paths.playlist_path,
        segment_dir=paths.segment_dir,
        last_discovered_clip_id=clip.source_id,
        last_ingested_clip_id=clip.source_id,
        discovered_clip_count=1,
        ingested_clip_count=1,
        ffmpeg_pid=None,
        updated_at=start,
    )
    store.upsert_day_session(state)
    scanner = Mock(spec=FrigateVodScanner)
    scanner.scan_range.return_value = [
        replace(clip, start_time=start - timedelta(hours=1), relative_path=Path("earlier.mp4")),
        clip,
    ]
    worker = Mock(spec=ContinuousFFmpegWorker, pid=123)
    worker.is_running.return_value = False
    worker.start.side_effect = lambda _: setattr(worker.is_running, "return_value", True)
    worker.stop.side_effect = lambda: setattr(worker.is_running, "return_value", False)
    pipeline = TimelapsePipeline(
        PipelineServices(
            settings=settings,
            store=store,
            scanner=scanner,
            ingestor=Mock(spec=ContinuousClipIngestor),
            worker=worker,
            hls_publisher=publisher,
            library=LibraryPublisher(settings, store),
        )
    )
    return pipeline, state


@pytest.mark.parametrize("restart_policy", ["resume", "regenerate"])
@pytest.mark.parametrize(
    "schedule",
    [
        {"start_time": "08:00", "end_time": "20:00"},
        {"start_time": "07:00", "end_time": "19:00"},
        {"start_time": "09:00", "end_time": "21:00"},
        {"start_time": "23:30", "end_time": "23:59"},
    ],
)
def test_completed_session_ignores_schedule_changes_and_unseen_clips(
    session_pipeline: tuple[TimelapsePipeline, SessionState],
    restart_policy: str,
    schedule: dict,
) -> None:
    pipeline, state = session_pipeline
    services = pipeline._services
    services.settings = Settings(
        **(
            services.settings.model_dump()
            | {
                "app": {"timezone": "Europe/Helsinki", "restart_policy": restart_policy},
                "schedule": schedule,
            }
        )
    )
    assert services.library is not None
    services.library.settings = services.settings
    before = {p.name: p.read_bytes() for p in state.segment_dir.iterdir()}
    now = state.session_start.replace(hour=23)

    scan = pipeline.run_scan(now)
    result, sleep = pipeline.run_loop_iteration(now)
    next_start = services.settings.window_start_for_day(state.day + timedelta(days=1))

    assert scan == result
    assert result.session_mode == SessionMode.COMPLETED
    assert result.clips_pending == 0 and result.clips_ingested == 1
    assert not result.worker_running
    assert sleep == (next_start + timedelta(seconds=30) - now).total_seconds()
    services.scanner.scan_range.assert_not_called()
    services.ingestor.ingest_clip.assert_not_called()
    services.worker.start.assert_not_called()
    assert services.store.get_day_session(camera="cam", day=state.day) == state
    assert {p.name: p.read_bytes() for p in state.segment_dir.iterdir()} == before
    assert services.library.publish(now)["days"][0]["poster_url"]


@pytest.mark.parametrize(
    "schedule",
    [
        {"start_time": "07:00", "end_time": "19:00"},
        {"start_time": "09:00", "end_time": "21:00"},
    ],
)
def test_resumed_session_uses_saved_window_and_next_day_uses_new_schedule(
    session_pipeline: tuple[TimelapsePipeline, SessionState],
    schedule: dict,
) -> None:
    pipeline, state = session_pipeline
    services = pipeline._services
    services.settings = Settings(**(services.settings.model_dump() | {"schedule": schedule}))
    assert services.library is not None
    services.library.settings = services.settings
    services.store.upsert_day_session(
        replace(
            state,
            status=SessionStatus.STOPPED,
            mode=SessionMode.LIVE_FOLLOW,
        )
    )
    pending = SourceClip(
        Path("/pending.mp4"),
        "cam",
        state.session_start + timedelta(minutes=2),
        10,
        Path("pending.mp4"),
    )
    services.scanner.scan_range.return_value = [pending]
    now = state.session_start + timedelta(minutes=5)
    scan = pipeline.run_scan(now)
    assert scan.clips_pending == 1
    services.scanner.scan_range.assert_called_once_with(
        start_time=state.session_start,
        end_time=now,
    )
    services.scanner.scan_range.reset_mock()
    with patch("frigate_timelapse_hls.pipeline.datetime", wraps=datetime) as clock:
        clock.now.return_value = now
        result, _ = pipeline.run_loop_iteration(now)
    saved = services.store.get_day_session(camera="cam", day=state.day)
    assert saved is not None
    assert (saved.session_start, saved.session_end) == (state.session_start, state.session_end)
    services.scanner.scan_range.assert_has_calls(
        [
            call(start_time=state.session_start, end_time=now),
            call(start_time=state.session_start, end_time=now),
        ]
    )
    services.worker.start.assert_called_once()
    services.ingestor.ingest_clip.assert_called_once_with(pending, services.worker)
    assert result.session_mode == SessionMode.LIVE_FOLLOW
    session = json.loads((state.segment_dir / "session.json").read_text())
    assert session["poster_target"] == (state.session_start + timedelta(seconds=200)).isoformat()

    services.scanner.scan_range.reset_mock()
    services.scanner.scan_range.return_value = []
    now = state.session_end + timedelta(minutes=5)
    result, sleep = pipeline.run_loop_iteration(now)
    assert result.session_mode == SessionMode.COMPLETED
    services.scanner.scan_range.assert_called_once_with(
        start_time=state.session_end - timedelta(seconds=50),
        end_time=state.session_end,
    )
    next_day = state.day + timedelta(days=1)
    next_start = services.settings.window_start_for_day(next_day)
    assert sleep == (next_start + timedelta(seconds=30) - now).total_seconds()

    services.scanner.scan_range.reset_mock()
    now = next_start + timedelta(seconds=30)
    with patch("frigate_timelapse_hls.pipeline.datetime", wraps=datetime) as clock:
        clock.now.return_value = now
        pipeline.run_loop_iteration(now)
    services.scanner.scan_range.assert_called_once_with(start_time=next_start, end_time=now)
    saved = services.store.get_day_session(camera="cam", day=next_day)
    assert saved is not None
    assert saved.session_start == next_start
    assert saved.session_end == services.settings.window_end_for_day(next_day)


def test_resume_before_saved_start_waits_for_saved_schedule(
    session_pipeline: tuple[TimelapsePipeline, SessionState],
) -> None:
    pipeline, state = session_pipeline
    services = pipeline._services
    services.store.upsert_day_session(replace(state, status=SessionStatus.STOPPED))
    now = state.session_start - timedelta(minutes=30)
    result, sleep = pipeline.run_loop_iteration(now)
    assert result.session_mode == SessionMode.IDLE
    assert sleep == 30 * 60 + 30
    services.scanner.scan_range.assert_not_called()
    services.worker.start.assert_not_called()


def test_regenerate_interrupted_session_uses_new_schedule(
    session_pipeline: tuple[TimelapsePipeline, SessionState],
) -> None:
    pipeline, state = session_pipeline
    services = pipeline._services
    services.settings = services.settings.model_copy(
        update={
            "app": services.settings.app.model_copy(update={"restart_policy": "regenerate"}),
        }
    )
    assert services.library is not None
    services.library.settings = services.settings
    services.store.upsert_day_session(
        replace(
            state,
            status=SessionStatus.STOPPED,
            mode=SessionMode.LIVE_FOLLOW,
        )
    )
    generation_path = state.segment_dir / "generation.txt"
    generation = generation_path.read_text()
    now = state.session_start + timedelta(minutes=5)
    services.scanner.scan_range.return_value = []
    with patch("frigate_timelapse_hls.pipeline.datetime", wraps=datetime) as clock:
        clock.now.return_value = now
        pipeline.run_loop_iteration(now)
    services.scanner.scan_range.assert_called_once_with(
        start_time=services.settings.window_start_for_day(state.day),
        end_time=now,
    )
    saved = services.store.get_day_session(camera="cam", day=state.day)
    assert saved is not None
    assert saved.session_start == services.settings.window_start_for_day(state.day)
    assert saved.session_end == services.settings.window_end_for_day(state.day)
    assert saved.ingested_clip_count == 0
    assert services.store.list_ingested_source_ids(camera="cam", day=state.day) == set()
    assert generation_path.read_text() != generation
    services.worker.start.assert_called_once()


@pytest.mark.parametrize("worker_running", [False, True])
@pytest.mark.parametrize("completed", [False, True], ids=["interrupted", "completed"])
def test_shutdown_preserves_completion_and_publishes_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, completed: bool, worker_running: bool
) -> None:
    monkeypatch.setattr(Settings, "_config_path", tmp_path / "absent.toml")
    settings = Settings(
        app={"timezone": "Europe/Helsinki"},
        camera={"name": "cam"},
        paths={
            "recordings_source_root": tmp_path / "recordings",
            "state_db": tmp_path / "state.sqlite3",
            "output_root": tmp_path / "output",
        },
        frigate={"base_url": "http://unused", "recordings_root": "/recordings"},
    )
    start = datetime(2026, 11, 6, 8, tzinfo=settings.tzinfo)
    publisher = LiveHLSPublisher()
    paths = publisher.prepare_day_session(
        camera=settings.camera.name,
        day_label=start.date().isoformat(),
        output_root=settings.paths.output_root,
        reset=False,
    )
    (paths.segment_dir / "segment-000000.ts").write_bytes(b"media")
    paths.live_playlist_path.write_text("#EXTM3U\n#EXTINF:2,\nsegment-000000.ts\n")
    generation = (paths.publish_dir / "generation.txt").read_text()
    store = StateStore(settings.paths.state_db)
    state = SessionState(
        camera=settings.camera.name,
        day=start.date(),
        session_start=start,
        session_end=start + timedelta(hours=12),
        status=SessionStatus.COMPLETED if completed else SessionStatus.RUNNING,
        mode=SessionMode.COMPLETED if completed else SessionMode.LIVE_FOLLOW,
        playlist_path=paths.playlist_path,
        segment_dir=paths.segment_dir,
        last_discovered_clip_id="source-2.mp4",
        last_ingested_clip_id="source-1.mp4",
        discovered_clip_count=2,
        ingested_clip_count=1,
        ffmpeg_pid=123 if worker_running else None,
        updated_at=start,
    )
    store.upsert_day_session(state)
    worker = Mock(spec=ContinuousFFmpegWorker)
    worker.is_running.return_value = worker_running
    pipeline = TimelapsePipeline(
        PipelineServices(
            settings=settings,
            store=store,
            scanner=Mock(spec=FrigateVodScanner),
            ingestor=Mock(spec=ContinuousClipIngestor),
            worker=worker,
            hls_publisher=publisher,
        )
    )
    pipeline._active_day = state.day

    pipeline.stop()

    saved = store.get_day_session(camera=state.camera, day=state.day)
    assert saved is not None
    assert saved == replace(
        state,
        status=SessionStatus.COMPLETED if completed else SessionStatus.STOPPED,
        ffmpeg_pid=None,
        updated_at=saved.updated_at,
    )
    if worker_running:
        worker.stop.assert_called_once_with()
    else:
        worker.stop.assert_not_called()
    assert "#EXT-X-ENDLIST" in paths.playlist_path.read_text()
    assert (paths.publish_dir / "generation.txt").read_text() == generation
    entry = LibraryPublisher(settings, store).publish(start + timedelta(days=1))["days"][0]
    assert entry["complete"] is completed
    assert entry["status"] == ("completed" if completed else "stopped")


@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_cleanup_runs_once_per_schedule_day_and_never_blocks_ingestion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cleanup_fails: bool
) -> None:
    monkeypatch.setattr(Settings, "_config_path", tmp_path / "absent.toml")
    settings = Settings(
        app={"timezone": "Europe/Helsinki"},
        camera={"name": "cam"},
        paths={
            "recordings_source_root": tmp_path,
            "state_db": tmp_path / "state.sqlite3",
            "output_root": tmp_path / "output",
        },
        frigate={"base_url": "http://unused", "recordings_root": "/recordings"},
        schedule={"start_time": "08:00", "end_time": "20:00"},
    )
    store = Mock(spec=StateStore)
    store.get_day_session.return_value = None
    store.prune_completed_clip_history.side_effect = [
        sqlite3.OperationalError("busy") if cleanup_fails else 7,
        3,
    ]
    worker = Mock(spec=ContinuousFFmpegWorker)
    worker.is_running.return_value = False
    pipeline = TimelapsePipeline(
        PipelineServices(
            settings=settings,
            store=store,
            scanner=Mock(spec=FrigateVodScanner),
            ingestor=Mock(spec=ContinuousClipIngestor),
            worker=worker,
            hls_publisher=LiveHLSPublisher(),
        )
    )
    start = datetime(2026, 11, 7, 8, tzinfo=settings.tzinfo)
    with patch.object(pipeline, "_build_snapshot", return_value=None) as scan:
        pipeline.run_loop_iteration(start - timedelta(seconds=1))
        store.prune_completed_clip_history.assert_not_called()
        for now in [
            start,
            start + timedelta(hours=1),
            start + timedelta(days=1, seconds=-1),
            start + timedelta(days=1),
        ]:
            pipeline.run_loop_iteration(now)
        assert scan.call_count == 5
    assert store.prune_completed_clip_history.call_args_list == [
        call(camera="cam", before_day=start.date()),
        call(camera="cam", before_day=(start + timedelta(days=1)).date()),
    ]


def test_snapshot_does_not_reuse_another_days_state(session_pipeline) -> None:
    pipeline, state = session_pipeline
    services = pipeline._services
    now = state.session_start.replace(hour=0) + timedelta(days=1, minutes=1)
    services.scanner.scan_range.return_value = []
    assert pipeline._build_snapshot(now=now, existing_state=state) is None
    services.scanner.scan_range.assert_not_called()
    now = services.settings.window_start_for_day(now.date()) + timedelta(minutes=1)
    snapshot = pipeline._build_snapshot(now=now, existing_state=state)
    assert snapshot is not None
    assert snapshot.day == now.date()
    assert snapshot.session_start == services.settings.window_start_for_day(now.date())


def test_catchup_crossing_midnight_finishes_original_day(session_pipeline) -> None:
    pipeline, state = session_pipeline
    services = pipeline._services
    services.store.upsert_day_session(
        replace(
            state,
            status=SessionStatus.STOPPED,
            mode=SessionMode.CATCH_UP,
        )
    )
    clip = SourceClip(
        Path("/late.mp4"), "cam", state.session_end - timedelta(seconds=20), 10, Path("late.mp4")
    )
    services.scanner.scan_range.return_value = [clip]
    before_midnight = state.session_end.replace(hour=23, minute=59)
    after_midnight = before_midnight + timedelta(minutes=2)
    with patch("frigate_timelapse_hls.pipeline.datetime", wraps=datetime) as clock:
        clock.now.return_value = after_midnight
        result, sleep = pipeline.run_loop_iteration(before_midnight)
    saved = services.store.get_day_session(camera="cam", day=state.day)
    assert saved.status == SessionStatus.COMPLETED
    assert result.scanned_day == state.day
    assert result.session_mode == SessionMode.COMPLETED
    assert saved.ingested_clip_count == 2
    assert services.store.get_day_session(camera="cam", day=after_midnight.date()) is None
    assert services.store.list_ingested_source_ids(camera="cam", day=state.day) == {
        "source.mp4",
        "late.mp4",
    }
    services.ingestor.ingest_clip.assert_called_once_with(clip, services.worker)
    services.worker.stop.assert_called_once()
    next_start = services.settings.window_start_for_day(after_midnight.date())
    assert sleep == (next_start + timedelta(seconds=30) - after_midnight).total_seconds()


@pytest.mark.parametrize("delay", [0, 5, 300])
def test_live_follow_queries_tail_before_session_end(session_pipeline, delay) -> None:
    pipeline, state = session_pipeline
    services = pipeline._services
    services.worker.is_running.return_value = True
    services.scanner.scan_range.return_value = []
    live = replace(state, status=SessionStatus.RUNNING, mode=SessionMode.LIVE_FOLLOW)
    now = state.session_end + timedelta(seconds=delay)
    snapshot = pipeline._build_snapshot(now=now, existing_state=live)
    assert snapshot is not None
    services.scanner.scan_range.assert_called_once_with(
        start_time=state.session_end - timedelta(seconds=50),
        end_time=state.session_end,
    )


@pytest.mark.parametrize(
    "schedule, hour",
    [
        ({"start_time": "07:00", "end_time": "19:00"}, 7),
        ({"start_time": "09:00", "end_time": "21:00"}, 9),
    ],
)
def test_missing_playlist_regenerates_using_current_window(
    session_pipeline, schedule, hour
) -> None:
    pipeline, state = session_pipeline
    services = pipeline._services
    services.settings = Settings(**(services.settings.model_dump() | {"schedule": schedule}))
    services.library.settings = services.settings
    services.store.upsert_day_session(
        replace(
            state,
            status=SessionStatus.STOPPED,
            mode=SessionMode.CATCH_UP,
        )
    )
    (state.segment_dir / "live.m3u8").unlink()
    generation = (state.segment_dir / "generation.txt").read_text()
    start = services.settings.window_start_for_day(state.day)
    now = start + timedelta(minutes=5)
    services.scanner.scan_range.return_value = []
    with patch("frigate_timelapse_hls.pipeline.datetime", wraps=datetime) as clock:
        clock.now.return_value = now
        result, _ = pipeline.run_loop_iteration(now)
    services.scanner.scan_range.assert_called_once_with(start_time=start, end_time=now)
    saved = services.store.get_day_session(camera="cam", day=state.day)
    assert saved.session_start.hour == hour
    assert saved.session_start == start
    assert saved.session_end == services.settings.window_end_for_day(state.day)
    assert saved.ingested_clip_count == 0
    assert services.store.list_ingested_source_ids(camera="cam", day=state.day) == set()
    assert (state.segment_dir / "generation.txt").read_text() != generation
    assert result.session_mode == SessionMode.LIVE_FOLLOW
    # The worker has no first playlist yet; subsequent scans must retain its window.
    assert pipeline._session_window(state.day, saved) == (saved.session_start, saved.session_end)
    services.worker.start.assert_called_once()


def test_missing_playlist_waits_until_current_schedule_start(session_pipeline) -> None:
    pipeline, state = session_pipeline
    services = pipeline._services
    services.settings = Settings(
        **(
            services.settings.model_dump()
            | {
                "schedule": {"start_time": "09:00", "end_time": "21:00"},
            }
        )
    )
    services.store.upsert_day_session(replace(state, status=SessionStatus.STOPPED))
    (state.segment_dir / "live.m3u8").unlink()
    before = {p.name: p.read_bytes() for p in state.segment_dir.iterdir()}
    now = state.session_start + timedelta(minutes=30)
    result, sleep = pipeline.run_loop_iteration(now)
    assert result.session_mode == SessionMode.IDLE
    assert sleep == 30 * 60 + 30
    services.scanner.scan_range.assert_not_called()
    services.worker.start.assert_not_called()
    assert {p.name: p.read_bytes() for p in state.segment_dir.iterdir()} == before


@pytest.mark.parametrize("today_status", [None, SessionStatus.STOPPED, SessionStatus.COMPLETED])
def test_catchup_after_todays_start_advances_to_unfinished_today(
    session_pipeline, today_status
) -> None:
    pipeline, state = session_pipeline
    services = pipeline._services
    services.settings = Settings(
        **(
            services.settings.model_dump()
            | {
                "schedule": {"start_time": "00:01", "end_time": "19:00"},
            }
        )
    )
    services.library.settings = services.settings
    services.store.upsert_day_session(
        replace(
            state,
            status=SessionStatus.STOPPED,
            mode=SessionMode.CATCH_UP,
        )
    )
    before_midnight = state.session_end.replace(hour=23, minute=59)
    now = before_midnight + timedelta(minutes=4)
    start = services.settings.window_start_for_day(now.date())
    end = services.settings.window_end_for_day(now.date())
    if today_status is not None:
        paths = services.hls_publisher.prepare_day_session(
            camera="cam",
            day_label=now.date().isoformat(),
            output_root=services.settings.paths.output_root,
            reset=False,
        )
        services.store.upsert_day_session(
            replace(
                state,
                day=now.date(),
                session_start=start,
                session_end=end,
                status=today_status,
                mode=SessionMode.CATCH_UP,
                playlist_path=paths.playlist_path,
                segment_dir=paths.segment_dir,
                ingested_clip_count=0,
                discovered_clip_count=0,
                last_discovered_clip_id=None,
                last_ingested_clip_id=None,
            )
        )
    clip = SourceClip(
        Path("/late.mp4"), "cam", state.session_end - timedelta(seconds=20), 10, Path("late.mp4")
    )
    services.scanner.scan_range.return_value = [clip]
    with patch("frigate_timelapse_hls.pipeline.datetime", wraps=datetime) as clock:
        clock.now.return_value = now
        result, sleep = pipeline.run_loop_iteration(before_midnight)
        assert result.scanned_day == state.day
        assert result.session_mode == SessionMode.COMPLETED
        if today_status == SessionStatus.COMPLETED:
            next_start = services.settings.window_start_for_day(now.date() + timedelta(days=1))
            assert sleep == (next_start + timedelta(seconds=30) - now).total_seconds()
        else:
            assert sleep == 0
        services.scanner.scan_range.reset_mock()
        services.scanner.scan_range.return_value = []
        result, _ = pipeline.run_loop_iteration(now)
    saved = services.store.get_day_session(camera="cam", day=now.date())
    assert saved.session_start == start
    assert saved.session_end == end
    assert result.scanned_day == now.date()
    if today_status == SessionStatus.COMPLETED:
        services.scanner.scan_range.assert_not_called()
        assert result.session_mode == SessionMode.COMPLETED
    else:
        services.scanner.scan_range.assert_called_once_with(start_time=start, end_time=now)
        assert result.session_mode == SessionMode.LIVE_FOLLOW
        assert services.worker.start.call_count == 2
