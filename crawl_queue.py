from __future__ import annotations

from collections import deque
from typing import Deque, Iterable, List, Optional, Set

from .utils import normalize_url, shares_same_parent


class CrawlQueue:
    """Queue wrapper that deduplicates URLs and enforces the parent constraint."""

    def __init__(self, parent_url: str, respect_parent: bool = True) -> None:
        self._parent_url = parent_url
        self._respect_parent = respect_parent
        self._pending: Deque[str] = deque()
        self._queued: Set[str] = set()
        self._seen: Set[str] = set()

    def add(self, url: str, base_url: Optional[str] = None) -> bool:
        normalized = normalize_url(url, base_url)
        if not normalized:
            return False
        if self._respect_parent and not shares_same_parent(normalized, self._parent_url):
            return False
        if normalized in self._queued or normalized in self._seen:
            return False
        self._pending.append(normalized)
        self._queued.add(normalized)
        return True

    def extend(self, urls: Iterable[str], base_url: Optional[str] = None) -> int:
        added = 0
        for url in urls:
            if self.add(url, base_url=base_url):
                added += 1
        return added

    def next_batch(self, size: int) -> List[str]:
        batch: List[str] = []
        while self._pending and len(batch) < size:
            url = self._pending.popleft()
            self._queued.discard(url)
            self._seen.add(url)
            batch.append(url)
        return batch

    @property
    def pending(self) -> int:
        return len(self._pending)

    @property
    def scheduled(self) -> int:
        return len(self._seen)

    def __bool__(self) -> bool:
        return bool(self._pending)
