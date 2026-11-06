from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

from .publication import atomic_json, atomic_write


@dataclass(slots=True, frozen=True)
class LiveSessionPaths:
    publish_dir: Path
    playlist_path: Path
    live_playlist_path: Path
    segment_dir: Path
    latest_pointer_path: Path


class LiveHLSPublisher:
    def prepare_day_session(
        self,
        *,
        camera: str,
        day_label: str,
        output_root: Path,
        reset: bool,
    ) -> LiveSessionPaths:
        camera_publish_dir = output_root / "published" / camera
        publish_dir = camera_publish_dir / day_label
        if reset and publish_dir.exists():
            shutil.rmtree(publish_dir)
        publish_dir.mkdir(parents=True, exist_ok=True)
        generation_path = publish_dir / "generation.txt"
        if not generation_path.exists():
            atomic_write(generation_path, str(uuid4()) + "\n")
        return LiveSessionPaths(
            publish_dir=publish_dir,
            playlist_path=publish_dir / "index.m3u8",
            live_playlist_path=publish_dir / "live.m3u8",
            segment_dir=publish_dir,
            latest_pointer_path=camera_publish_dir / "latest.json",
        )

    def publish_latest_pointer(
        self,
        *,
        camera: str,
        day_label: str,
        output_root: Path,
    ) -> Path:
        camera_publish_dir = output_root / "published" / camera
        camera_publish_dir.mkdir(parents=True, exist_ok=True)
        playlist_url = f"/published/{quote(camera, safe='._-')}/{day_label}/index.m3u8"
        latest_path = camera_publish_dir / "latest.json"
        atomic_json(
            latest_path,
            {"day": day_label, "playlist_url": playlist_url},
        )
        return latest_path

    def publish_snapshot_playlist(self, paths: LiveSessionPaths) -> Path | None:
        if not paths.live_playlist_path.exists():
            return None
        contents = paths.live_playlist_path.read_text(encoding="utf-8")
        normalized = contents.rstrip()
        if "#EXT-X-ENDLIST" not in normalized:
            normalized = f"{normalized}\n#EXT-X-ENDLIST"
        atomic_write(paths.playlist_path, f"{normalized}\n")
        return paths.playlist_path

    def prepare_live_playlist_for_resume(self, paths: LiveSessionPaths) -> None:
        if not paths.live_playlist_path.exists():
            return
        lines = [
            line
            for line in paths.live_playlist_path.read_text(encoding="utf-8").splitlines()
            if line != "#EXT-X-ENDLIST"
        ]
        atomic_write(paths.live_playlist_path, "\n".join(lines).rstrip() + "\n")
