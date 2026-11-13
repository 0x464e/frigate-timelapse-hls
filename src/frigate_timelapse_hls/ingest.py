from __future__ import annotations

import logging
import shlex
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from .config import FFmpegConfig
from .models import SourceClip
from .source_time import REMUX_OFFSET_SECONDS, TIMESCALE, packet_times
from .worker import ContinuousFFmpegWorker

logger = logging.getLogger(__name__)


class ContinuousClipIngestor:
    def __init__(self, ffmpeg_config: FFmpegConfig) -> None:
        self._ffmpeg_config = ffmpeg_config

    def ingest_clip(self, clip: SourceClip, worker: ContinuousFFmpegWorker) -> None:
        if worker.source_time is not None:
            self._ingest_timestamped(clip, worker)
            return
        command = [
            self._ffmpeg_config.binary,
            "-loglevel",
            "error",
            "-nostdin",
        ]
        if clip.trim_start_seconds > 0:
            command.extend(["-ss", f"{clip.trim_start_seconds:.3f}"])
        command.extend(
            [
                "-i",
                str(clip.path),
                "-t",
                f"{clip.duration_seconds:.3f}",
                "-map",
                "0:v:0",
                "-an",
                "-c:v",
                "copy",
                "-f",
                "mpegts",
                "pipe:1",
            ]
        )
        logger.debug("Injecting clip %s", clip.relative_path.as_posix())
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        if process.stdout is None:
            raise RuntimeError("Failed to open clip ingest stdout pipe")
        try:
            while True:
                chunk = process.stdout.read(1024 * 64)
                if not chunk:
                    break
                worker.write(chunk)
        finally:
            process.stdout.close()
        stderr_bytes = b""
        if process.stderr is not None:
            stderr_bytes = process.stderr.read()
            process.stderr.close()
        return_code = process.wait()
        if return_code != 0:
            stderr_text = stderr_bytes.decode("utf-8", errors="replace").strip()
            raise RuntimeError(stderr_text or "Clip ingest remux failed")

    def _ingest_timestamped(self, clip: SourceClip, worker: ContinuousFFmpegWorker) -> None:
        mapping = worker.source_time
        assert mapping is not None
        ffmpeg = [self._ffmpeg_config.binary, "-loglevel", "error", "-nostdin", "-copyts"]
        remux = ["-map", "0:v:0", "-an", "-c:v", "copy", "-mpegts_copyts", "1", "-muxdelay", "0"]
        with TemporaryDirectory(prefix="fthls-remux-") as directory:
            path = Path(directory) / "clip.ts"
            # Preserve pre-roll retained by stream-copy seeking. The positive
            # offset keeps DTS before the first displayed frame representable.
            subprocess.run(
                [
                    *ffmpeg,
                    "-start_at_zero",
                    "-ss",
                    f"{clip.trim_start_seconds:.6f}",
                    "-i",
                    str(clip.path),
                    "-t",
                    f"{clip.trim_start_seconds + clip.duration_seconds:.6f}",
                    *remux,
                    "-output_ts_offset",
                    str(REMUX_OFFSET_SECONDS),
                    "-f",
                    "mpegts",
                    str(path),
                ],
                check=True,
                timeout=30,
            )
            packets = packet_times(mapping.probe, path)
            if not packets:
                raise RuntimeError(f"No video packets in {clip.relative_path}")
            source_start = clip.start_time.timestamp() - clip.trim_start_seconds
            offset = mapping.register(packets, source_start * 1000)
            command = [
                *ffmpeg,
                "-i",
                str(path),
                *remux,
                "-avoid_negative_ts",
                "disabled",
                "-output_ts_offset",
                f"{offset / TIMESCALE:.9f}",
                "-f",
                "mpegts",
                "pipe:1",
            ]
            logger.debug(
                "Injecting timestamped clip %s: %s", clip.relative_path, shlex.join(command)
            )
            payload = subprocess.run(command, stdout=subprocess.PIPE, check=True, timeout=30).stdout
            worker.write(payload)
