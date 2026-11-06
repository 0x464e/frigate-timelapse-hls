"""The generator's read-only web interface: a library manifest and cached posters.

A single background thread reconciles files and fetches at most one poster at a
 time. It never runs an encoder and never receives commands from a web client.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from threading import Event, Lock, Thread
from typing import Any
from urllib.parse import quote
from urllib.request import urlopen

from .config import Settings
from .models import SessionSnapshot
from .publication import atomic_json, atomic_write
from .state import StateStore

logger = logging.getLogger(__name__)


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except OSError, ValueError:
        return {}


@dataclass(frozen=True)
class PlaylistSummary:
    duration: float = 0
    segments: int = 0
    complete: bool = False
    ready: bool = False
    modified: float | None = None


def summarize_playlist(path: Path) -> PlaylistSummary:
    """Only publish readiness for a fully available, local MPEG-TS playlist."""
    try:
        content = path.read_text(encoding="utf-8")
        modified = path.stat().st_mtime
        if not content.startswith("#EXTM3U"):
            return PlaylistSummary()
        duration = 0.0
        count = 0
        pending_duration: float | None = None
        available = True
        for raw in content.splitlines():
            line = raw.strip()
            if line.startswith("#EXTINF:"):
                pending_duration = float(line.split(":", 1)[1].split(",", 1)[0])
                if not math.isfinite(pending_duration) or pending_duration <= 0:
                    return PlaylistSummary()
            elif line and not line.startswith("#"):
                # FFmpeg uses basenames. Reject URLs and paths outside the day directory.
                if pending_duration is None or Path(line).name != line or ":" in line:
                    return PlaylistSummary()
                segment = path.parent / line
                available = available and segment.is_file() and segment.stat().st_size > 0
                duration += pending_duration
                pending_duration = None
                count += 1
        ready = count > 0 and available and pending_duration is None
        return PlaylistSummary(
            duration=round(duration, 6) if ready else 0,
            segments=count,
            complete="#EXT-X-ENDLIST" in content,
            ready=ready,
            modified=modified,
        )
    except OSError, ValueError:
        return PlaylistSummary()


@dataclass
class PosterRequest:
    directory: Path
    generation: str
    target: datetime
    frame_time: datetime
    attempts: int = 0
    retry_at: datetime | None = None

    @property
    def key(self) -> tuple[str, str, str]:
        return (str(self.directory), self.generation, self.target.isoformat())


class LibraryPublisher:
    def __init__(self, settings: Settings, store: StateStore) -> None:
        self.settings = settings
        self.store = store
        self.root = settings.paths.output_root / "published"
        self._stop = Event()
        self._lock = Lock()
        self._thread: Thread | None = None
        self._request: PosterRequest | None = None
        self._publish_lock = Lock()

    def start(self) -> None:
        self.publish()
        self._thread = Thread(target=self._run, name="web-publication", daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=12)
        self.publish()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.process_poster()
                self.publish()
            except Exception:
                logger.exception("Web publication failed; timelapse ingestion continues")
            self._stop.wait(5)

    def observe(self, snapshot: SessionSnapshot) -> None:
        """Capture schedule identity and choose a poster without doing network I/O."""
        directory = self.root / self.settings.camera.name / snapshot.day.isoformat()
        generation_file = directory / "generation.txt"
        if not generation_file.exists():
            return
        generation = generation_file.read_text().strip()
        session_path = directory / "session.json"
        session = read_json(session_path)
        previous_session = session.copy()
        if not session:
            session.update(
                schedule_start=snapshot.session_start.isoformat(),
                schedule_end=snapshot.session_end.isoformat(),
                render_speed=self.settings.timelapse.timelapse_speed,
            )
        session["recordings_available"] = bool(snapshot.clips) or session.get(
            "recordings_available", False
        )
        target = snapshot.session_start + timedelta(seconds=self.settings.thumbnail.offset_seconds)
        if target >= snapshot.session_end:
            return
        # Update the desired target even when switching back to a cached image.
        session["poster_target"] = target.isoformat()
        if session != previous_session:
            atomic_json(session_path, session)
        with self._lock:
            if self._request is not None and self._request.key != (
                str(directory),
                generation,
                target.isoformat(),
            ):
                self._request = None
        poster = read_json(directory / "poster.json")
        if (
            poster.get("target") == target.isoformat()
            and poster.get("generation_id") == generation
            and (directory / "poster.jpg").is_file()
        ):
            return
        for clip in sorted(snapshot.clips, key=lambda item: item.start_time):
            frame_time = max(target, clip.start_time)
            if (
                frame_time < clip.end_time
                and frame_time < snapshot.session_end
                and clip.end_time
                <= snapshot.observed_at - timedelta(seconds=self.settings.app.ready_buffer_seconds)
            ):
                candidate = PosterRequest(directory, generation, target, frame_time)
                with self._lock:
                    if self._request is None or self._request.key != candidate.key:
                        self._request = candidate
                return

    def process_poster(self, now: datetime | None = None) -> None:
        now = now or datetime.now(self.settings.tzinfo)
        with self._lock:
            request = self._request
            if (
                request is None
                or request.attempts >= 5
                or (request.retry_at is not None and now < request.retry_at)
            ):
                return
            request.attempts += 1
            request.retry_at = now + timedelta(seconds=30)
        url = (
            f"{self.settings.frigate.base_url}/api/"
            f"{quote(self.settings.camera.name, safe='')}/recordings/"
            f"{request.frame_time.timestamp():.3f}/snapshot.jpg?height=720"
        )
        try:
            with urlopen(url, timeout=8) as response:
                image = response.read(5 * 1024 * 1024 + 1)
                content_type = response.headers.get("Content-Type", "")
            if (
                not content_type.startswith("image/jpeg")
                or not image.startswith(b"\xff\xd8")
                or not image.endswith(b"\xff\xd9")
                or len(image) > 5 * 1024 * 1024
            ):
                raise ValueError("Frigate returned an invalid recording snapshot")
            # Regeneration may have removed the directory while the GET was running.
            generation_path = request.directory / "generation.txt"
            if (
                not generation_path.exists()
                or generation_path.read_text().strip() != request.generation
            ):
                return
            atomic_write(request.directory / "poster.jpg", image)
            atomic_json(
                request.directory / "poster.json",
                {
                    "target": request.target.isoformat(),
                    "frame_time": request.frame_time.isoformat(),
                    "generation_id": request.generation,
                },
            )
            with self._lock:
                if self._request is request:
                    self._request = None
        except Exception as exc:
            logger.warning("Poster unavailable (attempt %s/5): %s", request.attempts, exc)

    def publish(self, now: datetime | None = None) -> dict:
        with self._publish_lock:
            return self._publish(now or datetime.now(self.settings.tzinfo))

    def _publish(self, now: datetime) -> dict:
        camera = self.settings.camera.name
        entries: list[dict[str, Any]] = []
        camera_root = self.root / camera
        states = self.store.list_day_sessions(camera=camera)
        for directory in sorted(camera_root.iterdir()) if camera_root.is_dir() else []:
            if not directory.is_dir():
                continue
            try:
                day = datetime.strptime(directory.name, "%Y-%m-%d").date()
            except ValueError:
                continue
            snapshot = summarize_playlist(directory / "index.m3u8")
            live = summarize_playlist(directory / "live.m3u8")
            if not live.ready and not snapshot.ready and day != now.date():
                continue
            state = states.get(day)
            session = read_json(directory / "session.json")
            generation_path = directory / "generation.txt"
            try:
                generation = generation_path.read_text().strip()
            except OSError:
                # Stable migration identity for old archives: never rewrite their media.
                generation = f"legacy-{directory.name}-{directory.stat().st_ino}"
            base = f"/published/{quote(camera, safe='')}/{directory.name}"
            summary = live if day == now.date() and live.ready else snapshot
            if not summary.ready:
                summary = live
            poster = read_json(directory / "poster.json")
            poster_ready = (
                (directory / "poster.jpg").is_file()
                and poster.get("generation_id") == generation
                and (
                    not session.get("poster_target")
                    or poster.get("target") == session["poster_target"]
                )
            )
            status = state.status.value if state else "unknown"
            # A historical live file can lack a snapshot after an abrupt shutdown.
            # Publish a finite copy so archive playback never waits indefinitely.
            if day < now.date() and live.ready and not snapshot.ready:
                from .hls import LiveHLSPublisher

                publisher = LiveHLSPublisher()
                paths = publisher.prepare_day_session(
                    camera=camera,
                    day_label=directory.name,
                    output_root=self.settings.paths.output_root,
                    reset=False,
                )
                publisher.publish_snapshot_playlist(paths)
                snapshot = summarize_playlist(paths.playlist_path)
                summary = snapshot
                generation = generation_path.read_text().strip()
            latest_mtime = summary.modified
            entries.append(
                {
                    "date": directory.name,
                    "generation_id": generation,
                    "poster_url": f"{base}/poster.jpg?v={generation}-{poster.get('target', '')}"
                    if poster_ready
                    else None,
                    "playlist_url": f"{base}/index.m3u8",
                    "live_playlist_url": f"{base}/live.m3u8",
                    "duration": summary.duration,
                    "ready": summary.ready,
                    "recordings_available": session.get("recordings_available", False)
                    or bool(state and state.discovered_clip_count > 0),
                    "status": status,
                    "mode": state.mode.value if state else None,
                    "complete": status == "completed",
                    "stream_ended": live.complete,
                    "published_at": datetime.fromtimestamp(
                        latest_mtime, self.settings.tzinfo
                    ).isoformat()
                    if latest_mtime is not None
                    else None,
                    "schedule_start": session.get("schedule_start"),
                    "schedule_end": session.get("schedule_end"),
                    "render_speed": session.get("render_speed"),
                }
            )
        manifest = {
            "version": 1,
            "camera": camera,
            "timezone": self.settings.app.timezone,
            "schedule": {
                "start": self.settings.schedule.start_time.isoformat(),
                "end": self.settings.schedule.end_time.isoformat(),
            },
            "thumbnail_offset_seconds": self.settings.thumbnail.offset_seconds,
            "published_at": now.isoformat(),
            "days": sorted(entries, key=lambda item: item["date"], reverse=True),
        }
        atomic_json(self.root / "library.json", manifest)
        return manifest
