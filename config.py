"""Configuration for the e-commerce scraper."""

from pathlib import Path

# MoltBot Gateway settings
MOLTBOT_GATEWAY_URL = "ws://127.0.0.1:18789"
MOLTBOT_AUTH_TOKEN = "8613693b9f217bc8e7e8b72c8eab3196800c630042f5f7b8"

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
    r"/itm/",          # eBay
    r"/ip/",           # Walmart
    r"/p/[^/]+/\d+",   # Home Depot, generic with ID
    r"/dp/",           # Amazon
    r"/gp/product/",   # Amazon
    r"/pd/",
    r"/detail/",
    r"/goods/",
    r"-p-\d+",
    r"/sku/",
    r"/listing/",      # Etsy
    r"/buy/",
    r"/A-\d+",         # Target product IDs
    r"/-/p/A-",        # Target product pages
    r"/site/[^/]+/\d+",  # BestBuy product pages
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
    r"/s\?",           # Amazon search
    r"/s/",            # Generic search path
    r"/b\?",           # Amazon browse
    r"/b/",            # Generic browse path
    r"/gp/browse",     # Amazon
    r"/departments/",
    r"/dept/",
    r"/pl/",           # Lowe's
    r"/N-",            # Target faceted nav
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
        "captcha-delivery",
        "validate-form",
        "type the characters",
        "enter the characters",
        "verify you are a human",
    ],
    "bot_protection": [
        "datadome",
        "perimeterx",
        "imperva",
        "akamai",
        "distil",
        "kasada",
        "shape security",
        "bot-protection",
        "are you a robot",
        "automated access",
        "access denied",
        "robot or human",
    ],
}
