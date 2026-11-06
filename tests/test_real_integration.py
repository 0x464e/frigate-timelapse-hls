"""Opt-in, read-only Frigate/NAS checks with strictly temporary generated media."""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from time import monotonic

import pytest

from frigate_timelapse_hls.config import PathsConfig, load_settings
from frigate_timelapse_hls.hls import LiveHLSPublisher
from frigate_timelapse_hls.library import LibraryPublisher
from frigate_timelapse_hls.models import SessionSnapshot
from frigate_timelapse_hls.scanner import FrigateVodScanner
from frigate_timelapse_hls.state import StateStore
from frigate_timelapse_hls.worker import ContinuousFFmpegCommandBuilder, WorkerSessionPlan


@pytest.mark.skipif(os.getenv("FTHLS_REAL_INTEGRATION") != "1", reason="Explicit opt-in required")
def test_real_frigate_poster_and_short_recording(tmp_path: Path) -> None:
    settings = load_settings()
    now = datetime.now(settings.tzinfo)
    start = settings.window_start_for_day(now.date())
    source_start = start + timedelta(minutes=3)
    deadline = monotonic() + 90
    scanner = FrigateVodScanner(
        camera=settings.camera.name,
        recordings_source_root=settings.paths.recordings_source_root,
        frigate=settings.frigate,
        tzinfo=settings.tzinfo,
    )
    clips = scanner.scan_range(
        start_time=source_start, end_time=source_start + timedelta(seconds=30)
    )
    assert clips, "No recordings in today's 30-second test window (schedule start + 3 minutes)"
    settings = settings.model_copy(
        update={
            "paths": PathsConfig(
                recordings_source_root=settings.paths.recordings_source_root,
                state_db=tmp_path / "state.sqlite3",
                output_root=tmp_path / "output",
            ),
            "thumbnail": settings.thumbnail.model_copy(update={"offset_seconds": 180}),
        }
    )
    publisher = LiveHLSPublisher()
    paths = publisher.prepare_day_session(
        camera=settings.camera.name,
        day_label=str(now.date()),
        output_root=settings.paths.output_root,
        reset=False,
    )
    library = LibraryPublisher(settings, StateStore(settings.paths.state_db))
    library.observe(
        SessionSnapshot(
            now,
            now.date(),
            start,
            settings.window_end_for_day(now.date()),
            source_start + timedelta(seconds=30),
            tuple(clips),
        )
    )
    library.process_poster()
    assert (paths.publish_dir / "poster.jpg").is_file(), "Frigate recording-snapshot GET failed"
    payloads: list[bytes] = []
    source_seconds = 0.0
    for clip in clips:
        duration = min(clip.duration_seconds, 30 - source_seconds)
        if duration <= 0:
            break
        result = subprocess.run(
            [
                settings.ffmpeg.binary,
                "-hide_banner",
                "-loglevel",
                "error",
                "-nostdin",
                "-ss",
                str(clip.trim_start_seconds),
                "-i",
                str(clip.path),
                "-t",
                str(duration),
                "-map",
                "0:v:0",
                "-an",
                "-c:v",
                "copy",
                "-f",
                "mpegts",
                "pipe:1",
            ],
            check=True,
            capture_output=True,
            timeout=max(0.1, min(15, deadline - monotonic())),
        )
        payloads.append(result.stdout)
        source_seconds += duration
    assert 0 < source_seconds <= 30
    command = ContinuousFFmpegCommandBuilder(settings.ffmpeg, settings.timelapse).build(
        WorkerSessionPlan(settings.camera.name, str(now.date()), paths)
    )
    result = subprocess.run(
        command,
        input=b"".join(payloads),
        cwd=paths.publish_dir,
        capture_output=True,
        timeout=max(0.1, min(60, deadline - monotonic())),
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    publisher.publish_snapshot_playlist(paths)
    entry = library.publish()["days"][0]
    assert entry["ready"] and entry["poster_url"]
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-count_frames",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_name,width,height,nb_read_frames",
            "-of",
            "json",
            str(paths.playlist_path),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=max(0.1, min(15, deadline - monotonic())),
    )
    assert int(json.loads(probe.stdout)["streams"][0]["nb_read_frames"]) > 0
