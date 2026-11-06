from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from frigate_timelapse_hls.config import Settings
from frigate_timelapse_hls.hls import LiveHLSPublisher
from frigate_timelapse_hls.library import LibraryPublisher, summarize_playlist
from frigate_timelapse_hls.models import (
    SessionMode,
    SessionSnapshot,
    SessionState,
    SessionStatus,
    SourceClip,
)
from frigate_timelapse_hls.publication import atomic_write
from frigate_timelapse_hls.state import StateStore


@pytest.fixture
def settings(tmp_path: Path, monkeypatch) -> Settings:
    monkeypatch.setattr(Settings, "_config_path", tmp_path / "absent.toml")
    return Settings(
        app={"timezone": "Europe/Helsinki", "ready_buffer_seconds": 0},
        camera={"name": "test camera"},
        paths={
            "recordings_source_root": tmp_path,
            "state_db": tmp_path / "state.sqlite3",
            "output_root": tmp_path / "output",
        },
        frigate={"base_url": "http://frigate:5000", "recordings_root": "/recordings"},
        schedule={"start_time": "08:00", "end_time": "20:00"},
        thumbnail={"offset_seconds": 180},
    )


def test_thumbnail_validation(settings: Settings) -> None:
    for offset in [-1, 43200]:
        with pytest.raises(ValidationError):
            Settings(**(settings.model_dump() | {"thumbnail": {"offset_seconds": offset}}))


def test_thumbnail_environment(monkeypatch, settings: Settings) -> None:
    monkeypatch.setenv("FTHLS_THUMBNAIL_OFFSET_SECONDS", "90")
    data = settings.model_dump()
    data.pop("thumbnail")
    assert Settings(**data).thumbnail.offset_seconds == 90


def test_atomic_write_permissions_and_replacement(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "file.json"
    atomic_write(path, "first")
    atomic_write(path, "second")
    assert path.read_text() == "second"
    assert path.stat().st_mode & 0o777 == 0o644
    assert list(path.parent.iterdir()) == [path]
    with patch.object(Path, "replace", side_effect=OSError("interrupted publication")):
        with pytest.raises(OSError):
            atomic_write(path, "not published")
    assert path.read_text() == "second"
    assert list(path.parent.iterdir()) == [path]


def test_generation_survives_resume_and_changes_on_reset(tmp_path: Path) -> None:
    publisher = LiveHLSPublisher()
    kwargs = {"camera": "cam", "day_label": "2026-11-06", "output_root": tmp_path}
    paths = publisher.prepare_day_session(**kwargs, reset=False)
    generation = (paths.publish_dir / "generation.txt").read_text()
    publisher.prepare_day_session(**kwargs, reset=False)
    assert (paths.publish_dir / "generation.txt").read_text() == generation
    publisher.prepare_day_session(**kwargs, reset=True)
    assert (paths.publish_dir / "generation.txt").read_text() != generation


def test_playlist_requires_published_segments_and_valid_durations(tmp_path: Path) -> None:
    playlist = tmp_path / "live.m3u8"
    playlist.write_text("#EXTM3U\n#EXTINF:2.5,\nsegment-000001.ts\n")
    assert not summarize_playlist(playlist).ready
    (tmp_path / "segment-000001.ts").write_bytes(b"media")
    summary = summarize_playlist(playlist)
    assert summary.ready and summary.duration == 2.5 and not summary.complete
    for duration in ["nan", "inf", "-1"]:
        playlist.write_text(f"#EXTM3U\n#EXTINF:{duration},\nsegment-000001.ts\n")
        assert not summarize_playlist(playlist).ready
    playlist.write_text("#EXTM3U\n#EXTINF:2,\n../outside.ts\n")
    assert not summarize_playlist(playlist).ready


def make_snapshot(settings: Settings, gap: int = 0) -> SessionSnapshot:
    start = datetime(2026, 11, 6, 8, tzinfo=ZoneInfo("Europe/Helsinki"))
    clip_start = start + timedelta(seconds=180 + gap)
    return SessionSnapshot(
        observed_at=start + timedelta(minutes=10),
        day=start.date(),
        session_start=start,
        session_end=start + timedelta(hours=12),
        query_end=start + timedelta(minutes=10),
        clips=(
            SourceClip(
                path=Path("/source.mp4"),
                camera=settings.camera.name,
                start_time=clip_start,
                duration_seconds=10,
                relative_path=Path("source.mp4"),
            ),
        ),
    )


def test_api_poster_gap_timezone_retention_and_failure_isolation(settings: Settings) -> None:
    snapshot = make_snapshot(settings, gap=20)
    paths = LiveHLSPublisher().prepare_day_session(
        camera=settings.camera.name,
        day_label=str(snapshot.day),
        output_root=settings.paths.output_root,
        reset=False,
    )
    publisher = LibraryPublisher(settings, StateStore(settings.paths.state_db))
    publisher.observe(snapshot)
    with patch("frigate_timelapse_hls.library.urlopen", side_effect=TimeoutError):
        publisher.process_poster(snapshot.observed_at)
    assert not (paths.publish_dir / "poster.jpg").exists()
    image = BytesIO(b"\xff\xd8test image\xff\xd9")
    image.headers = {"Content-Type": "image/jpeg"}
    with patch("frigate_timelapse_hls.library.urlopen", return_value=image) as request:
        publisher.process_poster(snapshot.observed_at + timedelta(seconds=31))
        url = request.call_args.args[0]
        assert f"{snapshot.clips[0].start_time.timestamp():.3f}" in url
        assert "/api/test%20camera/recordings/" in url
        assert request.call_args.kwargs["timeout"] == 8
    publisher.observe(snapshot)
    assert publisher._request is None
    manifest = publisher.publish(snapshot.observed_at)
    assert manifest["days"][0]["poster_url"]
    assert not manifest["days"][0]["ready"]  # poster does not imply playable video
    # A later empty scan must not discard a cached poster.
    publisher.observe(
        SessionSnapshot(
            snapshot.observed_at,
            snapshot.day,
            snapshot.session_start,
            snapshot.session_end,
            snapshot.query_end,
            (),
        )
    )
    assert publisher.publish(snapshot.observed_at)["days"][0]["poster_url"]


def test_existing_archive_and_finite_snapshot(settings: Settings) -> None:
    root = settings.paths.output_root / "published" / settings.camera.name / "2026-11-05"
    root.mkdir(parents=True)
    (root / "segment-000000.ts").write_bytes(b"video")
    (root / "live.m3u8").write_text("#EXTM3U\n#EXTINF:2,\nsegment-000000.ts\n")
    library = LibraryPublisher(settings, StateStore(settings.paths.state_db))
    manifest = library.publish(datetime(2026, 11, 6, 12, tzinfo=settings.tzinfo))
    entry = manifest["days"][0]
    assert entry["ready"] and entry["duration"] == 2
    assert not entry["complete"] and entry["schedule_start"] is None
    assert "#EXT-X-ENDLIST" in (root / "index.m3u8").read_text()
    assert entry["poster_url"] is None
    assert library.publish()["days"][0]["generation_id"] == entry["generation_id"]


def test_poster_target_changes_can_return_to_cached_target(settings: Settings) -> None:
    snapshot = make_snapshot(settings)
    LiveHLSPublisher().prepare_day_session(
        camera=settings.camera.name,
        day_label=str(snapshot.day),
        output_root=settings.paths.output_root,
        reset=False,
    )
    publisher = LibraryPublisher(settings, StateStore(settings.paths.state_db))
    publisher.observe(snapshot)
    image = BytesIO(b"\xff\xd8cached image\xff\xd9")
    image.headers = {"Content-Type": "image/jpeg"}
    with patch("frigate_timelapse_hls.library.urlopen", return_value=image):
        publisher.process_poster(snapshot.observed_at)
    assert publisher.publish(snapshot.observed_at)["days"][0]["poster_url"]
    publisher.settings = settings.model_copy(
        update={"thumbnail": settings.thumbnail.model_copy(update={"offset_seconds": 200})}
    )
    publisher.observe(snapshot)
    assert publisher.publish(snapshot.observed_at)["days"][0]["poster_url"] is None
    publisher.settings = settings
    publisher.observe(snapshot)
    assert publisher.publish(snapshot.observed_at)["days"][0]["poster_url"]
    assert publisher._request is None


def test_missing_recordings_and_bounded_retries(settings: Settings) -> None:
    snapshot = make_snapshot(settings)
    paths = LiveHLSPublisher().prepare_day_session(
        camera=settings.camera.name,
        day_label=str(snapshot.day),
        output_root=settings.paths.output_root,
        reset=False,
    )
    publisher = LibraryPublisher(settings, StateStore(settings.paths.state_db))
    empty = SessionSnapshot(
        snapshot.observed_at,
        snapshot.day,
        snapshot.session_start,
        snapshot.session_end,
        snapshot.query_end,
        (),
    )
    publisher.observe(empty)
    assert publisher._request is None
    publisher.observe(snapshot)
    with patch("frigate_timelapse_hls.library.urlopen", side_effect=TimeoutError) as request:
        for i in range(10):
            publisher.process_poster(snapshot.observed_at + timedelta(seconds=i * 31))
            publisher.observe(snapshot)  # repeated scans must not reset the retry budget
    assert request.call_count == 5
    assert not (paths.publish_dir / "poster.jpg").exists()
    # Regeneration gives a new request and never reuses a stale poster identity.
    LiveHLSPublisher().prepare_day_session(
        camera=settings.camera.name,
        day_label=str(snapshot.day),
        output_root=settings.paths.output_root,
        reset=True,
    )
    publisher.observe(snapshot)
    assert publisher._request is not None and publisher._request.attempts == 0


@pytest.mark.parametrize(
    "day,utc_hour",
    [
        (date(2026, 3, 28), 6),
        (date(2026, 3, 29), 5),
        (date(2026, 10, 24), 5),
        (date(2026, 10, 25), 6),
    ],
)
def test_thumbnail_target_tracks_application_dst(
    settings: Settings, day: date, utc_hour: int
) -> None:
    start = settings.window_start_for_day(day)
    target = start + timedelta(seconds=settings.thumbnail.offset_seconds)
    snapshot = SessionSnapshot(
        start + timedelta(minutes=10),
        day,
        start,
        settings.window_end_for_day(day),
        start + timedelta(minutes=10),
        (SourceClip(Path("/clip.mp4"), settings.camera.name, target, 10, Path("clip.mp4")),),
    )
    LiveHLSPublisher().prepare_day_session(
        camera=settings.camera.name,
        day_label=str(day),
        output_root=settings.paths.output_root,
        reset=False,
    )
    publisher = LibraryPublisher(settings, StateStore(settings.paths.state_db))
    publisher.observe(snapshot)
    assert publisher._request is not None
    utc = publisher._request.frame_time.astimezone(ZoneInfo("UTC"))
    assert utc.hour == utc_hour and utc.minute == 3


def test_snapshot_publication_preserves_existing_urls(tmp_path: Path) -> None:
    publisher = LiveHLSPublisher()
    paths = publisher.prepare_day_session(
        camera="cam",
        day_label="2026-11-06",
        output_root=tmp_path,
        reset=False,
    )
    paths.live_playlist_path.write_text("#EXTM3U\n#EXTINF:2,\nsegment.ts\n")
    assert publisher.publish_snapshot_playlist(paths) == paths.playlist_path
    assert paths.playlist_path.read_text().endswith("#EXT-X-ENDLIST\n")
    publisher.publish_latest_pointer(camera="cam", day_label="2026-11-06", output_root=tmp_path)
    assert (
        '"playlist_url": "/published/cam/2026-11-06/index.m3u8"'
        in paths.latest_pointer_path.read_text()
    )


def test_library_reconciles_sqlite_stopping_and_completion(settings: Settings) -> None:
    snapshot = make_snapshot(settings)
    paths = LiveHLSPublisher().prepare_day_session(
        camera=settings.camera.name,
        day_label=str(snapshot.day),
        output_root=settings.paths.output_root,
        reset=False,
    )
    (paths.publish_dir / "segment.ts").write_bytes(b"video")
    paths.live_playlist_path.write_text("#EXTM3U\n#EXTINF:2,\nsegment.ts\n#EXT-X-ENDLIST\n")
    store = StateStore(settings.paths.state_db)
    state = SessionState(
        camera=settings.camera.name,
        day=snapshot.day,
        session_start=snapshot.session_start,
        session_end=snapshot.session_end,
        status=SessionStatus.STOPPED,
        mode=SessionMode.LIVE_FOLLOW,
        playlist_path=paths.playlist_path,
        segment_dir=paths.segment_dir,
        last_discovered_clip_id=None,
        last_ingested_clip_id=None,
        discovered_clip_count=1,
        ingested_clip_count=1,
        ffmpeg_pid=None,
        updated_at=snapshot.observed_at,
    )
    store.upsert_day_session(state)
    publisher = LibraryPublisher(settings, store)
    entry = publisher.publish(snapshot.observed_at)["days"][0]
    assert entry["ready"] and entry["stream_ended"] and not entry["complete"]
    assert entry["recordings_available"] and entry["status"] == "stopped"
    store.upsert_day_session(
        replace(state, status=SessionStatus.COMPLETED, mode=SessionMode.COMPLETED)
    )
    completed = publisher.publish(snapshot.observed_at)["days"][0]
    assert completed["complete"] and completed["generation_id"] == entry["generation_id"]
