# moltbot-scraper

E-commerce site analyzer powered by **MoltBot/OpenClaw** AI assistant.

## Features

Analyzes e-commerce websites and extracts:
- **Listing page URLs** - Category, collection, search pages
- **Product page URLs** - Detail pages for individual products
- **Estimated total pages** - Based on pagination analysis
- **Estimated total products** - Extracted from page content
- **Pagination type** - Infinite scroll, next page links, load more buttons
- **Security issues** - Cloudflare, CAPTCHA, bot protection detection

## Prerequisites

### MoltBot/OpenClaw (Required)

```bash
# Install MoltBot (requires Node.js >= 22)
npm install -g openclaw@latest

# Setup and start gateway
openclaw onboard --install-daemon
openclaw gateway --port 18789
```

### Python Dependencies

```bash
# Install Python dependencies
pip install -r requirements.txt

# Install Playwright browsers (for standalone mode)
playwright install chromium
```

## Usage

### With MoltBot (Recommended)

1. Make sure MoltBot Gateway is running:
   ```bash
   openclaw gateway --port 18789
   ```

2. Add your sites to `data/sites.txt` (one URL per line):
   ```
   amazon.com
   ebay.com
   walmart.com
   # ... add up to 100 sites
   ```

3. Run the analyzer:
   ```bash
   python main.py
   ```

## Input Formats

Supports multiple input formats:
- **TXT** - One URL per line (lines starting with `#` are ignored)
- **CSV** - Looks for columns named `url`, `site`, `domain`, or `website`
- **JSON** - Array of URLs or object with `sites` key

## Output

Results are saved to `output/` directory in two formats:
- **JSON** - Full data with all details
- **CSV** - Flattened for spreadsheet analysis

### Output Fields

| Field | Description |
|-------|-------------|
| `url` | Original URL |
| `domain` | Domain name |
| `is_ecommerce` | Whether site appears to be e-commerce |
| `listing_urls_count` | Number of listing pages found |
| `product_urls_count` | Number of product pages found |
| `has_product_pages` | Whether product detail pages exist |
| `estimated_total_pages` | Estimated pagination depth |
| `estimated_total_products` | Estimated product count |
| `pagination_type` | `next_page_link`, `infinite_scroll`, `load_more_button`, `numbered_pages` |
| `security_issues` | `cloudflare`, `captcha`, `bot_protection`, `blocked`, `timeout` |
| `load_time_seconds` | Page load time |

## Configuration

Edit `config.py` to customize:

```python
BROWSER_HEADLESS = True      # Set False to see browser
MAX_CONCURRENT = 5           # Concurrent browser tabs
BROWSER_TIMEOUT = 30000      # Timeout in ms
DELAY_BETWEEN_REQUESTS = 1.0 # Delay between sites

# MoltBot agent settings
MOLTBOT_AGENT_COMPLETION_TIMEOUT = 420  # 7 min for multi-step agent work
MOLTBOT_AGENT_RETRY_TIMEOUT = 300      # 5 min for retry attempts
MOLTBOT_AGENT_CONCURRENCY = 2          # Concurrent agent sessions
```

## Project Structure

```
moltbot-scraper/
├── main.py              # Entry point
├── moltbot_client.py    # MoltBot WebSocket client
├── moltbot_scraper.py   # MoltBot-based scraper (phased prompt + retry logic)
├── models.py            # Data models
├── config.py            # Configuration
├── requirements.txt     # Dependencies
├── data/
│   └── sites.txt        # Your list of sites
└── output/
    └── analysis_*.json/csv  # Results
```

## How It Works

Connects to MoltBot Gateway via WebSocket (`ws://127.0.0.1:18789`) and sends a **phased strategic prompt** to the AI agent:

1. **Navigate** — Visit the target URL, wait for full load, scroll to trigger lazy content
2. **Handle obstacles** — Automatically deal with geo-redirects, cookie banners, CAPTCHAs, Cloudflare challenges, and access denials
3. **Deep navigation** — Click into a category page from the nav menu to find more URLs beyond the homepage
4. **Extract data** — Collect listing/product URLs, pagination info, product counts, and security issues
5. **Retry on failure** — If blocked with no data, a follow-up prompt tries alternate strategies (sitemap, mobile site, alternate paths) on the same agent session

## MoltBot Skills Used

- `agent-browser` - Headless browser with accessibility tree snapshots
- `playwright-cli` - Browser automation for scraping
