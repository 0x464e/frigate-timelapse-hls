from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from .config import Settings
from .hls import LiveHLSPublisher, LiveSessionPaths
from .ingest import ContinuousClipIngestor
from .models import (
    LoopResult,
    SessionMode,
    SessionSnapshot,
    SessionState,
    SessionStatus,
    SourceClip,
)
from .scanner import FrigateVodScanner
from .state import StateStore
from .worker import ContinuousFFmpegWorker, WorkerSessionPlan

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class PipelineServices:
    settings: Settings
    store: StateStore
    scanner: FrigateVodScanner
    ingestor: ContinuousClipIngestor
    worker: ContinuousFFmpegWorker
    hls_publisher: LiveHLSPublisher


class TimelapsePipeline:
    def __init__(self, services: PipelineServices) -> None:
        self._services = services
        self._active_day: date | None = None

    def run_scan(self, now: datetime | None = None) -> LoopResult:
        observed_at = now or datetime.now(tz=self._services.settings.tzinfo)
        existing_state = self._services.store.get_day_session(
            camera=self._services.settings.camera.name,
            day=observed_at.date(),
        )
        snapshot = self._build_snapshot(now=observed_at, existing_state=existing_state)
        if snapshot is None:
            return LoopResult(
                scanned_day=observed_at.date(),
                clips_seen=0,
                clips_pending=0,
                clips_ingested=0,
                session_mode=SessionMode.IDLE,
                worker_running=self._services.worker.is_running(),
            )
        ingested_clip_ids = self._services.store.list_ingested_clip_ids(
            camera=self._services.settings.camera.name,
            day=snapshot.day,
        )
        pending_clips = [
            clip for clip in snapshot.clips if clip.ingest_id not in ingested_clip_ids
        ]
        mode = self._resolve_mode(
            observed_at=observed_at,
            session_end=snapshot.session_end,
            pending_clips=pending_clips,
        )
        return LoopResult(
            scanned_day=snapshot.day,
            clips_seen=len(snapshot.clips),
            clips_pending=len(pending_clips),
            clips_ingested=len(snapshot.clips) - len(pending_clips),
            session_mode=mode,
            worker_running=self._services.worker.is_running(),
        )

    def run_loop_iteration(self, now: datetime | None = None) -> tuple[LoopResult, float]:
        observed_at = now or datetime.now(tz=self._services.settings.tzinfo)
        existing_state = self._services.store.get_day_session(
            camera=self._services.settings.camera.name,
            day=observed_at.date(),
        )
        initial_snapshot = self._build_snapshot(
            now=observed_at,
            existing_state=existing_state,
        )
        if initial_snapshot is None:
            result = LoopResult(
                scanned_day=observed_at.date(),
                clips_seen=0,
                clips_pending=0,
                clips_ingested=0,
                session_mode=SessionMode.IDLE,
                worker_running=self._services.worker.is_running(),
            )
            sleep_seconds = self._compute_idle_sleep(current_time=observed_at)
            logger.info("Idle; next scan in %.1f seconds", sleep_seconds)
            return result, sleep_seconds

        self._ensure_day_session(initial_snapshot)
        existing_state = self._services.store.get_day_session(
            camera=self._services.settings.camera.name,
            day=initial_snapshot.day,
        )
        steady_live_follow = (
            existing_state is not None and existing_state.mode == SessionMode.LIVE_FOLLOW
        )

        total_catchup_clips = 0
        total_catchup_source_seconds = 0.0
        last_ingested_clip: SourceClip | None = None
        latest_discovered_clip: SourceClip | None = None
        snapshot = initial_snapshot
        final_mode = SessionMode.LIVE_FOLLOW
        backlog_catchup = False

        while True:
            ingested_clip_ids = self._services.store.list_ingested_clip_ids(
                camera=self._services.settings.camera.name,
                day=snapshot.day,
            )
            pending_clips = [
                clip for clip in snapshot.clips if clip.ingest_id not in ingested_clip_ids
            ]
            latest_discovered_clip = snapshot.clips[-1] if snapshot.clips else None

            if not pending_clips:
                final_mode = self._resolve_mode(
                    observed_at=snapshot.observed_at,
                    session_end=snapshot.session_end,
                    pending_clips=[],
                )
                break

            current_pending_source_seconds = sum(
                clip.duration_seconds for clip in pending_clips
            )
            if total_catchup_clips == 0:
                backlog_catchup = (
                    not steady_live_follow
                    and (
                        len(pending_clips) >= 12
                        or current_pending_source_seconds >= 120.0
                    )
                )

            if total_catchup_clips == 0 and backlog_catchup:
                logger.info(
                    "Starting catch-up for %s: %s clips spanning %.1f minutes of source time",
                    snapshot.day,
                    len(pending_clips),
                    current_pending_source_seconds / 60.0,
                )

            logger.debug(
                "Ingesting %s pending clips for %s",
                len(pending_clips),
                snapshot.day,
            )

            for clip in pending_clips:
                self._services.ingestor.ingest_clip(clip, self._services.worker)
                self._services.store.mark_clip_ingested(
                    camera=self._services.settings.camera.name,
                    day=snapshot.day,
                    clip=clip,
                    ingested_at=datetime.now(tz=self._services.settings.tzinfo),
                )
                self._publish_snapshot_playlist_for_day(snapshot.day)
                last_ingested_clip = clip

            total_catchup_clips += len(pending_clips)
            total_catchup_source_seconds += current_pending_source_seconds

            next_snapshot = self._build_snapshot(
                now=datetime.now(tz=self._services.settings.tzinfo),
                existing_state=self._services.store.get_day_session(
                    camera=self._services.settings.camera.name,
                    day=snapshot.day,
                ),
            )
            if next_snapshot is None:
                break
            snapshot = next_snapshot

        current_state = self._build_session_state(
            snapshot=snapshot,
            mode=final_mode,
            latest_discovered_clip=latest_discovered_clip,
            last_ingested_clip=last_ingested_clip,
            newly_ingested_count=total_catchup_clips,
        )
        self._services.store.upsert_day_session(current_state)
        self._services.hls_publisher.publish_latest_pointer(
            camera=self._services.settings.camera.name,
            day_label=snapshot.day.isoformat(),
            output_root=self._services.settings.paths.output_root,
        )
        self._publish_snapshot_playlist(current_state)

        if total_catchup_clips > 0:
            if steady_live_follow or not backlog_catchup:
                logger.debug(
                    "Live-follow ingested %s clips for %s spanning %.1f minutes of source time",
                    total_catchup_clips,
                    snapshot.day,
                    total_catchup_source_seconds / 60.0,
                )
            else:
                logger.info(
                    (
                        "Catch-up finished for %s: ingested %s clips spanning "
                        "%.1f minutes of source time"
                    ),
                    snapshot.day,
                    total_catchup_clips,
                    total_catchup_source_seconds / 60.0,
                )

        if snapshot.observed_at >= snapshot.session_end and final_mode == SessionMode.COMPLETED:
            self._finalize_active_session(snapshot.day, current_state)
            result = LoopResult(
                scanned_day=snapshot.day,
                clips_seen=len(snapshot.clips),
                clips_pending=0,
                clips_ingested=len(snapshot.clips),
                session_mode=SessionMode.COMPLETED,
                worker_running=False,
            )
            sleep_seconds = self._compute_idle_sleep(current_time=snapshot.observed_at)
            logger.info(
                "Session completed for %s; next scan in %.1f seconds",
                snapshot.day,
                sleep_seconds,
            )
            return result, sleep_seconds

        sleep_seconds = self._compute_active_sleep(
            current_time=snapshot.observed_at,
            latest_discovered_clip=latest_discovered_clip,
            pending_clips=[],
            session_end=snapshot.session_end,
        )
        result = LoopResult(
            scanned_day=snapshot.day,
            clips_seen=len(snapshot.clips),
            clips_pending=0,
            clips_ingested=len(snapshot.clips),
            session_mode=final_mode,
            worker_running=self._services.worker.is_running(),
        )
        if final_mode == SessionMode.LIVE_FOLLOW and not steady_live_follow:
            logger.info(
                "Live-follow mode for %s; next scan in %.1f seconds",
                snapshot.day,
                sleep_seconds,
            )
        elif final_mode != SessionMode.LIVE_FOLLOW:
            logger.info(
                "%s for %s; next scan in %.1f seconds",
                final_mode.value,
                snapshot.day,
                sleep_seconds,
            )
        return result, sleep_seconds

    def _build_snapshot(
        self,
        *,
        now: datetime,
        existing_state: SessionState | None,
    ) -> SessionSnapshot | None:
        settings = self._services.settings
        day = now.date()
        session_start = settings.window_start_for_day(day)
        session_end = settings.window_end_for_day(day)
        query_end = min(now, session_end)
        if query_end <= session_start:
            return None
        query_start = self._compute_query_start(
            now=now,
            session_start=session_start,
            existing_state=existing_state,
        )
        clips = tuple(
            self._services.scanner.scan_range(
                start_time=query_start,
                end_time=query_end,
            )
        )
        return SessionSnapshot(
            observed_at=now,
            day=day,
            session_start=session_start,
            session_end=session_end,
            query_end=query_end,
            clips=clips,
        )

    def _compute_query_start(
        self,
        *,
        now: datetime,
        session_start: datetime,
        existing_state: SessionState | None,
    ) -> datetime:
        if existing_state is None or existing_state.mode != SessionMode.LIVE_FOLLOW:
            return session_start

        # In steady live-follow mode, only ask Frigate for a short trailing window.
        # Keep a small overlap beyond the ready buffer to safely cover the newest
        # one or two source clips without re-querying the whole day.
        lookback_seconds = max(self._services.settings.app.ready_buffer_seconds, 30) + 20
        trailing_start = now - timedelta(seconds=lookback_seconds)
        return max(session_start, trailing_start)

    def _ensure_day_session(self, snapshot: SessionSnapshot) -> None:
        settings = self._services.settings
        if self._active_day != snapshot.day:
            if self._services.worker.is_running():
                self._services.worker.stop()
            self._active_day = snapshot.day
            previous_state = self._services.store.get_day_session(
                camera=settings.camera.name,
                day=snapshot.day,
            )
            if previous_state is not None:
                self._services.store.reset_day_session(
                    camera=settings.camera.name,
                    day=snapshot.day,
                )
            session_paths = self._services.hls_publisher.prepare_day_session(
                camera=settings.camera.name,
                day_label=snapshot.day.isoformat(),
                output_root=settings.paths.output_root,
                reset=True,
            )
            self._start_worker(snapshot, session_paths)
            return
        if not self._services.worker.is_running():
            session_paths = self._services.hls_publisher.prepare_day_session(
                camera=settings.camera.name,
                day_label=snapshot.day.isoformat(),
                output_root=settings.paths.output_root,
                reset=False,
            )
            self._start_worker(snapshot, session_paths)

    def _start_worker(self, snapshot: SessionSnapshot, session_paths: LiveSessionPaths) -> None:
        self._services.worker.start(
            WorkerSessionPlan(
                camera=self._services.settings.camera.name,
                day_label=snapshot.day.isoformat(),
                paths=session_paths,
            )
        )
        session_state = SessionState(
            camera=self._services.settings.camera.name,
            day=snapshot.day,
            session_start=snapshot.session_start,
            session_end=snapshot.session_end,
            status=SessionStatus.RUNNING,
            mode=SessionMode.CATCH_UP,
            playlist_path=session_paths.playlist_path,
            segment_dir=session_paths.segment_dir,
            last_discovered_clip_id=None,
            last_ingested_clip_id=None,
            discovered_clip_count=0,
            ingested_clip_count=0,
            ffmpeg_pid=self._services.worker.pid,
            updated_at=snapshot.observed_at,
        )
        self._services.store.upsert_day_session(session_state)
        self._services.hls_publisher.publish_latest_pointer(
            camera=self._services.settings.camera.name,
            day_label=snapshot.day.isoformat(),
            output_root=self._services.settings.paths.output_root,
        )
        self._publish_snapshot_playlist(session_state)

    def _build_session_state(
        self,
        *,
        snapshot: SessionSnapshot,
        mode: SessionMode,
        latest_discovered_clip: SourceClip | None,
        last_ingested_clip: SourceClip | None,
        newly_ingested_count: int,
    ) -> SessionState:
        existing_state = self._services.store.get_day_session(
            camera=self._services.settings.camera.name,
            day=snapshot.day,
        )
        if existing_state is None:
            paths = self._services.hls_publisher.prepare_day_session(
                camera=self._services.settings.camera.name,
                day_label=snapshot.day.isoformat(),
                output_root=self._services.settings.paths.output_root,
                reset=False,
            )
            playlist_path = paths.playlist_path
            segment_dir = paths.segment_dir
            ingested_count = 0
        else:
            playlist_path = existing_state.playlist_path
            segment_dir = existing_state.segment_dir
            ingested_count = existing_state.ingested_clip_count

        ingested_count += newly_ingested_count

        return SessionState(
            camera=self._services.settings.camera.name,
            day=snapshot.day,
            session_start=snapshot.session_start,
            session_end=snapshot.session_end,
            status=SessionStatus.RUNNING,
            mode=mode,
            playlist_path=playlist_path,
            segment_dir=segment_dir,
            last_discovered_clip_id=(
                None if latest_discovered_clip is None else latest_discovered_clip.ingest_id
            ),
            last_ingested_clip_id=(
                existing_state.last_ingested_clip_id
                if last_ingested_clip is None and existing_state is not None
                else None if last_ingested_clip is None else last_ingested_clip.ingest_id
            ),
            discovered_clip_count=len(snapshot.clips),
            ingested_clip_count=ingested_count,
            ffmpeg_pid=self._services.worker.pid,
            updated_at=snapshot.observed_at,
        )

    def _resolve_mode(
        self,
        *,
        observed_at: datetime,
        session_end: datetime,
        pending_clips: list[SourceClip],
    ) -> SessionMode:
        if observed_at >= session_end and not pending_clips:
            return SessionMode.COMPLETED
        if pending_clips:
            return SessionMode.CATCH_UP
        return SessionMode.LIVE_FOLLOW

    def _compute_active_sleep(
        self,
        *,
        current_time: datetime,
        latest_discovered_clip: SourceClip | None,
        pending_clips: list[SourceClip],
        session_end: datetime,
    ) -> float:
        if pending_clips:
            return 0.0
        buffer_seconds = max(0, self._services.settings.app.ready_buffer_seconds)
        if latest_discovered_clip is None:
            wake_at = min(session_end, current_time + timedelta(seconds=buffer_seconds))
            return max(0.0, (wake_at - current_time).total_seconds())
        next_possible_clip_at = latest_discovered_clip.end_time + timedelta(
            seconds=buffer_seconds
        )
        if current_time >= next_possible_clip_at:
            return 0.0
        wake_at = min(session_end, next_possible_clip_at)
        return max(0.0, (wake_at - current_time).total_seconds())

    def _compute_idle_sleep(self, *, current_time: datetime) -> float:
        settings = self._services.settings
        day = current_time.date()
        session_start = settings.window_start_for_day(day)
        if current_time < session_start:
            wake_at = session_start + timedelta(seconds=settings.app.ready_buffer_seconds)
            return max(0.0, (wake_at - current_time).total_seconds())
        next_day = day + timedelta(days=1)
        wake_at = settings.window_start_for_day(next_day) + timedelta(
            seconds=settings.app.ready_buffer_seconds
        )
        return max(0.0, (wake_at - current_time).total_seconds())

    def _finalize_active_session(self, day: date, current_state: SessionState) -> None:
        if self._services.worker.is_running():
            self._services.worker.stop()
        self._publish_snapshot_playlist(current_state)
        self._services.store.upsert_day_session(
            SessionState(
                camera=current_state.camera,
                day=day,
                session_start=current_state.session_start,
                session_end=current_state.session_end,
                status=SessionStatus.COMPLETED,
                mode=SessionMode.COMPLETED,
                playlist_path=current_state.playlist_path,
                segment_dir=current_state.segment_dir,
                last_discovered_clip_id=current_state.last_discovered_clip_id,
                last_ingested_clip_id=current_state.last_ingested_clip_id,
                discovered_clip_count=current_state.discovered_clip_count,
                ingested_clip_count=current_state.ingested_clip_count,
                ffmpeg_pid=None,
                updated_at=datetime.now(tz=self._services.settings.tzinfo),
            )
        )

    def _publish_snapshot_playlist(self, state: SessionState) -> None:
        self._publish_snapshot_playlist_for_day(state.day)

    def _publish_snapshot_playlist_for_day(self, day: date) -> None:
        camera = self._services.settings.camera.name
        day_label = day.isoformat()
        paths = self._services.hls_publisher.prepare_day_session(
            camera=camera,
            day_label=day_label,
            output_root=self._services.settings.paths.output_root,
            reset=False,
        )
        self._services.hls_publisher.publish_snapshot_playlist(
            paths
        )
