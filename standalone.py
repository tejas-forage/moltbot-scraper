"""Web scraper using Playwright for browser automation."""

import asyncio
import time
from urllib.parse import urlparse

from playwright.async_api import async_playwright, Browser, Page, TimeoutError as PlaywrightTimeout

from analyzer import PageAnalyzer
from config import (
    BROWSER_HEADLESS,
    BROWSER_TIMEOUT,
    DELAY_BETWEEN_REQUESTS,
    MAX_CONCURRENT,
    PAGE_LOAD_TIMEOUT,
    RETRY_ATTEMPTS,
)
from models import PaginationType, SecurityIssue, SiteAnalysis


class EcommerceScraper:
    """Scraper for analyzing e-commerce websites."""

    def __init__(self, headless: bool = BROWSER_HEADLESS):
        self.headless = headless
        self.browser: Browser | None = None
        self.semaphore = asyncio.Semaphore(MAX_CONCURRENT)

    async def __aenter__(self):
        """Start browser on context enter."""
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(
            headless=self.headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-sandbox",
            ],
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Close browser on context exit."""
        if self.browser:
            await self.browser.close()
        await self.playwright.stop()

    async def analyze_site(self, url: str) -> SiteAnalysis:
        """Analyze a single e-commerce site."""
        async with self.semaphore:
            return await self._analyze_with_retry(url)

    async def _analyze_with_retry(self, url: str) -> SiteAnalysis:
        """Analyze site with retry logic."""
        last_error = None

        for attempt in range(RETRY_ATTEMPTS + 1):
            try:
                return await self._do_analyze(url)
            except Exception as e:
                last_error = e
                if attempt < RETRY_ATTEMPTS:
                    await asyncio.sleep(DELAY_BETWEEN_REQUESTS * (attempt + 1))

        # All retries failed
        return SiteAnalysis(
            url=url,
            domain=urlparse(url).netloc,
            error_message=str(last_error),
            security_issues=[SecurityIssue.BLOCKED],
        )

    async def _do_analyze(self, url: str) -> SiteAnalysis:
        """Perform the actual site analysis."""
        if not url.startswith(("http://", "https://")):
            url = f"https://{url}"

        domain = urlparse(url).netloc
        start_time = time.time()

        context = await self.browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )

        try:
            page = await context.new_page()
            page.set_default_timeout(BROWSER_TIMEOUT)

            # Navigate to the site
            try:
                response = await page.goto(url, wait_until="domcontentloaded", timeout=PAGE_LOAD_TIMEOUT)
            except PlaywrightTimeout:
                return SiteAnalysis(
                    url=url,
                    domain=domain,
                    error_message="Page load timeout",
                    security_issues=[SecurityIssue.TIMEOUT],
                )

            if not response:
                return SiteAnalysis(
                    url=url,
                    domain=domain,
                    error_message="No response received",
                )

            # Wait for dynamic content
            await asyncio.sleep(2)

            # Scroll to trigger lazy loading
            await self._scroll_page(page)

            # Get page content
            html = await page.content()
            load_time = time.time() - start_time

            # Analyze the page
            analyzer = PageAnalyzer(url, html)

            # Check for security blocks
            security_issues = analyzer.detect_security_issues()
            if response.status == 403:
                security_issues.append(SecurityIssue.BLOCKED)

            # Get URLs
            listing_urls = analyzer.find_listing_urls()
            product_urls = analyzer.find_product_urls()

            # Create analysis result
            analysis = SiteAnalysis(
                url=url,
                domain=domain,
                is_ecommerce=analyzer.is_ecommerce_site(),
                listing_urls=listing_urls,
                product_urls=product_urls,
                has_product_pages=len(product_urls) > 0,
                estimated_total_pages=analyzer.estimate_page_count(),
                estimated_total_products=analyzer.estimate_product_count(),
                pagination_type=analyzer.detect_pagination_type(),
                security_issues=security_issues,
                page_title=analyzer.get_page_title(),
                load_time_seconds=load_time,
            )

            # If we found listing pages, analyze one for better estimates
            if listing_urls and not product_urls:
                analysis = await self._analyze_listing_page(
                    page, listing_urls[0], analysis
                )

            return analysis

        finally:
            await context.close()
            await asyncio.sleep(DELAY_BETWEEN_REQUESTS)

    async def _scroll_page(self, page: Page, scrolls: int = 3):
        """Scroll page to trigger lazy loading."""
        for _ in range(scrolls):
            await page.evaluate("window.scrollBy(0, window.innerHeight)")
            await asyncio.sleep(0.5)
        # Scroll back to top
        await page.evaluate("window.scrollTo(0, 0)")

    async def _analyze_listing_page(
        self, page: Page, listing_url: str, analysis: SiteAnalysis
    ) -> SiteAnalysis:
        """Analyze a listing page for better product/page estimates."""
        try:
            await page.goto(listing_url, wait_until="domcontentloaded", timeout=PAGE_LOAD_TIMEOUT)
            await asyncio.sleep(2)
            await self._scroll_page(page)

            html = await page.content()
            listing_analyzer = PageAnalyzer(listing_url, html)

            # Update with better estimates
            product_urls = listing_analyzer.find_product_urls()
            if product_urls:
                analysis.product_urls = product_urls
                analysis.has_product_pages = True

            new_page_count = listing_analyzer.estimate_page_count()
            if new_page_count > analysis.estimated_total_pages:
                analysis.estimated_total_pages = new_page_count

            new_product_count = listing_analyzer.estimate_product_count()
            if new_product_count > analysis.estimated_total_products:
                analysis.estimated_total_products = new_product_count

            # Update pagination type
            pagination = listing_analyzer.detect_pagination_type()
            if pagination != PaginationType.UNKNOWN:
                analysis.pagination_type = pagination

        except Exception:
            pass  # Keep original analysis if listing page fails

        return analysis

    async def analyze_sites(self, urls: list[str]) -> list[SiteAnalysis]:
        """Analyze multiple sites concurrently."""
        tasks = [self.analyze_site(url) for url in urls]
        return await asyncio.gather(*tasks)


# Standalone main function
async def main():
    """Run standalone scraper (without MoltBot)."""
    import json
    import sys
    from datetime import datetime
    from pathlib import Path

    import pandas as pd
    from rich.console import Console
    from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
    from rich.table import Table
    from rich.panel import Panel

    from config import DATA_DIR, OUTPUT_DIR

    console = Console()

    console.print(Panel.fit(
        "[bold blue]E-commerce Site Analyzer[/bold blue]\n"
        "[dim]Standalone Mode (Playwright)[/dim]",
        border_style="yellow"
    ))

    # Load sites
    sites_file = sys.argv[1] if len(sys.argv) > 1 else None
    if sites_file:
        sites_path = Path(sites_file)
    else:
        sites_path = DATA_DIR / "sites.txt"

    if not sites_path.exists():
        console.print(f"[red]Error: Sites file not found: {sites_path}[/red]")
        return

    # Load sites from file
    with open(sites_path) as f:
        sites = [line.strip() for line in f if line.strip() and not line.startswith("#")]

    console.print(f"\n[green]Loaded {len(sites)} sites to analyze[/green]\n")

    if not sites:
        console.print("[red]No sites to analyze[/red]")
        return

    # Run analysis
    results = []
    async with EcommerceScraper() as scraper:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("Analyzing sites...", total=len(sites))

            for site in sites:
                progress.update(task, description=f"Analyzing {site[:40]}...")
                result = await scraper.analyze_site(site)
                results.append(result)
                progress.advance(task)

    # Save results
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    data = [r.to_dict() for r in results]

    json_path = OUTPUT_DIR / f"analysis_{timestamp}.json"
    with open(json_path, "w") as f:
        json.dump(data, f, indent=2)

    csv_path = OUTPUT_DIR / f"analysis_{timestamp}.csv"
    df = pd.DataFrame(data)
    df["listing_urls_sample"] = df["listing_urls_sample"].apply(lambda x: "; ".join(x) if x else "")
    df["product_urls_sample"] = df["product_urls_sample"].apply(lambda x: "; ".join(x) if x else "")
    df["security_issues"] = df["security_issues"].apply(lambda x: ", ".join(x) if x else "none")
    df.to_csv(csv_path, index=False)

    console.print(f"\n[green]Results saved to:[/green]")
    console.print(f"  JSON: {json_path}")
    console.print(f"  CSV:  {csv_path}\n")

    # Print summary table
    table = Table(title="Analysis Summary")
    table.add_column("Domain", style="cyan")
    table.add_column("E-com", justify="center")
    table.add_column("Products", justify="right")
    table.add_column("Pagination", style="green")
    table.add_column("Issues", style="red")

    for r in results:
        table.add_row(
            r.domain[:30],
            "✓" if r.is_ecommerce else "✗",
            str(r.estimated_total_products) or "-",
            r.pagination_type.value[:15],
            ", ".join(i.value for i in r.security_issues)[:20] or "-",
        )

    console.print(table)


if __name__ == "__main__":
    asyncio.run(main())
