from __future__ import annotations

import hashlib
import posixpath
import random
import re
from pathlib import Path
from typing import List, Optional
from urllib.parse import urljoin, urlparse, urlunparse


def _normalized_path(path: str) -> str:
    if not path:
        return "/"
    # Ensure path starts with slash so normpath keeps hierarchy
    if not path.startswith("/"):
        path = f"/{path}"
    normalized = posixpath.normpath(path)
    if path.endswith("/") and not normalized.endswith("/"):
        normalized = f"{normalized}/"
    if not normalized.startswith("/"):
        normalized = f"/{normalized}"
    return normalized or "/"


def normalize_url(url: str, base_url: Optional[str] = None) -> Optional[str]:
    """
    Normalize a URL and optionally resolve it against ``base_url``.

    Removes URL fragments and ensures scheme + netloc exist.
    """

    if not url:
        return None

    parsed = urlparse(url)
    if not parsed.scheme:
        if not base_url:
            return None
        parsed = urlparse(urljoin(base_url, url))

    if parsed.scheme.lower() not in {"http", "https"}:
        return None

    fragmentless = parsed._replace(fragment="")
    normalized = urlunparse(
        (
            fragmentless.scheme.lower(),
            fragmentless.netloc.lower(),
            _normalized_path(fragmentless.path),
            "",
            fragmentless.query,
            "",
        )
    )
    return normalized


def derive_parent_url(url: str) -> str:
    """Compute the parent directory URL used for filtering child links."""

    parsed = urlparse(url)
    path = _normalized_path(parsed.path) or "/"
    parent_path = posixpath.dirname(path.rstrip("/"))
    if not parent_path.startswith("/"):
        parent_path = f"/{parent_path}"
    if parent_path != "/":
        parent_path = f"{parent_path}/"

    result = urlunparse(
        (parsed.scheme.lower(), parsed.netloc.lower(), parent_path or "/", "", "", "")
    )
    return result


def shares_same_parent(url: str, parent_url: str) -> bool:
    """Return True when ``url`` lives under ``parent_url``."""

    if not parent_url:
        return True
    return derive_parent_url(url) == parent_url


def is_within_scope(url: str, scope_url: str) -> bool:
    """Check whether ``url`` belongs to the subtree defined by ``scope_url``."""

    if not scope_url:
        return True

    candidate = urlparse(url)
    scope = urlparse(scope_url)
    if not candidate.scheme or not candidate.netloc:
        return False
    if candidate.scheme.lower() != scope.scheme.lower():
        return False
    if candidate.netloc.lower() != scope.netloc.lower():
        return False

    candidate_path = _normalized_path(candidate.path)
    scope_path = _normalized_path(scope.path)
    if scope_path == "/":
        return True
    if scope_path.endswith("/"):
        return candidate_path.startswith(scope_path)
    return candidate_path == scope_path or candidate_path.startswith(f"{scope_path}/")


def domain_key(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"


_SANITIZE_RE = re.compile(r"[^a-zA-Z0-9_.-]+")


def _slugify(segment: str) -> str:
    segment = _SANITIZE_RE.sub("-", segment.strip().lower())
    segment = segment.strip("-._")
    return segment or "index"


def build_markdown_path(base_dir: Path, url: str) -> Path:
    """
    Convert a URL into a deterministic markdown file path rooted at ``base_dir``.
    """

    parsed = urlparse(url)
    parts: List[str] = [parsed.netloc.lower()]
    path_segments = [seg for seg in parsed.path.split("/") if seg]
    if not path_segments:
        path_segments = ["index"]
    if len(path_segments) == 1:
        directory_segments: List[str] = parts
    else:
        directory_segments = parts + path_segments[:-1]

    directory = base_dir
    for segment in directory_segments:
        directory /= _slugify(segment)
    directory.mkdir(parents=True, exist_ok=True)

    filename = _slugify(path_segments[-1])
    if parsed.query:
        digest = hashlib.md5(parsed.query.encode("utf-8")).hexdigest()[:8]
        filename = f"{filename}-{digest}"
    return directory / f"{filename}.md"


def choose_random_delay(min_seconds: float, max_seconds: float) -> float:
    """Helper used by throttling logic to pick a random wait time."""

    if max_seconds < min_seconds:
        max_seconds = min_seconds
    return random.uniform(min_seconds, max_seconds)
