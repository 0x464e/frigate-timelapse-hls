from __future__ import annotations

import logging
import subprocess

from .config import FFmpegConfig
from .models import SourceClip
from .worker import ContinuousFFmpegWorker

logger = logging.getLogger(__name__)


class ContinuousClipIngestor:
    def __init__(self, ffmpeg_config: FFmpegConfig) -> None:
        self._ffmpeg_config = ffmpeg_config

    def ingest_clip(self, clip: SourceClip, worker: ContinuousFFmpegWorker) -> None:
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
