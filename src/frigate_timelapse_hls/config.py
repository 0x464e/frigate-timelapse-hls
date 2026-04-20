from __future__ import annotations

import os
import shlex
from datetime import date, datetime, time
from pathlib import Path
from typing import Annotated, Any, ClassVar
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    NoDecode,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

CONFIG_PATH_ENV = "FTHLS_CONFIG_PATH"
DEFAULT_CONFIG_PATH = Path("config.toml")


def _resolve_path(value: str | Path, base_dir: Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return (base_dir / path).resolve()


class _TomlConfigSettingsSource(TomlConfigSettingsSource):
    def __init__(self, settings_cls: type[BaseSettings], config_path: Path) -> None:
        self._config_path = config_path
        super().__init__(
            settings_cls,
            toml_file=config_path if config_path.exists() else None,
        )

    def __call__(self) -> dict[str, Any]:
        data = super().__call__()
        paths = data.get("paths")
        if isinstance(paths, dict):
            base_dir = self._config_path.parent
            for key in ("recordings_source_root", "state_db", "output_root"):
                value = paths.get(key)
                if isinstance(value, str):
                    paths[key] = str(_resolve_path(value, base_dir))
        return data


class ConfigModel(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class AppConfig(ConfigModel):
    timezone: str
    ready_buffer_seconds: int = 30
    log_level: str = "INFO"


class CameraConfig(ConfigModel):
    name: str


class PathsConfig(ConfigModel):
    recordings_source_root: Path
    state_db: Path
    output_root: Path

    @field_validator("recordings_source_root", "state_db", "output_root", mode="before")
    @classmethod
    def _resolve_env_path(cls, value: Any) -> Any:
        if isinstance(value, str | Path):
            return _resolve_path(value, Path.cwd())
        return value


class FrigateConfig(ConfigModel):
    base_url: str
    recordings_root: str

    @field_validator("base_url", "recordings_root")
    @classmethod
    def _strip_trailing_slash(cls, value: str) -> str:
        return value.rstrip("/")


class TimelapseConfig(ConfigModel):
    timelapse_speed: float = 288.0
    output_fps: int = 30
    hls_segment_seconds: int = 2


class ScheduleConfig(ConfigModel):
    start_time: time = time(0, 0)
    end_time: time = time(23, 59, 59)


class FFmpegConfig(ConfigModel):
    binary: str = "ffmpeg"
    video_codec: str = "libx264"
    preset: str | None = None
    profile: str | None = None
    custom_input_args: Annotated[tuple[str, ...], NoDecode] = ()
    custom_output_args: Annotated[tuple[str, ...], NoDecode] = ()

    @field_validator("custom_input_args", "custom_output_args", mode="before")
    @classmethod
    def _parse_shell_args(cls, value: Any) -> tuple[str, ...] | Any:
        if value is None:
            return ()
        if isinstance(value, str):
            return tuple(shlex.split(value))
        return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="FTHLS_",
        env_nested_delimiter="_",
        env_nested_max_split=1,
        case_sensitive=False,
        extra="ignore",
    )

    _config_path: ClassVar[Path] = DEFAULT_CONFIG_PATH

    app: AppConfig
    camera: CameraConfig
    paths: PathsConfig
    frigate: FrigateConfig
    timelapse: TimelapseConfig = TimelapseConfig()
    schedule: ScheduleConfig = ScheduleConfig()
    ffmpeg: FFmpegConfig = FFmpegConfig()

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            env_settings,
            _TomlConfigSettingsSource(settings_cls, cls._config_path),
        )

    @model_validator(mode="after")
    def _validate_schedule(self) -> Settings:
        if self.schedule.end_time <= self.schedule.start_time:
            raise ValueError("schedule.end_time must be later than schedule.start_time")
        return self

    @property
    def tzinfo(self) -> ZoneInfo:
        return ZoneInfo(self.app.timezone)

    def window_start_for_day(self, day: date) -> datetime:
        return datetime.combine(day, self.schedule.start_time, tzinfo=self.tzinfo)

    def window_end_for_day(self, day: date) -> datetime:
        return datetime.combine(day, self.schedule.end_time, tzinfo=self.tzinfo)


def _get_config_path() -> Path:
    raw_path = os.getenv(CONFIG_PATH_ENV)
    if raw_path:
        return Path(raw_path).expanduser().resolve()
    return DEFAULT_CONFIG_PATH.resolve()


def load_settings() -> Settings:
    Settings._config_path = _get_config_path()
    return Settings()  # type: ignore[call-arg]
