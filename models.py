"""Data models for site analysis."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class PaginationType(str, Enum):
    NEXT_PAGE = "next_page_link"
    INFINITE_SCROLL = "infinite_scroll"
    LOAD_MORE = "load_more_button"
    NUMBERED = "numbered_pages"
    NONE = "none"
    UNKNOWN = "unknown"


class SecurityIssue(str, Enum):
    CLOUDFLARE = "cloudflare"
    CAPTCHA = "captcha"
    BOT_PROTECTION = "bot_protection"
    BLOCKED = "blocked"
    TIMEOUT = "timeout"
    NONE = "none"


@dataclass
class SiteAnalysis:
    """Analysis result for a single e-commerce site."""

    url: str
    domain: str
    is_ecommerce: bool = False

    # URLs found
    listing_urls: list[str] = field(default_factory=list)
    product_urls: list[str] = field(default_factory=list)
    has_product_pages: bool = False

    # Estimates
    estimated_total_pages: int = 0
    estimated_total_products: int = 0

    # Pagination
    pagination_type: PaginationType = PaginationType.UNKNOWN

    # Issues
    security_issues: list[SecurityIssue] = field(default_factory=list)
    error_message: Optional[str] = None

    # Metadata
    page_title: str = ""
    load_time_seconds: float = 0.0

    def to_dict(self) -> dict:
        """Convert to dictionary for export."""
        return {
            "url": self.url,
            "domain": self.domain,
            "is_ecommerce": self.is_ecommerce,
            "listing_urls_count": len(self.listing_urls),
            "listing_urls_sample": self.listing_urls[:5],
            "product_urls_count": len(self.product_urls),
            "product_urls_sample": self.product_urls[:5],
            "has_product_pages": self.has_product_pages,
            "estimated_total_pages": self.estimated_total_pages,
            "estimated_total_products": self.estimated_total_products,
            "pagination_type": self.pagination_type.value,
            "security_issues": [s.value for s in self.security_issues],
            "error_message": self.error_message,
            "page_title": self.page_title,
            "load_time_seconds": round(self.load_time_seconds, 2),
        }
