from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock

import pytest

from frigate_timelapse_hls.config import Settings
from frigate_timelapse_hls.hls import LiveHLSPublisher
from frigate_timelapse_hls.ingest import ContinuousClipIngestor
from frigate_timelapse_hls.library import LibraryPublisher
from frigate_timelapse_hls.models import SessionMode, SessionState, SessionStatus
from frigate_timelapse_hls.pipeline import PipelineServices, TimelapsePipeline
from frigate_timelapse_hls.scanner import FrigateVodScanner
from frigate_timelapse_hls.state import StateStore
from frigate_timelapse_hls.worker import ContinuousFFmpegWorker


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
