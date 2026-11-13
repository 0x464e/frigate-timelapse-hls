"""Publish source timestamps for the video packets that made it into HLS."""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
from bisect import bisect_right
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from tempfile import TemporaryDirectory

from .hls import LiveSessionPaths
from .publication import atomic_json

logger = logging.getLogger(__name__)
TIMESCALE = 90_000
REMUX_OFFSET_SECONDS = 100
SEGMENT_NAME = re.compile(r"segment-\d{6,}\.ts")
MAP_DIRECTORY = "source-times"


def packet_times(probe: str, path: Path) -> list[tuple[int, int, int]]:
    """Read video packet PTS, DTS and duration in MPEG-TS ticks."""
    result = subprocess.run(
        [
            probe,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_packets",
            "-show_entries",
            "packet=pts,dts,duration",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        check=True,
        timeout=15,
    )
    return [
        (int(packet["pts"]), int(packet["dts"]), int(packet.get("duration", 0)))
        for packet in json.loads(result.stdout)["packets"]
    ]


def playlist_segments(path: Path) -> list[str]:
    try:
        return [line for line in path.read_text().splitlines() if SEGMENT_NAME.fullmatch(line)]
    except FileNotFoundError:
        return []


@dataclass(frozen=True)
class InputSpan:
    start: int
    end: int
    source_start_ms: float


class SourceTimeMap:
    """One encoder run. Write a map for each fully published segment."""

    def __init__(self, paths: LiveSessionPaths, probe: str) -> None:
        self.paths = paths
        self.probe = probe
        self.generation = (paths.publish_dir / "generation.txt").read_text().strip()
        self.previous = set(playlist_segments(paths.live_playlist_path))
        self.scratch = TemporaryDirectory(prefix="fthls-source-time-")
        self.stats_path = Path(self.scratch.name) / "mux.txt"
        self.spans: list[InputSpan] = []
        self.next_dts = 0
        self.stats_offset = 0
        self.pending: list[tuple[int, int, int | None]] = []
        self.processed: set[str] = set()
        self.disabled = False
        self.last_playlist: tuple[int, int] | None = None

    def register(self, packets: list[tuple[int, int, int]], source_start_ms: float) -> int:
        first_dts = packets[0][1]
        first_pts = min(pts for pts, _, _ in packets)
        last_pts = max(pts for pts, _, _ in packets)
        min_pts = self.spans[-1].end + 1 if self.spans else 0
        offset = max(self.next_dts - first_dts, min_pts - first_pts)
        self.spans.append(
            InputSpan(
                first_pts + offset,
                last_pts + offset,
                source_start_ms + (first_pts / TIMESCALE - REMUX_OFFSET_SECONDS) * 1000,
            )
        )
        self.next_dts = max(dts + max(1, duration) for _, dts, duration in packets) + offset
        return offset

    def _read_stats(self, count: int) -> None:
        if len(self.pending) >= count:
            return
        starts = [span.start for span in self.spans]
        with self.stats_path.open("rb") as stream:
            stream.seek(self.stats_offset)
            while len(self.pending) < count:
                line = stream.readline()
                if not line.endswith(b"\n"):
                    break
                self.stats_offset = stream.tell()
                ptsi, tbi, pts, dts, tb = line.decode("ascii").split()
                input_scale = Fraction(tbi) * TIMESCALE
                scale = Fraction(tb) * TIMESCALE
                source = None
                if input_scale and int(ptsi) != 2**63 - 1:
                    input_tick = round(int(ptsi) * input_scale)
                    index = bisect_right(starts, input_tick) - 1
                    if index >= 0:
                        span = self.spans[index]
                        if input_tick <= span.end:
                            source = round(span.source_start_ms + (input_tick - span.start) / 90)
                self.pending.append((round(int(pts) * scale), round(int(dts) * scale), source))

    def publish(self) -> None:
        if self.disabled or not self.stats_path.is_file():
            return
        try:
            modified = self.paths.live_playlist_path.stat()
            revision = (modified.st_ino, modified.st_mtime_ns)
            if revision == self.last_playlist:
                return
            if (self.paths.publish_dir / "generation.txt").read_text().strip() != self.generation:
                self.disabled = True
                return
            for name in playlist_segments(self.paths.live_playlist_path):
                if name in self.previous or name in self.processed:
                    continue
                packets = packet_times(self.probe, self.paths.publish_dir / name)
                if not packets:
                    break
                self._read_stats(len(packets))
                if len(self.pending) < len(packets):
                    break
                stats = self.pending[: len(packets)]
                # Validate correspondence in mux order. The TS muxer may shift
                # every timestamp by a constant. B-frames still use PTS for lookup.
                shift = packets[0][0] - stats[0][0]
                if any(
                    abs(pts - raw_pts - shift) > 1 or abs(dts - raw_dts - shift) > 1
                    for (pts, dts, _), (raw_pts, raw_dts, _) in zip(packets, stats, strict=True)
                ):
                    raise ValueError(f"FFmpeg timestamp mismatch for {name}")
                first_pts = min(pts for pts, _, _ in packets)
                frames = sorted(
                    [pts - first_pts, source]
                    for (pts, _, _), (_, _, source) in zip(packets, stats, strict=True)
                    if source is not None
                )
                if frames:
                    atomic_json(
                        self.paths.publish_dir / MAP_DIRECTORY / f"{name}.json",
                        {
                            "version": 1,
                            "generation_id": self.generation,
                            "segment": name,
                            "frames": frames,
                        },
                    )
                self.pending = self.pending[len(packets) :]
                self.processed.add(name)
            else:
                self.last_playlist = revision
        except FileNotFoundError:
            return
        except OSError, ValueError, KeyError, IndexError, subprocess.SubprocessError:
            self.disabled = True
            logger.exception("Source timestamp publication failed for this encoder run")

    def close(self) -> None:
        self.scratch.cleanup()


def ffprobe_path(ffmpeg: str) -> str | None:
    """Prefer the probe shipped alongside a configured FFmpeg executable."""
    executable = shutil.which(ffmpeg)
    if executable and (probe := shutil.which(str(Path(executable).with_name("ffprobe")))):
        return probe
    return shutil.which("ffprobe")
