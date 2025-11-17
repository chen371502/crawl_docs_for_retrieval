from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Dict, Optional, Tuple
from urllib.parse import urlparse, urlunparse
from urllib import robotparser

import httpx

from .utils import domain_key


@dataclass
class RobotsInfo:
    url: str
    parser: robotparser.RobotFileParser
    raw_text: str
    crawl_delay: float

    def can_fetch(self, user_agent: str, target_url: str) -> bool:
        if not self.parser:
            return True
        return self.parser.can_fetch(user_agent, target_url)


class RobotsManager:
    """Fetches and caches robots.txt directives per origin."""

    def __init__(self, user_agent: str, timeout: float = 15.0) -> None:
        self._user_agent = user_agent
        self._timeout = timeout
        self._client = httpx.AsyncClient(
            follow_redirects=True,
            timeout=timeout,
            headers={"User-Agent": user_agent},
        )
        self._cache: Dict[str, RobotsInfo] = {}
        self._locks: Dict[str, asyncio.Lock] = {}

    async def close(self) -> None:
        await self._client.aclose()

    async def allowed(self, url: str) -> Tuple[bool, RobotsInfo]:
        info = await self._get_info(url)
        if info is None:
            # No robots file found, default allow
            dummy = robotparser.RobotFileParser()
            return True, RobotsInfo(
                url="",
                parser=dummy,
                raw_text="",
                crawl_delay=0.0,
            )
        return info.can_fetch(self._user_agent, url), info

    async def _get_info(self, url: str) -> Optional[RobotsInfo]:
        origin = domain_key(url)
        if origin in self._cache:
            return self._cache[origin]

        lock = self._locks.setdefault(origin, asyncio.Lock())
        async with lock:
            if origin in self._cache:
                return self._cache[origin]
            robots_url = self._build_robots_url(origin)
            info = await self._download(robots_url)
            self._cache[origin] = info
            return info

    async def _download(self, robots_url: str) -> RobotsInfo:
        text = ""
        try:
            response = await self._client.get(robots_url)
            if response.status_code == 200:
                text = response.text
        except Exception:
            text = ""

        parser = robotparser.RobotFileParser()
        parser.set_url(robots_url)
        if text:
            parser.parse(text.splitlines())
        else:
            parser.parse([])

        delay = self._extract_delay(parser)

        return RobotsInfo(
            url=robots_url,
            parser=parser,
            raw_text=text,
            crawl_delay=delay,
        )

    def _extract_delay(self, parser: robotparser.RobotFileParser) -> float:
        delay = parser.crawl_delay(self._user_agent)
        if delay is None:
            delay = parser.crawl_delay("*")

        rate = parser.request_rate(self._user_agent)
        if rate is None:
            rate = parser.request_rate("*")
        rate_delay = 0.0
        if rate and getattr(rate, "requests", 0):
            rate_delay = rate.seconds / max(rate.requests, 1)

        effective_delay = max(delay or 0.0, rate_delay or 0.0)
        return effective_delay

    @staticmethod
    def _build_robots_url(origin: str) -> str:
        parsed = urlparse(origin)
        scheme = parsed.scheme or "https"
        netloc = parsed.netloc
        return urlunparse((scheme, netloc, "/robots.txt", "", "", ""))
