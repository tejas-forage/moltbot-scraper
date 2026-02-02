"""Analyze page content to extract e-commerce information."""

import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from config import (
    LISTING_URL_PATTERNS,
    PAGINATION_SELECTORS,
    PRODUCT_URL_PATTERNS,
    SECURITY_INDICATORS,
)
from models import PaginationType, SecurityIssue


class PageAnalyzer:
    """Analyze a webpage for e-commerce characteristics."""

    def __init__(self, url: str, html: str, page_source: str = ""):
        self.url = url
        self.domain = urlparse(url).netloc
        self.html = html
        self.page_source = page_source or html
        self.soup = BeautifulSoup(html, "lxml")

    def get_all_links(self) -> list[str]:
        """Extract all links from the page."""
        links = []
        for a in self.soup.find_all("a", href=True):
            href = a["href"]
            full_url = urljoin(self.url, href)
            if urlparse(full_url).netloc == self.domain:
                links.append(full_url)
        return list(set(links))

    def find_listing_urls(self) -> list[str]:
        """Find URLs that look like listing/category pages."""
        all_links = self.get_all_links()
        listing_urls = []

        for link in all_links:
            for pattern in LISTING_URL_PATTERNS:
                if re.search(pattern, link, re.IGNORECASE):
                    listing_urls.append(link)
                    break

        return list(set(listing_urls))

    def find_product_urls(self) -> list[str]:
        """Find URLs that look like product detail pages."""
        all_links = self.get_all_links()
        product_urls = []

        for link in all_links:
            for pattern in PRODUCT_URL_PATTERNS:
                if re.search(pattern, link, re.IGNORECASE):
                    product_urls.append(link)
                    break

        return list(set(product_urls))

    def detect_pagination_type(self) -> PaginationType:
        """Detect the type of pagination used on the page."""
        html_lower = self.html.lower()

        # Check for infinite scroll indicators
        for selector in PAGINATION_SELECTORS["infinite_scroll"]:
            clean_selector = selector.replace("[", "").replace("]", "").replace("*", "")
            if clean_selector in html_lower:
                return PaginationType.INFINITE_SCROLL

        # Check for load more buttons
        for selector in PAGINATION_SELECTORS["load_more"]:
            if "load more" in html_lower or "load-more" in html_lower:
                return PaginationType.LOAD_MORE

        # Check for next page links
        for selector in PAGINATION_SELECTORS["next_button"]:
            try:
                if ":contains" in selector:
                    continue
                element = self.soup.select_one(selector)
                if element:
                    return PaginationType.NEXT_PAGE
            except Exception:
                continue

        # Check for numbered pagination
        pagination_el = self.soup.select_one(".pagination, [class*='pagination'], nav[aria-label*='pagination']")
        if pagination_el:
            page_links = pagination_el.find_all("a")
            if any(link.text.strip().isdigit() for link in page_links):
                return PaginationType.NUMBERED

        # Check URL patterns for page numbers
        if re.search(r"[?&](page|p)=\d+", self.url):
            return PaginationType.NEXT_PAGE

        return PaginationType.UNKNOWN

    def detect_security_issues(self) -> list[SecurityIssue]:
        """Detect security measures that might block scraping."""
        issues = []
        html_lower = self.html.lower()
        source_lower = self.page_source.lower()
        combined = html_lower + source_lower

        # Check for Cloudflare
        for indicator in SECURITY_INDICATORS["cloudflare"]:
            if indicator in combined:
                issues.append(SecurityIssue.CLOUDFLARE)
                break

        # Check for CAPTCHA
        for indicator in SECURITY_INDICATORS["captcha"]:
            if indicator in combined:
                issues.append(SecurityIssue.CAPTCHA)
                break

        # Check for bot protection
        for indicator in SECURITY_INDICATORS["bot_protection"]:
            if indicator in combined:
                issues.append(SecurityIssue.BOT_PROTECTION)
                break

        return list(set(issues))

    def is_ecommerce_site(self) -> bool:
        """Determine if this appears to be an e-commerce site."""
        indicators = 0

        # Check for product URLs
        if self.find_product_urls():
            indicators += 2

        # Check for listing URLs
        if self.find_listing_urls():
            indicators += 1

        # Check for common e-commerce elements
        ecommerce_keywords = [
            "add to cart",
            "add to bag",
            "buy now",
            "checkout",
            "shopping cart",
            "price",
            "add-to-cart",
            "product",
            "shop",
            "$",
            "€",
            "£",
        ]

        html_lower = self.html.lower()
        for keyword in ecommerce_keywords:
            if keyword in html_lower:
                indicators += 0.5

        # Check for price patterns
        if re.search(r"[\$€£]\s*\d+[.,]\d{2}", self.html):
            indicators += 1

        return indicators >= 2

    def estimate_product_count(self) -> int:
        """Estimate the number of products on the site."""
        # Look for product count in text
        patterns = [
            r"(\d+)\s*products?",
            r"(\d+)\s*items?",
            r"(\d+)\s*results?",
            r"showing\s*\d+\s*-\s*\d+\s*of\s*(\d+)",
            r"(\d+)\s*total",
        ]

        for pattern in patterns:
            match = re.search(pattern, self.html, re.IGNORECASE)
            if match:
                try:
                    count = int(match.group(1).replace(",", ""))
                    if count > 0 and count < 1000000:
                        return count
                except ValueError:
                    continue

        # Fallback: count product-like elements
        product_urls = self.find_product_urls()
        if product_urls:
            return len(product_urls) * 10  # Rough estimate

        return 0

    def estimate_page_count(self) -> int:
        """Estimate total number of pages."""
        # Look for page count in pagination
        pagination = self.soup.select_one(".pagination, [class*='pagination']")
        if pagination:
            page_numbers = re.findall(r"\b(\d+)\b", pagination.get_text())
            if page_numbers:
                try:
                    return max(int(n) for n in page_numbers if int(n) < 10000)
                except ValueError:
                    pass

        # Look for "page X of Y" patterns
        match = re.search(r"page\s*\d+\s*of\s*(\d+)", self.html, re.IGNORECASE)
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                pass

        # Estimate from product count
        product_count = self.estimate_product_count()
        if product_count > 0:
            return max(1, product_count // 20)  # Assume ~20 products per page

        return 0

    def get_page_title(self) -> str:
        """Get the page title."""
        title = self.soup.find("title")
        return title.get_text().strip() if title else ""
