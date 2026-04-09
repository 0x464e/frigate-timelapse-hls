from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta, tzinfo
from pathlib import Path
from urllib.parse import quote
from urllib.request import urlopen

from .config import FrigateConfig
from .models import SourceClip

logger = logging.getLogger(__name__)

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | JsonObject | list[JsonValue]
type JsonObject = dict[str, JsonValue]


class FrigateVodScanner:
    def __init__(
        self,
        *,
        camera: str,
        source_root: Path,
        frigate: FrigateConfig,
        tzinfo: tzinfo,
    ) -> None:
        self._camera = camera
        self._source_root = source_root
        self._frigate = frigate
        self._tzinfo = tzinfo

    def scan_range(self, *, start_time: datetime, end_time: datetime) -> list[SourceClip]:
        payload = self._fetch_vod_payload(
            camera=self._camera,
            start_ts=int(start_time.timestamp()),
            end_ts=int(end_time.timestamp()),
        )
        return self._parse_payload(payload)

    def _fetch_vod_payload(self, *, camera: str, start_ts: int, end_ts: int) -> JsonObject:
        camera_path = quote(camera, safe="")
        url = (
            f"{self._frigate.base_url}/api/vod/{camera_path}/start/{start_ts}/end/{end_ts}"
        )
        logger.debug("Fetching Frigate VOD metadata from %s", url)
        with urlopen(url) as response:
            payload = json.load(response)
        return payload

    def _parse_payload(self, payload: JsonObject) -> list[SourceClip]:
        clips: list[SourceClip] = []
        sequences = payload.get("sequences", [])
        if not isinstance(sequences, list):
            return clips

        for sequence in sequences:
            if not isinstance(sequence, dict):
                continue
            raw_clips = sequence.get("clips", [])
            if not isinstance(raw_clips, list):
                continue
            for raw_clip in raw_clips:
                clip = self._parse_clip(raw_clip)
                if clip is not None:
                    clips.append(clip)
        return clips

    def _parse_clip(self, raw_clip: JsonValue) -> SourceClip | None:
        if not isinstance(raw_clip, dict):
            return None
        if raw_clip.get("type") != "source":
            return None

        raw_path = raw_clip.get("path")
        if not isinstance(raw_path, str):
            return None

        keyframe_durations = raw_clip.get("keyFrameDurations", [])
        if not isinstance(keyframe_durations, list):
            return None

        clip_from_ms = self._get_int(raw_clip.get("clipFrom"), default=0)
        if clip_from_ms is None:
            clip_from_ms = 0
        clip_to_ms = self._get_int(raw_clip.get("clipTo"))
        raw_duration_ms = sum(
            duration
            for duration in (self._get_int(item) for item in keyframe_durations)
            if duration is not None
        )
        end_limit_ms = (
            min(raw_duration_ms, clip_to_ms)
            if clip_to_ms is not None
            else raw_duration_ms
        )
        effective_duration_ms = max(0, end_limit_ms - clip_from_ms)
        if effective_duration_ms <= 0:
            return None

        relative_path = self._map_relative_path(raw_path)
        start_time = self._parse_start_time(relative_path, clip_from_ms)
        local_path = self._source_root / relative_path
        return SourceClip(
            path=local_path,
            camera=self._camera,
            start_time=start_time,
            duration_seconds=effective_duration_ms / 1000.0,
            relative_path=relative_path,
            trim_start_seconds=clip_from_ms / 1000.0,
        )

    def _map_relative_path(self, raw_path: str) -> Path:
        prefix = f"{self._frigate.recordings_root}/"
        if raw_path.startswith(prefix):
            raw_path = raw_path.removeprefix(prefix)
        return Path(*Path(raw_path).parts)

    def _parse_start_time(self, relative_path: Path, clip_from_ms: int) -> datetime:
        day_part, hour_part, _, file_name = relative_path.parts
        year_text, month_text, day_text = day_part.split("-")
        minute_text, second_text, suffix = file_name.split(".")
        if suffix.lower() != "mp4":
            raise ValueError(f"Unsupported clip suffix in {relative_path}")
        start_time_utc = datetime(
            year=int(year_text),
            month=int(month_text),
            day=int(day_text),
            hour=int(hour_part),
            minute=int(minute_text),
            second=int(second_text),
            tzinfo=UTC,
        )
        return start_time_utc.astimezone(self._tzinfo) + timedelta(milliseconds=clip_from_ms)

    @staticmethod
    def _get_int(value: JsonValue, default: int | None = None) -> int | None:
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        if value is None:
            return default
        return default
