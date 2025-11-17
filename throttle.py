from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Dict, Optional

from .utils import choose_random_delay, domain_key


@dataclass
class ThrottleController:
    """
    Simple rate limiter that enforces random delays per domain and honors robots directives.
    """

    min_seconds: float
    max_seconds: float
    _last_hit: Dict[str, float] = field(default_factory=dict)
    _lock: asyncio.Lock = field(init=False)

    def __post_init__(self) -> None:
        self._lock = asyncio.Lock()

    async def wait_for_turn(self, url: str, robots_delay: Optional[float]) -> float:
        """
        Block until the caller is allowed to make the next request to ``url``'s domain.

        Returns:
            The amount of seconds actually slept.
        """

        base_delay = choose_random_delay(self.min_seconds, self.max_seconds)
        enforced_delay = max(base_delay, float(robots_delay or 0))
        domain = domain_key(url)

        async with self._lock:
            last_hit = self._last_hit.get(domain)
            now = time.monotonic()

            if last_hit is None:
                wait_for = enforced_delay
            else:
                elapsed = now - last_hit
                wait_for = max(enforced_delay - elapsed, base_delay)

            ready_at = now + wait_for
            self._last_hit[domain] = ready_at

        if wait_for > 0:
            await asyncio.sleep(wait_for)
        return wait_for
