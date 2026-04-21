from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from threading import Thread

from .config import FFmpegConfig, TimelapseConfig
from .hls import LiveSessionPaths

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class WorkerSessionPlan:
    camera: str
    day_label: str
    paths: LiveSessionPaths


class ContinuousFFmpegCommandBuilder:
    def __init__(self, config: FFmpegConfig, timelapse: TimelapseConfig) -> None:
        self._config = config
        self._timelapse = timelapse

    def build(self, plan: WorkerSessionPlan) -> list[str]:
        speedup_filter = f"setpts=PTS/{self._timelapse.timelapse_speed}"
        fps_filter = f"fps={self._timelapse.output_fps}"
        command = [
            self._config.binary,
            "-progress",
            "pipe:1",
            "-nostats",
            "-loglevel",
            "error",
            "-nostdin",
            "-y",
            *self._config.custom_input_args,
            "-fflags",
            "+genpts",
            "-f",
            "mpegts",
            "-i",
            "pipe:0",
            "-an",
        ]
        if self._config.custom_output_args:
            command.extend(
                self._resolve_custom_output_args(
                    speedup_filter=speedup_filter,
                    fps_filter=fps_filter,
                )
            )
        else:
            command.extend(
                [
                    "-vf",
                    f"{speedup_filter},{fps_filter}",
                    "-c:v",
                    self._config.video_codec,
                ]
            )
            if self._config.preset:
                command.extend(["-preset", self._config.preset])
            if self._config.profile:
                command.extend(["-profile:v", self._config.profile])
        command.extend(
            [
                "-f",
                "hls",
                "-hls_time",
                str(self._timelapse.hls_segment_seconds),
                "-hls_list_size",
                "0",
                "-hls_playlist_type",
                "event",
                "-hls_flags",
                "append_list+independent_segments+temp_file",
                "-hls_segment_filename",
                "segment-%06d.ts",
                "live.m3u8",
            ]
        )
        return command

    def _resolve_custom_output_args(
        self,
        *,
        speedup_filter: str,
        fps_filter: str,
    ) -> list[str]:
        has_speedup_filter_placeholder = any(
            "{speedup_filter}" in arg for arg in self._config.custom_output_args
        )
        has_fps_filter_placeholder = any(
            "{fps_filter}" in arg for arg in self._config.custom_output_args
        )
        has_vf_arg = "-vf" in self._config.custom_output_args
        resolved = [
            arg.replace("{speedup_filter}", speedup_filter).replace(
                "{fps_filter}", fps_filter
            )
            for arg in self._config.custom_output_args
        ]
        if not has_vf_arg and not (
            has_speedup_filter_placeholder or has_fps_filter_placeholder
        ):
            resolved = ["-vf", f"{speedup_filter},{fps_filter}", *resolved]
        return resolved


class ContinuousFFmpegWorker:
    def __init__(self, builder: ContinuousFFmpegCommandBuilder) -> None:
        self._builder = builder
        self._process: subprocess.Popen[bytes] | None = None
        self._stdout_thread: Thread | None = None
        self._stderr_thread: Thread | None = None
        self._last_progress_line: str | None = None

    @property
    def pid(self) -> int | None:
        return None if self._process is None else self._process.pid

    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def start(self, plan: WorkerSessionPlan) -> None:
        if self.is_running():
            return
        command = self._builder.build(plan)
        logger.info("Starting continuous FFmpeg worker for %s", plan.day_label)
        self._process = subprocess.Popen(
            command,
            cwd=plan.paths.publish_dir,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        self._stdout_thread = Thread(
            target=self._drain_stdout,
            name="ffmpeg-progress-drain",
            daemon=True,
        )
        self._stderr_thread = Thread(
            target=self._drain_stderr,
            name="ffmpeg-stderr-drain",
            daemon=True,
        )
        self._stdout_thread.start()
        self._stderr_thread.start()

    def write(self, payload: bytes) -> None:
        if not self.is_running() or self._process is None or self._process.stdin is None:
            raise RuntimeError("Continuous FFmpeg worker is not running")
        self._process.stdin.write(payload)
        self._process.stdin.flush()

    def stop(self) -> None:
        if self._process is None:
            return
        if self._process.stdin is not None and not self._process.stdin.closed:
            self._process.stdin.close()
        self._process.wait(timeout=30)
        self._process = None

    def terminate(self) -> None:
        if self._process is None:
            return
        self._process.terminate()
        self._process.wait(timeout=10)
        self._process = None

    def _drain_stdout(self) -> None:
        if self._process is None or self._process.stdout is None:
            return
        for raw_line in self._process.stdout:
            try:
                line = raw_line.decode("utf-8", errors="replace").strip()
            except AttributeError:
                line = str(raw_line).strip()
            if line:
                self._last_progress_line = line

    def _drain_stderr(self) -> None:
        if self._process is None or self._process.stderr is None:
            return
        for raw_line in self._process.stderr:
            try:
                line = raw_line.decode("utf-8", errors="replace").strip()
            except AttributeError:
                line = str(raw_line).strip()
            if line:
                logger.error("ffmpeg: %s", line)
