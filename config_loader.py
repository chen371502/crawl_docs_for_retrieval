from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

from .utils import derive_parent_url, normalize_url

DEFAULT_CONFIG_PATH = Path(__file__).with_name("crawl_config.yaml")
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)


@dataclass
class DelaySettings:
    """Holds random delay boundaries applied before every visit."""

    min_seconds: float = 1.0
    max_seconds: float = 3.0

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "DelaySettings":
        data = data or {}
        min_seconds = float(data.get("min_seconds", cls.min_seconds))
        max_seconds = float(data.get("max_seconds", cls.max_seconds))
        if max_seconds < min_seconds:
            max_seconds = min_seconds
        return cls(min_seconds=min_seconds, max_seconds=max_seconds)


@dataclass
class BrowserSettings:
    """Browser level switches that map to Crawl4AI's ``BrowserConfig``."""

    headless: bool = True
    verbose: bool = False
    user_agent: str = DEFAULT_USER_AGENT

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "BrowserSettings":
        data = data or {}
        return cls(
            headless=bool(data.get("headless", cls.headless)),
            verbose=bool(data.get("verbose", cls.verbose)),
            user_agent=str(data.get("user_agent", cls.user_agent)),
        )


@dataclass
class TabTraversalSettings:
    """Controls optional tab traversal and extraction behavior."""

    enabled: bool = False
    max_groups: int = 10
    max_tabs_per_group: int = 5
    max_total_tabs: int = 40
    heading_template: str = "#### [Tab: {group} - {label}]"
    wait_for_activation_ms: int = 4000

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "TabTraversalSettings":
        data = data or {}
        max_groups = int(data.get("max_groups", cls.max_groups))
        max_tabs_per_group = int(data.get("max_tabs_per_group", cls.max_tabs_per_group))
        max_total_tabs = int(data.get("max_total_tabs", cls.max_total_tabs))
        heading_template = str(data.get("heading_template", cls.heading_template)).strip()
        wait_for_activation_ms = int(
            data.get("wait_for_activation_ms", cls.wait_for_activation_ms)
        )
        if wait_for_activation_ms < 500:
            wait_for_activation_ms = 500
        return cls(
            enabled=bool(data.get("enabled", cls.enabled)),
            max_groups=max(1, max_groups),
            max_tabs_per_group=max(1, max_tabs_per_group),
            max_total_tabs=max(1, max_total_tabs),
            heading_template=heading_template or cls.heading_template,
            wait_for_activation_ms=wait_for_activation_ms,
        )


@dataclass
class CrawlParameters:
    """Top level crawling options."""

    seed_url: str
    output_dir: Path
    concurrency: int = 1
    max_pages: int = 2000
    respect_parent_path: bool = True
    page_timeout_ms: int = 120_000
    wait_for_timeout_ms: Optional[int] = None
    scope_mode: str = "parent"

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CrawlParameters":
        if "seed_url" not in data:
            raise ValueError("Config is missing required 'seed_url'.")
        concurrency = int(data.get("concurrency", cls.concurrency))
        if concurrency < 1:
            concurrency = 1
        max_pages = int(data.get("max_pages", cls.max_pages))
        if max_pages < 1:
            max_pages = cls.max_pages

        output_dir = Path(data.get("output_dir", "crawl_results")).expanduser()
        page_timeout_ms = int(data.get("page_timeout_ms", cls.page_timeout_ms))
        if page_timeout_ms < 1000:
            page_timeout_ms = 1000
        wait_for_timeout = data.get("wait_for_timeout_ms")
        wait_for_timeout_ms = int(wait_for_timeout) if wait_for_timeout is not None else None
        scope_mode = str(data.get("scope_mode", cls.scope_mode)).strip().lower()
        if scope_mode not in {"parent", "seed"}:
            scope_mode = cls.scope_mode

        return cls(
            seed_url=str(data["seed_url"]),
            output_dir=output_dir,
            concurrency=concurrency,
            max_pages=max_pages,
            respect_parent_path=bool(
                data.get("respect_parent_path", cls.respect_parent_path)
            ),
            page_timeout_ms=page_timeout_ms,
            wait_for_timeout_ms=wait_for_timeout_ms,
            scope_mode=scope_mode,
        )

    @property
    def parent_url(self) -> str:
        return derive_parent_url(self.seed_url)

    @property
    def scope_url(self) -> str:
        if self.scope_mode == "seed":
            normalized = normalize_url(self.seed_url)
            return normalized or self.seed_url
        return derive_parent_url(self.seed_url)


@dataclass
class CrawlSettingsBundle:
    """Aggregates every configuration section."""

    browser: BrowserSettings
    crawl: CrawlParameters
    delay: DelaySettings
    tabs: TabTraversalSettings


def load_settings(path: Optional[str | Path] = None) -> CrawlSettingsBundle:
    """
    Load settings from YAML and return a ready-to-use dataclass.

    Args:
        path: Optional override path. Defaults to ``crawl_config.yaml`` next to this module.
    """

    config_path = Path(path or DEFAULT_CONFIG_PATH)
    if not config_path.exists():
        raise FileNotFoundError(
            f"Unable to locate configuration file at {config_path}. "
            "Please create it before running the crawler."
        )

    with config_path.open("r", encoding="utf-8") as handle:
        raw_config = yaml.safe_load(handle) or {}

    browser = BrowserSettings.from_dict(raw_config.get("browser"))
    delay = DelaySettings.from_dict(raw_config.get("delay"))
    crawl = CrawlParameters.from_dict(raw_config.get("crawl", {}))
    tabs = TabTraversalSettings.from_dict(raw_config.get("tab_traversal"))

    return CrawlSettingsBundle(browser=browser, crawl=crawl, delay=delay, tabs=tabs)
