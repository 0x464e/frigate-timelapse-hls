from __future__ import annotations

import logging
import signal
from argparse import ArgumentParser
from threading import Event

from .config import Settings, load_settings
from .hls import LiveHLSPublisher
from .ingest import ContinuousClipIngestor
from .library import LibraryPublisher
from .logging_utils import configure_logging
from .pipeline import PipelineServices, TimelapsePipeline
from .scanner import FrigateVodScanner
from .state import StateStore
from .worker import ContinuousFFmpegCommandBuilder, ContinuousFFmpegWorker

logger = logging.getLogger(__name__)


def build_parser() -> ArgumentParser:
    parser = ArgumentParser(prog="frigate-timelapse-hls")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("show-config", help="Print resolved configuration")
    subparsers.add_parser("scan", help="Query Frigate metadata and show current ingest state")
    subparsers.add_parser("run-loop", help="Continuously ingest clips into one FFmpeg HLS session")
    subparsers.add_parser("publish-library", help="Rebuild web metadata without encoding video")
    return parser


def load_pipeline(settings: Settings, library: LibraryPublisher | None = None) -> TimelapsePipeline:
    settings.paths.output_root.mkdir(parents=True, exist_ok=True)
    store = StateStore(settings.paths.state_db)
    scanner = FrigateVodScanner(
        camera=settings.camera.name,
        recordings_source_root=settings.paths.recordings_source_root,
        frigate=settings.frigate,
        tzinfo=settings.tzinfo,
    )
    services = PipelineServices(
        settings=settings,
        store=store,
        scanner=scanner,
        ingestor=ContinuousClipIngestor(settings.ffmpeg),
        worker=ContinuousFFmpegWorker(
            ContinuousFFmpegCommandBuilder(settings.ffmpeg, settings.timelapse)
        ),
        hls_publisher=LiveHLSPublisher(),
        library=library,
    )
    return TimelapsePipeline(services)


def _settings_as_dict(settings: Settings) -> dict[str, object]:
    return settings.model_dump(mode="json")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    settings = load_settings()
    configure_logging(settings.app.log_level)

    if args.command == "show-config":
        import json

        print(json.dumps(_settings_as_dict(settings), indent=2))
        return 0
    library = LibraryPublisher(settings, StateStore(settings.paths.state_db))
    if args.command == "publish-library":
        library.publish()
        return 0
    pipeline = load_pipeline(settings, library)
    if args.command == "scan":
        result = pipeline.run_scan()
        logger.info(
            "Scan result for %s: seen=%s pending=%s ingested=%s mode=%s worker_running=%s",
            result.scanned_day,
            result.clips_seen,
            result.clips_pending,
            result.clips_ingested,
            result.session_mode.value,
            result.worker_running,
        )
        return 0
    if args.command == "run-loop":
        stop_requested = Event()

        def request_stop(signum: int, _frame: object) -> None:
            logger.info("Shutdown requested by signal %s", signum)
            stop_requested.set()

        signal.signal(signal.SIGINT, request_stop)
        signal.signal(signal.SIGTERM, request_stop)
        library.start()
        try:
            while not stop_requested.is_set():
                _, sleep_seconds = pipeline.run_loop_iteration()
                if sleep_seconds > 0:
                    stop_requested.wait(sleep_seconds)
        finally:
            try:
                pipeline.stop()
            finally:
                library.close()
        return 0
    parser.error(f"Unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
