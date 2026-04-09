from __future__ import annotations

import logging
import time
from argparse import ArgumentParser
from dataclasses import asdict
from pathlib import Path

from .config import Settings, load_settings
from .hls import LiveHLSPublisher
from .ingest import ContinuousClipIngestor
from .logging_utils import configure_logging
from .pipeline import PipelineServices, TimelapsePipeline
from .scanner import FrigateVodScanner
from .state import StateStore
from .worker import ContinuousFFmpegCommandBuilder, ContinuousFFmpegWorker

logger = logging.getLogger(__name__)


def build_parser() -> ArgumentParser:
    parser = ArgumentParser(prog="frigate-timelapse")
    parser.add_argument("--config", required=True, type=Path, help="Path to TOML config file")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("show-config", help="Print resolved configuration")
    subparsers.add_parser("scan", help="Query Frigate metadata and show current ingest state")
    subparsers.add_parser("run-loop", help="Continuously ingest clips into one FFmpeg HLS session")
    return parser


def load_pipeline(settings: Settings) -> TimelapsePipeline:
    settings.paths.output_root.mkdir(parents=True, exist_ok=True)
    store = StateStore(settings.paths.state_db)
    scanner = FrigateVodScanner(
        camera=settings.camera.name,
        source_root=settings.paths.source_root,
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
    )
    return TimelapsePipeline(services)


def _settings_as_dict(settings: Settings) -> dict[str, object]:
    payload = asdict(settings)
    payload["paths"] = {key: str(value) for key, value in payload["paths"].items()}
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    settings = load_settings(args.config)
    configure_logging(settings.app.log_level)
    pipeline = load_pipeline(settings)

    if args.command == "show-config":
        import json

        print(json.dumps(_settings_as_dict(settings), indent=2))
        return 0
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
        while True:
            _, sleep_seconds = pipeline.run_loop_iteration()
            if sleep_seconds > 0:
                time.sleep(sleep_seconds)
    parser.error(f"Unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
