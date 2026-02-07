"""E-commerce site scraper using MoltBot/OpenClaw."""

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from string import Template
from typing import Any
from urllib.parse import urlparse

from moltbot_client import MoltBotClient, MoltBotConfig
from models import PaginationType, SecurityIssue, SiteAnalysis
from config import (
    MOLTBOT_AGENT_COMPLETION_TIMEOUT,
    MOLTBOT_AGENT_RETRY_TIMEOUT,
    MOLTBOT_AGENT_CONCURRENCY,
    PRODUCT_URL_PATTERNS,
    LISTING_URL_PATTERNS,
)

logger = logging.getLogger(__name__)

# Build pattern hint strings for the prompt
_PRODUCT_PATTERNS_HINT = ", ".join(PRODUCT_URL_PATTERNS[:10])
_LISTING_PATTERNS_HINT = ", ".join(LISTING_URL_PATTERNS[:10])

# Compiled regexes for URL validation
_PRODUCT_RE = [re.compile(p) for p in PRODUCT_URL_PATTERNS]
_LISTING_RE = [re.compile(p) for p in LISTING_URL_PATTERNS]

# URLs that should never appear in listing/product results
_JUNK_URL_PATTERNS = re.compile(
    r"(sitemap\.xml|robots\.txt|favicon\.ico|\.css|\.js(\?|$)|\.png|\.jpg|\.svg)",
    re.IGNORECASE,
)

# Phased strategic prompt using string.Template ($ substitution avoids JSON brace issues)
ANALYSIS_PROMPT = Template(r"""You are an expert web scraper agent. Analyze the e-commerce website at $url following these phases IN ORDER.

TOOL STRATEGY — IMPORTANT:
- Try **web_fetch** FIRST — it is faster and more reliable.
- Only use the **browser** tool if web_fetch fails to return useful content (e.g., returns empty or blocked).
- If browser also fails, do NOT keep retrying it — move on to alternate URLs.
- NEVER retry the same tool on the same URL more than once.

== PHASE 1: NAVIGATE ==
1. Use web_fetch to fetch https://www.$domain (use www prefix to avoid geo-redirects)
2. If that fails or returns a country selector, try web_fetch on https://$domain directly
3. Only fall back to the browser tool if web_fetch returns no usable content

== PHASE 2: HANDLE OBSTACLES ==
If the page did NOT load normally, try these fixes:

- **Geo-redirect / country selector**: Try https://www.$domain instead of bare $domain. If using browser, click "United States" or select the US region.
- **Cookie consent banner**: If using browser, click "Accept All".
- **CAPTCHA / Cloudflare / Access Denied**: Do NOT keep retrying the same URL. Instead, immediately try:
  1. web_fetch https://$domain/sitemap.xml
  2. web_fetch https://$domain/robots.txt
  3. web_fetch https://www.$domain/shop or /products or /collections

IMPORTANT: If a site blocks you, move on quickly. Do NOT make more than 3 total web_fetch attempts on a blocked site.

== PHASE 3: DEEP NAVIGATION ==
Do NOT just scrape the homepage. Go deeper:
1. From the homepage content, identify category/department links in the navigation
2. Use web_fetch on at least ONE category page (e.g., /shop, /products, /collections, /categories, or any department path you found)
3. Collect links from BOTH pages

== PHASE 4: EXTRACT DATA ==
From all pages you visited, collect:

1. **Listing/category URLs** — URLs matching patterns like: $listing_patterns
   Do NOT include: the bare homepage, sitemap.xml, robots.txt, or non-page resources
2. **Product detail URLs** — URLs matching patterns like: $product_patterns
3. **Pagination type** — What method does the site use?
   - "next_page_link" — Traditional next/previous links
   - "infinite_scroll" — Content loads on scroll
   - "load_more_button" — "Load more" / "Show more" button
   - "numbered_pages" — Numbered page links (1, 2, 3...)
   - "none" — No pagination visible
4. **Product count** — Look for text like "X products", "X items", "Showing 1-20 of X", "X results"
5. **Page count** — Look for "Page X of Y" or max pagination number
6. **Security issues** — Report any you encountered: "cloudflare", "captcha", "bot_protection", "blocked", or empty list if none
7. **Page title** — The <title> of the main page

== PHASE 5: RETURN JSON ==
Return your findings as a single JSON object. Do NOT wrap it in markdown code fences. Use 0 for unknown numbers, not null.

{
  "is_ecommerce": true,
  "listing_urls": ["https://www.example.com/category/shoes"],
  "product_urls": ["https://www.example.com/product/shoe-123"],
  "has_product_pages": true,
  "pagination_type": "numbered_pages",
  "estimated_total_products": 1500,
  "estimated_total_pages": 75,
  "security_issues": [],
  "page_title": "Example Store",
  "pages_visited": ["https://www.example.com", "https://www.example.com/shop"],
  "error": null
}

CRITICAL RULES:
- Include up to 10 listing URLs and up to 10 product URLs (real URLs you found, not examples).
- If you were blocked, still return the JSON with security_issues filled in and empty URL lists.
- Do NOT return null for numeric fields — use 0 instead.
- Do NOT include bare homepages (e.g., "https://www.example.com/") as listing URLs.
- Do NOT include sitemap.xml or robots.txt as listing URLs.
- Stop making requests after 3 consecutive failures on the same site.
""")

RETRY_PROMPT = Template(r"""The first attempt to scrape $url failed. Issues encountered: $issues.

Use ONLY web_fetch (do NOT use browser tool — it is not working). Try these in order, stop after the first one that returns useful content:

1. web_fetch https://$domain/sitemap.xml — look for product and category URLs in the XML
2. web_fetch https://www.$domain/shop
3. web_fetch https://www.$domain/products
4. web_fetch https://www.$domain/collections
5. web_fetch https://$domain/robots.txt — look for Sitemap: or Disallow: patterns that reveal URL structure

From whatever you can access, extract listing and product URLs. Stop after 3 total attempts if everything is blocked.

Return the same JSON format. Use 0 for unknown numbers, not null:
{
  "is_ecommerce": true,
  "listing_urls": [],
  "product_urls": [],
  "has_product_pages": false,
  "pagination_type": "none",
  "estimated_total_products": 0,
  "estimated_total_pages": 0,
  "security_issues": [],
  "page_title": "",
  "pages_visited": [],
  "error": null
}
""")


def _extract_json_object(text: str) -> dict | None:
    """Extract the first top-level JSON object from text using brace counting.

    Handles nested objects and quoted strings correctly, unlike simple regex.
    """
    # First try code fences
    fence_match = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if fence_match:
        try:
            return json.loads(fence_match.group(1))
        except json.JSONDecodeError:
            pass

    # Brace-counting parser
    start = text.find("{")
    if start == -1:
        return None

    depth = 0
    in_string = False
    escape_next = False
    i = start

    while i < len(text):
        ch = text[i]

        if escape_next:
            escape_next = False
            i += 1
            continue

        if ch == "\\":
            escape_next = True
            i += 1
            continue

        if ch == '"' and not escape_next:
            in_string = not in_string
            i += 1
            continue

        if not in_string:
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start:i + 1]
                    try:
                        return json.loads(candidate)
                    except json.JSONDecodeError:
                        # Skip this brace and try to find next object
                        start = text.find("{", i + 1)
                        if start == -1:
                            return None
                        i = start
                        depth = 0
                        continue

        i += 1

    return None


def _is_junk_url(url: str, site_domain: str) -> bool:
    """Check if a URL is junk (not a real listing/product page)."""
    if not url or not isinstance(url, str):
        return True
    if _JUNK_URL_PATTERNS.search(url):
        return True
    # Bare homepage (with or without trailing slash)
    parsed = urlparse(url)
    if parsed.path in ("", "/"):
        return True
    return False


def _matches_patterns(url: str, compiled_patterns: list[re.Pattern]) -> bool:
    """Check if a URL matches any of the compiled patterns."""
    for pattern in compiled_patterns:
        if pattern.search(url):
            return True
    return False


def _filter_urls(urls: list, site_domain: str, patterns: list[re.Pattern]) -> list[str]:
    """Filter URLs: remove junk, validate against known patterns, cap at 10."""
    if not urls:
        return []
    seen = set()
    result = []
    for url in urls:
        if not isinstance(url, str) or not url.strip():
            continue
        url = url.strip()
        if url in seen:
            continue
        seen.add(url)
        if _is_junk_url(url, site_domain):
            continue
        # Keep URL if it matches known patterns OR if it's from the target domain
        # (agent may find valid URLs with patterns we don't have)
        parsed = urlparse(url)
        url_domain = parsed.netloc.lower().lstrip("www.")
        clean_site = site_domain.lower().lstrip("www.")
        if url_domain and clean_site not in url_domain:
            continue  # Skip URLs from other domains
        result.append(url)
        if len(result) >= 10:
            break
    return result


@dataclass
class MoltBotScraper:
    """Scraper that uses MoltBot for browser automation."""

    config: MoltBotConfig = field(default_factory=MoltBotConfig)
    client: MoltBotClient | None = None

    async def __aenter__(self):
        self.client = MoltBotClient(self.config)
        await self.client.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.client:
            await self.client.disconnect()

    async def analyze_site(self, url: str) -> SiteAnalysis:
        """Analyze a single e-commerce site using MoltBot."""
        if not url.startswith(("http://", "https://")):
            url = f"https://{url}"

        parsed = urlparse(url)
        domain = parsed.netloc
        # Add www. prefix if bare domain (helps with geo-redirects like bestbuy)
        if not domain.startswith("www.") and domain.count(".") == 1:
            url = f"https://www.{domain}{parsed.path or '/'}"

        import uuid
        session_key = f"agent:main:scraper-{uuid.uuid4().hex[:8]}"

        try:
            prompt = ANALYSIS_PROMPT.substitute(
                url=url,
                domain=domain,
                listing_patterns=_LISTING_PATTERNS_HINT,
                product_patterns=_PRODUCT_PATTERNS_HINT,
            )

            start_time = time.monotonic()
            result = await self.client.invoke_agent(
                prompt=prompt,
                tools=["agent-browser", "playwright-cli"],
                session_key=session_key,
                completion_timeout=MOLTBOT_AGENT_COMPLETION_TIMEOUT,
            )
            elapsed = time.monotonic() - start_time

            analysis = self._parse_agent_response(url, domain, result)
            analysis.load_time_seconds = elapsed

            # Retry if blocked/errored with no useful data
            if self._should_retry(analysis):
                logger.info("Retrying %s — initial attempt was blocked", domain)
                retry_start = time.monotonic()
                retry_analysis = await self._retry_site(url, domain, session_key, analysis)
                retry_analysis.load_time_seconds = time.monotonic() - retry_start
                analysis = self._merge_analyses(analysis, retry_analysis)
                analysis.load_time_seconds = elapsed + retry_analysis.load_time_seconds

            return analysis

        except TimeoutError:
            return SiteAnalysis(
                url=url,
                domain=domain,
                error_message="MoltBot agent timeout",
                security_issues=[SecurityIssue.TIMEOUT],
            )
        except Exception as e:
            return SiteAnalysis(
                url=url,
                domain=domain,
                error_message=str(e),
            )

    async def _retry_site(self, url: str, domain: str, session_key: str,
                          first_analysis: SiteAnalysis) -> SiteAnalysis:
        """Send a follow-up prompt on the same session to try alternate strategies."""
        issues_str = ", ".join(s.value for s in first_analysis.security_issues) or "unknown blocking"
        if first_analysis.error_message:
            issues_str += f"; {first_analysis.error_message[:100]}"

        prompt = RETRY_PROMPT.substitute(
            url=url,
            domain=domain,
            issues=issues_str,
        )

        try:
            result = await self.client.invoke_agent(
                prompt=prompt,
                tools=["agent-browser", "playwright-cli"],
                session_key=session_key,
                completion_timeout=MOLTBOT_AGENT_RETRY_TIMEOUT,
            )
            return self._parse_agent_response(url, domain, result)
        except (TimeoutError, Exception) as e:
            logger.warning("Retry for %s failed: %s", domain, e)
            return SiteAnalysis(
                url=url,
                domain=domain,
                error_message=f"Retry failed: {e}",
            )

    @staticmethod
    def _should_retry(analysis: SiteAnalysis) -> bool:
        """Check if a site should be retried — blocked/failed with no useful data."""
        has_security_issues = bool(
            analysis.security_issues
            and any(s != SecurityIssue.NONE for s in analysis.security_issues)
        )
        has_error = bool(analysis.error_message)
        has_no_data = (
            not analysis.listing_urls
            and not analysis.product_urls
        )
        return has_no_data and (has_security_issues or has_error)

    @staticmethod
    def _merge_analyses(first: SiteAnalysis, retry: SiteAnalysis) -> SiteAnalysis:
        """Merge two analysis results, preferring whichever has more data."""
        first_score = len(first.listing_urls) + len(first.product_urls)
        retry_score = len(retry.listing_urls) + len(retry.product_urls)

        best = retry if retry_score > first_score else first

        # Merge security issues from both attempts
        all_issues = set(first.security_issues) | set(retry.security_issues)
        all_issues.discard(SecurityIssue.NONE)
        best.security_issues = list(all_issues) if all_issues else []

        # Keep the better error message (or clear it if retry succeeded)
        if retry_score > 0:
            best.error_message = None

        return best

    def _parse_agent_response(self, url: str, domain: str, result: Any) -> SiteAnalysis:
        """Parse MoltBot agent response into SiteAnalysis."""
        try:
            # Extract text content from agent result
            if isinstance(result, dict):
                if result.get("error"):
                    return SiteAnalysis(
                        url=url,
                        domain=domain,
                        error_message=result["error"],
                    )
                if result.get("status") == "timeout":
                    return SiteAnalysis(
                        url=url,
                        domain=domain,
                        error_message="Agent timed out",
                        security_issues=[SecurityIssue.TIMEOUT],
                    )
                response_text = result.get("content", "")
            else:
                response_text = str(result)

            if not isinstance(response_text, str):
                response_text = str(response_text)

            response_text = response_text.strip()

            if not response_text:
                return SiteAnalysis(
                    url=url,
                    domain=domain,
                    error_message="Agent returned empty response",
                )

            # Use brace-counting JSON extractor
            data = _extract_json_object(response_text)
            if data is None:
                data = json.loads(response_text)

            # Map pagination type
            pagination_map = {
                "next_page_link": PaginationType.NEXT_PAGE,
                "infinite_scroll": PaginationType.INFINITE_SCROLL,
                "load_more_button": PaginationType.LOAD_MORE,
                "numbered_pages": PaginationType.NUMBERED,
                "none": PaginationType.NONE,
            }
            pagination = pagination_map.get(
                data.get("pagination_type", "unknown"),
                PaginationType.UNKNOWN
            )

            # Map security issues
            security_map = {
                "cloudflare": SecurityIssue.CLOUDFLARE,
                "captcha": SecurityIssue.CAPTCHA,
                "bot_protection": SecurityIssue.BOT_PROTECTION,
                "blocked": SecurityIssue.BLOCKED,
                "timeout": SecurityIssue.TIMEOUT,
            }
            security_issues = [
                security_map.get(s, SecurityIssue.NONE)
                for s in data.get("security_issues", [])
                if s in security_map
            ]

            # Filter and validate URLs
            listing_urls = _filter_urls(
                data.get("listing_urls") or [], domain, _LISTING_RE
            )
            product_urls = _filter_urls(
                data.get("product_urls") or [], domain, _PRODUCT_RE
            )

            # Override has_product_pages based on actual data
            has_product_pages = bool(product_urls) or bool(data.get("has_product_pages"))

            return SiteAnalysis(
                url=url,
                domain=domain,
                is_ecommerce=bool(data.get("is_ecommerce")),
                listing_urls=listing_urls,
                product_urls=product_urls,
                has_product_pages=has_product_pages,
                estimated_total_pages=data.get("estimated_total_pages") or 0,
                estimated_total_products=data.get("estimated_total_products") or 0,
                pagination_type=pagination,
                security_issues=security_issues,
                page_title=data.get("page_title") or "",
                error_message=data.get("error"),
            )

        except (json.JSONDecodeError, KeyError, TypeError) as e:
            return SiteAnalysis(
                url=url,
                domain=domain,
                error_message=f"Failed to parse agent response: {e}",
            )

    async def analyze_sites(self, urls: list[str],
                            concurrency: int = MOLTBOT_AGENT_CONCURRENCY) -> list[SiteAnalysis]:
        """Analyze multiple sites with controlled concurrency."""
        semaphore = asyncio.Semaphore(concurrency)

        async def analyze_with_semaphore(url: str) -> SiteAnalysis:
            async with semaphore:
                return await self.analyze_site(url)

        tasks = [analyze_with_semaphore(url) for url in urls]
        return await asyncio.gather(*tasks)


async def check_moltbot_connection(config: MoltBotConfig | None = None) -> tuple[bool, str | None]:
    """Check if MoltBot Gateway is running and accessible.

    Returns (success, error_message).
    """
    try:
        client = MoltBotClient(config)
        await client.connect()
        await client.health()
        await client.disconnect()
        return True, None
    except Exception as e:
        import traceback
        error_detail = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
        return False, error_detail
