from __future__ import annotations

import asyncio
import logging
import copy
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig

from .config_loader import CrawlSettingsBundle
from .crawl_queue import CrawlQueue
from .robots_manager import RobotsInfo, RobotsManager
from .throttle import ThrottleController
from .utils import build_markdown_path, domain_key
from .tab_traversal import TabTraversalManager, TabMarkdownBlock


logger = logging.getLogger(__name__)


@dataclass
class CrawlStats:
    saved_pages: int = 0
    skipped_by_robots: int = 0
    failures: int = 0
    attempted: int = 0


@dataclass
class DiscoveredLinks:
    page_url: str
    hrefs: List[str] = field(default_factory=list)


class CrawlOrchestrator:
    """Coordinates the Crawl4AI session together with robots + throttling logic."""

    def __init__(self, settings: CrawlSettingsBundle) -> None:
        self.settings = settings
        self._queue = CrawlQueue(
            scope_url=settings.crawl.scope_url,
            respect_parent=settings.crawl.respect_parent_path,
        )
        self._robots = RobotsManager(settings.browser.user_agent)
        self._throttle = ThrottleController(
            min_seconds=settings.delay.min_seconds,
            max_seconds=settings.delay.max_seconds,
        )
        self._logged_robots: set[str] = set()
        self._stats = CrawlStats()
        self._run_config = CrawlerRunConfig(
            cache_mode=CacheMode.BYPASS,
            page_timeout=self.settings.crawl.page_timeout_ms,
            wait_for_timeout=self.settings.crawl.wait_for_timeout_ms,
        )
        self._tab_manager = TabTraversalManager(settings.tabs)
        self._output_dir: Path = settings.crawl.output_dir
        self._output_dir.mkdir(parents=True, exist_ok=True)

    async def run(self) -> CrawlStats:
        """Kick off the crawl and return summary statistics."""

        try:
            seed_added = self._queue.add(self.settings.crawl.seed_url)
            if not seed_added:
                raise RuntimeError("Seed URL could not be enqueued. Check configuration.")

            browser_config = BrowserConfig(
                headless=self.settings.browser.headless,
                verbose=self.settings.browser.verbose,
                user_agent=self.settings.browser.user_agent,
            )

            async with AsyncWebCrawler(config=browser_config) as crawler:
                await self._log_seed_robots()
                await self._drain_queue(crawler)
        finally:
            await self._robots.close()

        return self._stats

    async def _log_seed_robots(self) -> None:
        allowed, info = await self._robots.allowed(self.settings.crawl.seed_url)
        await self._log_robots_info(info)
        if not allowed:
            logger.warning("Seed URL is disallowed by robots.txt. No crawling performed.")

    async def _drain_queue(self, crawler: AsyncWebCrawler) -> None:
        while self._queue.pending and self._stats.attempted < self.settings.crawl.max_pages:
            remaining = self.settings.crawl.max_pages - self._stats.attempted
            batch_size = min(self.settings.crawl.concurrency, remaining)
            urls = self._queue.next_batch(batch_size)
            if not urls:
                break

            self._stats.attempted += len(urls)
            results = await asyncio.gather(
                *[self._crawl_single(crawler, url) for url in urls],
                return_exceptions=True,
            )

            for url, outcome in zip(urls, results):
                if isinstance(outcome, Exception):
                    self._stats.failures += 1
                    logger.error("Error while crawling %s: %s", url, outcome)
                    continue
                if not isinstance(outcome, DiscoveredLinks):
                    continue
                added = self._queue.extend(outcome.hrefs, base_url=outcome.page_url)
                if added:
                    logger.debug("Queued %d new URLs from %s", added, url)

    async def _crawl_single(self, crawler: AsyncWebCrawler, url: str) -> DiscoveredLinks:
        allowed, robots_info = await self._robots.allowed(url)
        await self._log_robots_info(robots_info)
        if not allowed:
            self._stats.skipped_by_robots += 1
            logger.info("Skipped %s due to robots.txt restrictions.", url)
            return DiscoveredLinks(page_url=url)

        await self._throttle.wait_for_turn(url, robots_info.crawl_delay)

        session_id = str(uuid.uuid4()) if self._tab_manager.enabled else None
        run_config = self._clone_run_config(session_id=session_id)
        tab_blocks: List[TabMarkdownBlock] = []

        try:
            result = await crawler.arun(url=url, config=run_config)
        except Exception as exc:
            self._stats.failures += 1
            logger.exception("Crawler threw an exception for %s: %s", url, exc)
            await self._cleanup_session(crawler, session_id)
            return DiscoveredLinks(page_url=url)

        if not getattr(result, "success", True):
            self._stats.failures += 1
            message = getattr(result, "error_message", "unknown error")
            logger.warning("Crawling %s failed: %s", url, message)
            await self._cleanup_session(crawler, session_id)
            return DiscoveredLinks(page_url=url)

        markdown = getattr(result, "markdown", None)
        if self._tab_manager.enabled and session_id:
            tab_blocks = await self._tab_manager.collect_markdown_blocks(
                crawler=crawler,
                url=result.url or url,
                base_config=self._run_config,
                session_id=session_id,
            )

        await self._cleanup_session(crawler, session_id)

        combined_markdown = self._merge_markdown(markdown, tab_blocks)
        if combined_markdown:
            saved_path = self._persist_markdown(result.url, combined_markdown)
            self._stats.saved_pages += 1
            logger.info("Saved %s to %s", url, saved_path)
        else:
            logger.info("No markdown content returned for %s", url)

        source_url = getattr(result, "redirected_url", None) or result.url or url
        new_links = self._extract_links(result, tab_blocks)
        return DiscoveredLinks(page_url=source_url, hrefs=new_links)

    async def _log_robots_info(self, info: RobotsInfo) -> None:
        source = info.url or self.settings.crawl.seed_url
        origin = domain_key(source)
        if origin in self._logged_robots:
            return
        self._logged_robots.add(origin)
        snippet = info.raw_text.strip() or "<empty>"
        if len(snippet) > 800:
            snippet = f"{snippet[:800]}..."
        logger.info(
            "robots.txt for %s (crawl delay %.2fs)\n%s",
            origin,
            info.crawl_delay,
            snippet,
        )

    def _persist_markdown(self, url: str, markdown_text: str) -> Path:
        path = build_markdown_path(self._output_dir, url)
        with path.open("w", encoding="utf-8") as handle:
            handle.write(f"# Source: {url}\n\n")
            handle.write(markdown_text.strip())
            handle.write("\n")
        return path

    def _clone_run_config(self, session_id: Optional[str] = None) -> CrawlerRunConfig:
        cloned = copy.deepcopy(self._run_config)
        cloned.session_id = session_id
        return cloned

    async def _cleanup_session(self, crawler: AsyncWebCrawler, session_id: Optional[str]) -> None:
        if not session_id:
            return
        try:
            await crawler.crawler_strategy.kill_session(session_id)
        except Exception:
            logger.debug("Failed to clean up session %s", session_id)

    def _merge_markdown(
        self, markdown, tab_blocks: List[TabMarkdownBlock]
    ) -> Optional[str]:
        base_text = markdown.raw_markdown if markdown and getattr(markdown, "raw_markdown", "") else ""
        if tab_blocks:
            base_text = self._tab_manager.merge_into_markdown(base_text, tab_blocks)
        else:
            base_text = base_text.strip()
        return base_text or None

    def _extract_links(
        self, result, tab_blocks: Optional[List[TabMarkdownBlock]] = None
    ) -> List[str]:
        collected: List[str] = []
        link_groups = getattr(result, "links", {}) or {}
        for group in ("internal", "external"):
            for link in link_groups.get(group, []):
                href = (link.get("href") or "").strip()
                if href:
                    collected.append(href)
        if tab_blocks:
            for block in tab_blocks:
                for href in block.links:
                    href = (href or "").strip()
                    if href:
                        collected.append(href)
        return collected
