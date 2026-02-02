"""E-commerce site scraper using MoltBot/OpenClaw."""

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Any

from moltbot_client import MoltBotClient, MoltBotConfig
from models import PaginationType, SecurityIssue, SiteAnalysis


# Prompt template for MoltBot agent
ANALYSIS_PROMPT = """Analyze the e-commerce website at {url} and extract the following information.

Use the browser tool to:
1. Navigate to {url}
2. Wait for the page to fully load
3. Scroll down to trigger lazy loading
4. Extract all links from the page

Then analyze and report:

1. **Is E-commerce**: Does this appear to be an e-commerce/shopping site? (true/false)
2. **Listing Page URLs**: Find URLs that look like category/collection/search pages (contain /category/, /collection/, /shop/, /browse/, /c/, etc.)
3. **Product Page URLs**: Find URLs that look like product detail pages (contain /product/, /item/, /p/, /dp/, /detail/, etc.)
4. **Has Product Pages**: Are there product detail page URLs? (true/false)
5. **Pagination Type**: What pagination method is used?
   - "next_page_link" - Traditional next/previous page links
   - "infinite_scroll" - Content loads on scroll
   - "load_more_button" - "Load more" or "Show more" button
   - "numbered_pages" - Numbered page links (1, 2, 3...)
   - "none" - No pagination visible
6. **Estimated Total Products**: Look for text like "X products", "X items", "Showing 1-20 of X"
7. **Estimated Total Pages**: Look for pagination numbers or "Page X of Y"
8. **Security Issues**: Check for:
   - "cloudflare" - Cloudflare protection
   - "captcha" - CAPTCHA challenge
   - "bot_protection" - Other bot protection (DataDome, PerimeterX, etc.)
   - "blocked" - Access denied or 403 error
   - "none" - No issues detected

Return the results as JSON in this exact format:
```json
{{
  "is_ecommerce": true/false,
  "listing_urls": ["url1", "url2", ...],
  "product_urls": ["url1", "url2", ...],
  "has_product_pages": true/false,
  "pagination_type": "next_page_link|infinite_scroll|load_more_button|numbered_pages|none",
  "estimated_total_products": number or 0,
  "estimated_total_pages": number or 0,
  "security_issues": ["cloudflare", "captcha", ...] or [],
  "page_title": "Page Title",
  "error": null or "error message"
}}
```

If you encounter any errors, include them in the "error" field.
"""


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

        from urllib.parse import urlparse
        domain = urlparse(url).netloc

        try:
            # Send analysis request to MoltBot agent
            prompt = ANALYSIS_PROMPT.format(url=url)
            result = await self.client.invoke_agent(
                prompt=prompt,
                tools=["agent-browser", "playwright-cli"],  # Request browser tools
            )

            # Parse the agent's response
            return self._parse_agent_response(url, domain, result)

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

    def _parse_agent_response(self, url: str, domain: str, result: Any) -> SiteAnalysis:
        """Parse MoltBot agent response into SiteAnalysis."""
        try:
            # Extract JSON from agent response
            response_text = str(result)
            json_match = re.search(r"```json\s*(.*?)\s*```", response_text, re.DOTALL)

            if json_match:
                data = json.loads(json_match.group(1))
            else:
                # Try to parse entire response as JSON
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

            return SiteAnalysis(
                url=url,
                domain=domain,
                is_ecommerce=data.get("is_ecommerce", False),
                listing_urls=data.get("listing_urls", [])[:10],
                product_urls=data.get("product_urls", [])[:10],
                has_product_pages=data.get("has_product_pages", False),
                estimated_total_pages=data.get("estimated_total_pages", 0),
                estimated_total_products=data.get("estimated_total_products", 0),
                pagination_type=pagination,
                security_issues=security_issues,
                page_title=data.get("page_title", ""),
                error_message=data.get("error"),
            )

        except (json.JSONDecodeError, KeyError, TypeError) as e:
            return SiteAnalysis(
                url=url,
                domain=domain,
                error_message=f"Failed to parse agent response: {e}",
            )

    async def analyze_sites(self, urls: list[str], concurrency: int = 3) -> list[SiteAnalysis]:
        """Analyze multiple sites with controlled concurrency."""
        semaphore = asyncio.Semaphore(concurrency)

        async def analyze_with_semaphore(url: str) -> SiteAnalysis:
            async with semaphore:
                return await self.analyze_site(url)

        tasks = [analyze_with_semaphore(url) for url in urls]
        return await asyncio.gather(*tasks)


async def check_moltbot_connection() -> bool:
    """Check if MoltBot Gateway is running and accessible."""
    try:
        async with MoltBotClient() as client:
            await client.health()
            return True
    except Exception:
        return False
