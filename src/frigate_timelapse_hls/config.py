from __future__ import annotations

import os
import shlex
import tomllib
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from typing import cast
from zoneinfo import ZoneInfo

type TomlScalar = str | int | float | bool
type TomlValue = TomlScalar | TomlTable | list[TomlValue]
type TomlTable = dict[str, TomlValue]


@dataclass(slots=True, frozen=True)
class AppConfig:
    timezone: str
    ready_buffer_seconds: int
    log_level: str


@dataclass(slots=True, frozen=True)
class CameraConfig:
    name: str


@dataclass(slots=True, frozen=True)
class PathsConfig:
    recordings_source_root: Path
    state_db: Path
    output_root: Path


@dataclass(slots=True, frozen=True)
class FrigateConfig:
    base_url: str
    recordings_root: str


@dataclass(slots=True, frozen=True)
class TimelapseConfig:
    timelapse_speed: float
    output_fps: int
    hls_segment_seconds: int


@dataclass(slots=True, frozen=True)
class ScheduleConfig:
    start_time: time
    end_time: time


@dataclass(slots=True, frozen=True)
class FFmpegConfig:
    binary: str
    video_codec: str
    preset: str | None
    profile: str | None
    custom_input_args: tuple[str, ...]
    custom_output_args: tuple[str, ...]


@dataclass(slots=True, frozen=True)
class Settings:
    app: AppConfig
    camera: CameraConfig
    paths: PathsConfig
    frigate: FrigateConfig
    timelapse: TimelapseConfig
    schedule: ScheduleConfig
    ffmpeg: FFmpegConfig

    @property
    def tzinfo(self) -> ZoneInfo:
        return ZoneInfo(self.app.timezone)

    def window_start_for_day(self, day: date) -> datetime:
        return datetime.combine(day, self.schedule.start_time, tzinfo=self.tzinfo)

    def window_end_for_day(self, day: date) -> datetime:
        return datetime.combine(day, self.schedule.end_time, tzinfo=self.tzinfo)


def _resolve_path(value: str, config_path: Path) -> Path:
    path = Path(value)

    if path.is_absolute():
        return path
    return (config_path.parent / path).resolve()


def _get_table(data: TomlTable, key: str) -> TomlTable:
    value = data.get(key)
    if isinstance(value, dict):
        return cast(TomlTable, dict(value))
    return {}


def _get_required_value(data: TomlTable, key: str) -> TomlValue:
    return data[key]


def _get_required_str(data: TomlTable, key: str) -> str:
    return str(_get_required_value(data, key))


def _get_time(data: TomlTable, key: str, default: str) -> time:
    value = str(data.get(key, default))
    return time.fromisoformat(value)


def _get_int(data: TomlTable, key: str, default: int) -> int:
    value = data.get(key, default)
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float, str)):
        return int(value)
    raise TypeError(f"Expected scalar value for {key!r}, got {type(value).__name__}")


def _get_float(data: TomlTable, key: str, default: float) -> float:
    value = data.get(key, default)
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float, str)):
        return float(value)
    raise TypeError(f"Expected scalar value for {key!r}, got {type(value).__name__}")


def _get_bool(data: TomlTable, key: str, default: bool) -> bool:
    value = data.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float, str)):
        return bool(value)
    raise TypeError(f"Expected scalar value for {key!r}, got {type(value).__name__}")


def _get_optional_str(data: TomlTable, key: str) -> str | None:
    value = data.get(key)
    if value is None:
        return None
    return str(value)


def _get_shell_args(data: TomlTable, key: str) -> tuple[str, ...]:
    value = data.get(key)
    if value is None:
        return ()
    if not isinstance(value, (str, int, float, bool)):
        raise TypeError(f"Expected scalar value for {key!r}, got {type(value).__name__}")
    return tuple(shlex.split(str(value)))


def _apply_env_overrides(data: TomlTable, config_path: Path) -> TomlTable:
    output = dict(data)
    env_recordings_source_root = os.getenv("FRIGATE_TIMELAPSE_RECORDINGS_SOURCE_ROOT")
    if env_recordings_source_root:
        paths = _get_table(output, "paths")
        paths["recordings_source_root"] = str(
            _resolve_path(env_recordings_source_root, config_path)
        )
        output["paths"] = paths
    env_camera = os.getenv("FRIGATE_TIMELAPSE_CAMERA")
    if env_camera:
        camera = _get_table(output, "camera")
        camera["name"] = env_camera
        output["camera"] = camera
    return output


def load_settings(config_path: Path) -> Settings:
    raw = cast(TomlTable, tomllib.loads(config_path.read_text(encoding="utf-8")))
    raw = _apply_env_overrides(raw, config_path)
    app = _get_table(raw, "app")
    camera = _get_table(raw, "camera")
    paths = _get_table(raw, "paths")
    frigate = _get_table(raw, "frigate")
    timelapse = _get_table(raw, "timelapse")
    if not timelapse:
        timelapse = _get_table(raw, "batch")
    schedule = _get_table(raw, "schedule")
    ffmpeg = _get_table(raw, "ffmpeg")
    schedule_config = ScheduleConfig(
        start_time=_get_time(schedule, "start_time", "00:00"),
        end_time=_get_time(schedule, "end_time", "23:59:59"),
    )
    if schedule_config.end_time <= schedule_config.start_time:
        raise ValueError("schedule.end_time must be later than schedule.start_time")

    return Settings(
        app=AppConfig(
            timezone=_get_required_str(app, "timezone"),
            ready_buffer_seconds=_get_int(app, "ready_buffer_seconds", 30),
            log_level=str(app.get("log_level", "INFO")),
        ),
        camera=CameraConfig(name=_get_required_str(camera, "name")),
        paths=PathsConfig(
            recordings_source_root=_resolve_path(
                _get_required_str(paths, "recordings_source_root"), config_path
            ),
            state_db=_resolve_path(_get_required_str(paths, "state_db"), config_path),
            output_root=_resolve_path(_get_required_str(paths, "output_root"), config_path),
        ),
        frigate=FrigateConfig(
            base_url=_get_required_str(frigate, "base_url").rstrip("/"),
            recordings_root=_get_required_str(frigate, "recordings_root").rstrip("/"),
        ),
        timelapse=TimelapseConfig(
            timelapse_speed=_get_float(timelapse, "timelapse_speed", 288.0),
            output_fps=_get_int(timelapse, "output_fps", 30),
            hls_segment_seconds=_get_int(timelapse, "hls_segment_seconds", 2),
        ),
        schedule=schedule_config,
        ffmpeg=FFmpegConfig(
            binary=str(ffmpeg.get("binary", "ffmpeg")),
            video_codec=str(ffmpeg.get("video_codec", "libx264")),
            preset=_get_optional_str(ffmpeg, "preset"),
            profile=_get_optional_str(ffmpeg, "profile"),
            custom_input_args=_get_shell_args(ffmpeg, "custom_input_args"),
            custom_output_args=_get_shell_args(ffmpeg, "custom_output_args"),
        ),
    )
