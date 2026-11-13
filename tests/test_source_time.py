from __future__ import annotations

import json
import shutil
import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from frigate_timelapse_hls.config import FFmpegConfig, TimelapseConfig
from frigate_timelapse_hls.hls import LiveHLSPublisher
from frigate_timelapse_hls.ingest import ContinuousClipIngestor
from frigate_timelapse_hls.models import SourceClip
from frigate_timelapse_hls.source_time import MAP_DIRECTORY, packet_times, playlist_segments
from frigate_timelapse_hls.worker import (
    ContinuousFFmpegCommandBuilder,
    ContinuousFFmpegWorker,
    WorkerSessionPlan,
)


def run_ffmpeg(*args: str) -> bytes:
    return subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", *args],
        capture_output=True,
        check=True,
        timeout=20,
    ).stdout


@pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg tools"
)
@pytest.mark.parametrize(("speed", "fps"), [(288, 60), (32, 24), (2, 48), (0.5, 60)])
def test_mapping_survives_gap_trim_restart_and_render_settings(
    tmp_path: Path,
    speed: float,
    fps: int,
) -> None:
    # The red channel encodes the source frame number. With the lossless
    # encoder it supplies a reference independent of the timestamp report.
    originals = []
    for i in range(4):
        path = tmp_path / f"clip{i}.mp4"
        run_ffmpeg(
            "-f",
            "lavfi",
            "-i",
            f"nullsrc=s=32x32:r=20:d=10,format=gbrp,geq=r='{50 * i}+floor(N/4)':g=0:b=0",
            "-c:v",
            "libx264rgb",
            "-crf",
            "0",
            "-g",
            "20",
            "-threads",
            "1",
            str(path),
        )
        originals.append(path)
    publisher = LiveHLSPublisher()
    paths = publisher.prepare_day_session(
        camera="terrarium",
        day_label="2026-11-13",
        output_root=tmp_path,
        reset=False,
    )
    config = FFmpegConfig(
        custom_output_args=("-c:v", "libx264rgb", "-crf", "0", "-threads", "1", "-g", str(fps)),
    )
    builder = ContinuousFFmpegCommandBuilder(
        config,
        TimelapseConfig(timelapse_speed=speed, output_fps=fps, hls_segment_seconds=1),
    )
    worker = ContinuousFFmpegWorker(builder, track_source_time=True)
    ingestor = ContinuousClipIngestor(config)
    origin = datetime(2026, 11, 13, 6, tzinfo=UTC)
    old_segments = {}
    for i, path in enumerate(originals):
        if i in (0, 2):
            publisher.prepare_live_playlist_for_resume(paths)
            worker.start(WorkerSessionPlan("terrarium", "2026-11-13", paths))
        trim = 1.37 if i == 0 else 0
        ingestor.ingest_clip(
            SourceClip(
                path=path,
                relative_path=Path(path.name),
                camera="terrarium",
                start_time=origin + timedelta(seconds=(0, 110, 220, 230)[i] + trim),
                trim_start_seconds=trim,
                duration_seconds=10 - trim,
            ),
            worker,
        )
        if i == 1 and speed == 0.5:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                worker.publish_source_times(paths)
                if (paths.publish_dir / MAP_DIRECTORY / "segment-000000.ts.json").is_file():
                    break
                time.sleep(0.05)
            else:
                pytest.fail("Expected source times for a segment before encoder shutdown")
        if i in (1, 3):
            worker.stop()
            mapping = {
                path.name.removesuffix(".json"): json.loads(path.read_text())["frames"]
                for path in (paths.publish_dir / MAP_DIRECTORY).glob("*.json")
            }
            assert all(mapping[name] == old for name, old in old_segments.items())
            old_segments = mapping
    names = playlist_segments(paths.live_playlist_path)
    assert set(names) == set(old_segments)
    for name in names:
        path = paths.publish_dir / name
        pixels = run_ffmpeg("-i", str(path), "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1")
        red = pixels[:: 32 * 32 * 3]
        packets = sorted(packet_times("ffprobe", path))
        frames = old_segments[name]
        assert len(frames) == len(packets) == len(red)
        for (tick, timestamp), (pts, _, _), channel in zip(frames, packets, red, strict=True):
            clip, offset = divmod(channel, 50)
            expected = origin.timestamp() + (0, 110, 220, 230)[clip] + offset / 5
            assert tick == pts - packets[0][0]
            assert expected - 0.001 <= timestamp / 1000 < expected + 0.201
