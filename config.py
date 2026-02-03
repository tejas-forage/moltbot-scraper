"""Configuration for the e-commerce scraper."""

from pathlib import Path

# MoltBot Gateway settings
MOLTBOT_GATEWAY_URL = "ws://127.0.0.1:18789"
MOLTBOT_AUTH_TOKEN = "6ef8746eb7b4061271da65b9273f61b0aac1ec17bd6f0edd"

# Paths
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"

# Create directories
DATA_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

# Browser settings
BROWSER_HEADLESS = True
BROWSER_TIMEOUT = 30000  # 30 seconds
PAGE_LOAD_TIMEOUT = 60000  # 60 seconds

# Scraping settings
MAX_CONCURRENT = 5  # Max concurrent browser tabs
RETRY_ATTEMPTS = 2
DELAY_BETWEEN_REQUESTS = 1.0  # seconds

# Detection patterns
PRODUCT_URL_PATTERNS = [
    r"/product/",
    r"/products/",
    r"/item/",
    r"/p/",
    r"/dp/",
    r"/pd/",
    r"/detail/",
    r"/goods/",
    r"-p-\d+",
    r"/sku/",
]

LISTING_URL_PATTERNS = [
    r"/category/",
    r"/categories/",
    r"/collection/",
    r"/collections/",
    r"/shop/",
    r"/catalog/",
    r"/browse/",
    r"/search",
    r"/c/",
    r"/l/",
]

PAGINATION_SELECTORS = {
    "next_button": [
        "a[rel='next']",
        "a.next",
        "a.pagination-next",
        "button.next",
        "[aria-label='Next']",
        "[aria-label='Next page']",
        ".pagination a:contains('Next')",
        ".pagination a:contains('>')",
        "a[href*='page=']",
        "a[href*='p=']",
    ],
    "infinite_scroll": [
        "[data-infinite-scroll]",
        ".infinite-scroll",
        "[class*='infinite']",
        "[class*='lazy-load']",
    ],
    "load_more": [
        "button:contains('Load more')",
        "button:contains('Show more')",
        "a:contains('Load more')",
        ".load-more",
        "[class*='load-more']",
    ],
}

SECURITY_INDICATORS = {
    "cloudflare": [
        "cf-browser-verification",
        "cf_clearance",
        "cloudflare",
        "__cf_bm",
    ],
    "captcha": [
        "captcha",
        "recaptcha",
        "hcaptcha",
        "g-recaptcha",
        "h-captcha",
    ],
    "bot_protection": [
        "datadome",
        "perimeterx",
        "imperva",
        "akamai",
        "distil",
    ],
}
