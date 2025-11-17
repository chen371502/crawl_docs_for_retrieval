from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

from .config_loader import CrawlSettingsBundle, load_settings
from .crawler_runner import CrawlOrchestrator


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a robots-aware Crawl4AI pipeline constrained to a seed parent."
    )
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Path to crawl_config.yaml. Defaults to file next to this script.",
    )
    parser.add_argument("--seed", type=str, help="Override the seed URL from config.")
    parser.add_argument(
        "--max-pages", type=int, help="Maximum number of pages to visit (default 2000)."
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        help="Override the crawler concurrency level.",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        help="Directory where markdown files will be stored.",
    )
    parser.add_argument(
        "--page-timeout-ms",
        type=int,
        help="Playwright navigation timeout in milliseconds.",
    )
    parser.add_argument(
        "--wait-for-timeout-ms",
        type=int,
        help="Timeout dedicated to wait_for steps (overrides page timeout).",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        help="Python logging level (DEBUG, INFO, WARNING, ...).",
    )
    return parser.parse_args()


def apply_overrides(settings: CrawlSettingsBundle, args: argparse.Namespace) -> None:
    if args.seed:
        settings.crawl.seed_url = args.seed

    if args.max_pages is not None:
        settings.crawl.max_pages = max(1, int(args.max_pages))

    if args.concurrency is not None:
        settings.crawl.concurrency = max(1, int(args.concurrency))

    if args.output_dir:
        settings.crawl.output_dir = Path(args.output_dir).expanduser()
    if args.page_timeout_ms is not None:
        settings.crawl.page_timeout_ms = max(1000, int(args.page_timeout_ms))
    if args.wait_for_timeout_ms is not None:
        settings.crawl.wait_for_timeout_ms = max(1000, int(args.wait_for_timeout_ms))


async def run_async(settings: CrawlSettingsBundle) -> None:
    orchestrator = CrawlOrchestrator(settings)
    stats = await orchestrator.run()
    logging.getLogger(__name__).info(
        "Crawl completed: %s pages saved, %s skipped by robots, %s failures, %s attempted.",
        stats.saved_pages,
        stats.skipped_by_robots,
        stats.failures,
        stats.attempted,
    )


def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    settings = load_settings(args.config)
    apply_overrides(settings, args)
    asyncio.run(run_async(settings))


if __name__ == "__main__":
    main()
