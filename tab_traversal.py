from __future__ import annotations

import copy
import logging
from dataclasses import dataclass, field
from textwrap import dedent
from typing import List, Optional, Tuple

from crawl4ai import AsyncWebCrawler, CrawlerRunConfig
from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError

from .config_loader import TabTraversalSettings


logger = logging.getLogger(__name__ + ".tabs")


@dataclass
class TabCapture:
    group_title: str
    tab_label: str
    html: str
    index: int
    group_index: int
    text_digest: str


@dataclass
class TabMarkdownBlock:
    group_title: str
    tab_label: str
    markdown_text: str
    index: int
    group_index: int
    links: List[str] = field(default_factory=list)


class TabTraversalManager:
    _LABEL_HINTS = [
        "pip",
        "uv",
        "conda",
        "npm",
        "yarn",
        "pnpm",
        "curl",
        "http",
        "wget",
        "bash",
        "powershell",
        "python",
    ]

    def __init__(self, settings: TabTraversalSettings) -> None:
        self._settings = settings

    @property
    def enabled(self) -> bool:
        return bool(self._settings.enabled)

    async def collect_markdown_blocks(
        self,
        crawler: AsyncWebCrawler,
        url: str,
        base_config: CrawlerRunConfig,
        session_id: Optional[str],
    ) -> List[TabMarkdownBlock]:
        if not self.enabled or not session_id:
            return []

        page = await self._get_session_page(crawler, session_id)
        if page is None:
            return []

        captures = await self._collect_tab_content(page)
        if not captures:
            return []

        blocks: List[TabMarkdownBlock] = []
        seen: set[str] = set()
        for capture in captures:
            digest_key = capture.group_title + capture.tab_label + capture.text_digest
            if digest_key in seen:
                continue
            seen.add(digest_key)

            markdown, links = await self._html_fragment_to_markdown(
                crawler, base_config, url, capture.html
            )
            if not markdown and not links:
                continue
            markdown = (markdown or "").strip()
            if not markdown and not links:
                continue

            blocks.append(
                TabMarkdownBlock(
                    group_title=capture.group_title,
                    tab_label=capture.tab_label,
                    markdown_text=markdown,
                    index=capture.index,
                    group_index=capture.group_index,
                    links=links,
                )
            )

        if blocks:
            logger.info("Captured %d tab blocks for %s", len(blocks), url)
        return blocks

    async def _html_fragment_to_markdown(
        self,
        crawler: AsyncWebCrawler,
        base_config: CrawlerRunConfig,
        url: str,
        html_fragment: str,
    ) -> Tuple[Optional[str], List[str]]:
        processing_config = copy.deepcopy(base_config)
        processing_config.session_id = None
        processing_config.js_code = None
        processing_config.js_only = False

        wrapped_html = self._wrap_fragment(html_fragment)
        try:
            result = await crawler.aprocess_html(
                url=url,
                html=wrapped_html,
                extracted_content=wrapped_html,
                config=processing_config,
                screenshot_data=None,
                pdf_data=None,
                verbose=False,
            )
        except Exception as exc:  # pragma: no cover
            logger.warning("Tab fragment conversion failed for %s: %s", url, exc)
            return None, []

        markdown = getattr(result, "markdown", None)
        links = self._collect_links(result)
        if not markdown:
            return None, links
        return markdown.raw_markdown, links
@@
    def _collect_links(self, result) -> List[str]:
        collected: List[str] = []
        link_groups = getattr(result, "links", {}) or {}
        for group in ("internal", "external"):
            for link in link_groups.get(group, []):
                href = (link.get("href") or "").strip()
                if href:
                    collected.append(href)
        return collected

    def _wrap_fragment(self, html_fragment: str) -> str:
        snippet = (html_fragment or "").strip()
        if not snippet:
            return ""
        lower = snippet.lower()
        if "<html" in lower and "</html" in lower:
            return snippet
        return f"<html><body>{snippet}</body></html>"

    def _heading_for(self, block: TabMarkdownBlock) -> str:
        template = self._settings.heading_template or "#### [Tab: {label}]"
        group = block.group_title or "Tabs"
        label = block.tab_label or "Tab"
        try:
            return template.format(
                group=group,
                label=label,
                index=block.index + 1,
                group_index=block.group_index + 1,
            ).strip()
        except Exception:
            return f"#### [Tab: {group} - {label}]"

    def format_block(self, block: TabMarkdownBlock) -> str:
        heading = self._heading_for(block)
        return f"{heading}\n\n{block.markdown_text.strip()}"

    async def _get_session_page(
        self, crawler: AsyncWebCrawler, session_id: str
    ) -> Optional[Page]:
        try:
            page, _ = await crawler.crawler_strategy.browser_manager.get_page(
                CrawlerRunConfig(session_id=session_id)
            )
            return page
        except Exception as exc:  # pragma: no cover
            logger.warning("Failed to attach to session %s: %s", session_id, exc)
            return None

    async def _collect_tab_content(self, page: Page) -> List[TabCapture]:
        captures: List[TabCapture] = []
        try:
            await page.wait_for_load_state(
                "networkidle", timeout=self._settings.wait_for_activation_ms
            )
        except Exception:
            pass
        await page.wait_for_timeout(600)

        tablists = page.locator('[role="tablist"]')
        try:
            groups = min(await tablists.count(), self._settings.max_groups)
        except Exception:
            return captures

        total_collected = 0
        for group_index in range(groups):
            tablist = tablists.nth(group_index)
            try:
                group_title = await tablist.evaluate(self._group_title_script())
            except Exception:
                group_title = "Tabs"
            group_title = (group_title or "Tabs").strip() or "Tabs"

            tabs = tablist.locator('[role="tab"]')
            try:
                tab_count = min(await tabs.count(), self._settings.max_tabs_per_group)
            except Exception:
                continue

            for tab_index in range(tab_count):
                if total_collected >= self._settings.max_total_tabs:
                    break
                tab = tabs.nth(tab_index)
                try:
                    label = (await tab.inner_text()).strip()
                except Exception:
                    label = ""
                if not label:
                    continue

                try:
                    await tab.click()
                    await page.wait_for_timeout(250)
                    await self._wait_for_activation(page, tab)
                    html = await self._extract_panel_html(page, tab)
                except PlaywrightTimeoutError:
                    logger.debug("Timed out while collecting tab %s", label)
                    continue
                except Exception as exc:  # pragma: no cover
                    logger.debug("Playwright error for tab %s: %s", label, exc)
                    continue

                if not html:
                    continue

                captures.append(
                    TabCapture(
                        group_title=group_title,
                        tab_label=label,
                        html=html,
                        index=tab_index,
                        group_index=group_index,
                        text_digest=self._digest_html(html),
                    )
                )
                total_collected += 1

            if total_collected >= self._settings.max_total_tabs:
                break

        return captures

    async def _wait_for_activation(self, page: Page, tab_locator) -> None:
        handle = await tab_locator.element_handle()
        if handle is None:
            raise PlaywrightTimeoutError("Tab handle missing")
        await page.wait_for_function(
            """
            tab => {
                if (!tab) return false;
                const selected = tab.getAttribute('aria-selected');
                const state = tab.dataset.state;
                return selected === 'true' || state === 'active';
            }
            """,
            arg=handle,
            timeout=self._settings.wait_for_activation_ms,
        )
        await page.wait_for_timeout(120)

    async def _extract_panel_html(self, page: Page, tab_locator) -> str:
        handle = await tab_locator.element_handle()
        if handle is None:
            return ""
        html = await page.evaluate(
            """
            tab => {
                if (!tab) return '';
                const controlId = tab.getAttribute('aria-controls');
                let panel = controlId ? document.getElementById(controlId) : null;
                if (!panel && tab.id) {
                    panel = document.querySelector('[aria-labelledby="' + tab.id + '"]');
                }
                if (!panel) {
                    const root = tab.closest('[data-component-part="code-group"], .code-group');
                    if (root) {
                        panel = root.querySelector('[role="tabpanel"]:not([hidden])');
                    }
                }
                return panel ? panel.innerHTML || '' : '';
            }
            """,
            handle,
        )
        return html or ""

    def _group_title_script(self) -> str:
        return dedent(
            """
            (el) => {
                if (!el) return 'Tabs';
                const normalize = (value) => (value || '').replace(/\\s+/g, ' ').trim();
                const aria = normalize(el.getAttribute('aria-label'));
                if (aria) return aria;
                let node = el;
                for (let depth = 0; depth < 4 && node; depth += 1) {
                    const prev = node.previousElementSibling;
                    if (prev && /^H[1-6]$/.test(prev.tagName)) {
                        return normalize(prev.textContent);
                    }
                    node = node.parentElement;
                }
                return 'Tabs';
            }
            """
        )

    def _digest_html(self, html: str) -> str:
        normalized = (html or "").strip().lower()
        if not normalized:
            return ""
        return str(abs(hash(normalized)))

    def merge_into_markdown(
        self, base_markdown: str, blocks: List[TabMarkdownBlock]
    ) -> str:
        text = (base_markdown or "").strip()
        grouped: dict[int, List[TabMarkdownBlock]] = {}
        for block in sorted(blocks, key=lambda b: (b.group_index, b.index)):
            grouped.setdefault(block.group_index, []).append(block)

        for group_blocks in grouped.values():
            anchor = next((b for b in group_blocks if b.index == 0), None)
            extras = [b for b in group_blocks if b.index > 0]
            if not extras:
                continue

            insertion = "\n\n".join(self.format_block(b) for b in extras)
            inserted = False
            if anchor:
                anchor_text = anchor.markdown_text.strip()
                if anchor_text:
                    pos = text.find(anchor_text)
                    if pos != -1:
                        insert_pos = pos + len(anchor_text)
                        text = (
                            text[:insert_pos]
                            + "\n\n"
                            + insertion
                            + text[insert_pos:]
                        )
                        inserted = True

            if not inserted:
                if text:
                    text = text.rstrip() + "\n\n" + insertion
                else:
                    text = insertion

        return text
