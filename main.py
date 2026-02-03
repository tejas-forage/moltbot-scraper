"""Main entry point for the e-commerce site analyzer using MoltBot."""

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.table import Table
from rich.panel import Panel

from config import DATA_DIR, OUTPUT_DIR, MOLTBOT_GATEWAY_URL, MOLTBOT_AUTH_TOKEN
from moltbot_scraper import MoltBotScraper, check_moltbot_connection
from moltbot_client import MoltBotConfig
from models import SiteAnalysis

console = Console()


def load_sites(file_path: Path | str) -> list[str]:
    """Load site URLs from a file (txt, csv, or json)."""
    file_path = Path(file_path)

    if not file_path.exists():
        console.print(f"[red]Error: File not found: {file_path}[/red]")
        sys.exit(1)

    suffix = file_path.suffix.lower()

    if suffix == ".json":
        with open(file_path) as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
            elif isinstance(data, dict) and "sites" in data:
                return data["sites"]
    elif suffix == ".csv":
        df = pd.read_csv(file_path)
        # Try common column names
        for col in ["url", "site", "domain", "website"]:
            if col in df.columns:
                return df[col].tolist()
        # Use first column
        return df.iloc[:, 0].tolist()
    else:
        # Assume text file with one URL per line
        with open(file_path) as f:
            return [line.strip() for line in f if line.strip() and not line.startswith("#")]

    return []


def save_results(results: list[SiteAnalysis], output_dir: Path):
    """Save analysis results to multiple formats."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Convert to dicts
    data = [r.to_dict() for r in results]

    # Save as JSON
    json_path = output_dir / f"analysis_{timestamp}.json"
    with open(json_path, "w") as f:
        json.dump(data, f, indent=2)

    # Save as CSV
    csv_path = output_dir / f"analysis_{timestamp}.csv"
    df = pd.DataFrame(data)
    # Flatten list columns for CSV
    df["listing_urls_sample"] = df["listing_urls_sample"].apply(lambda x: "; ".join(x) if x else "")
    df["product_urls_sample"] = df["product_urls_sample"].apply(lambda x: "; ".join(x) if x else "")
    df["security_issues"] = df["security_issues"].apply(lambda x: ", ".join(x) if x else "none")
    df.to_csv(csv_path, index=False)

    return json_path, csv_path


def print_summary(results: list[SiteAnalysis]):
    """Print a summary table of results."""
    table = Table(title="E-commerce Site Analysis Summary")

    table.add_column("Domain", style="cyan", no_wrap=True)
    table.add_column("E-com", justify="center")
    table.add_column("Products", justify="right")
    table.add_column("Pages", justify="right")
    table.add_column("Pagination", style="green")
    table.add_column("Issues", style="red")

    for r in results:
        ecom = "✓" if r.is_ecommerce else "✗"
        issues = ", ".join(i.value for i in r.security_issues) if r.security_issues else "-"

        table.add_row(
            r.domain[:30],
            ecom,
            str(r.estimated_total_products) if r.estimated_total_products else "-",
            str(r.estimated_total_pages) if r.estimated_total_pages else "-",
            r.pagination_type.value[:15],
            issues[:20],
        )

    console.print(table)

    # Stats
    total = len(results)
    ecom_count = sum(1 for r in results if r.is_ecommerce)
    has_issues = sum(1 for r in results if r.security_issues)
    errors = sum(1 for r in results if r.error_message)

    console.print(f"\n[bold]Total sites:[/bold] {total}")
    console.print(f"[bold]E-commerce sites:[/bold] {ecom_count} ({ecom_count/total*100:.1f}%)")
    console.print(f"[bold]Sites with security issues:[/bold] {has_issues}")
    console.print(f"[bold]Sites with errors:[/bold] {errors}")


async def main(sites_file: str | None = None, gateway_url: str | None = None):
    """Run the e-commerce site analyzer using MoltBot."""
    console.print(Panel.fit(
        "[bold blue]E-commerce Site Analyzer[/bold blue]\n"
        "[dim]Powered by MoltBot/OpenClaw[/dim]",
        border_style="blue"
    ))

    # Configure MoltBot client
    config = MoltBotConfig(
        gateway_url=MOLTBOT_GATEWAY_URL,
        auth_token=MOLTBOT_AUTH_TOKEN,
    )

    # Check MoltBot connection
    console.print("\n[yellow]Checking MoltBot Gateway connection...[/yellow]")

    if not await check_moltbot_connection(config):
        console.print("[red]Error: Cannot connect to MoltBot Gateway![/red]")
        console.print("\nMake sure MoltBot is running:")
        console.print("  1. Install: [cyan]npm install -g openclaw@latest[/cyan]")
        console.print("  2. Start:   [cyan]openclaw gateway --port 18789[/cyan]")
        console.print("\nOr run the standalone scraper:")
        console.print("  [cyan]python standalone.py[/cyan]")
        return

    console.print("[green]✓ Connected to MoltBot Gateway[/green]\n")

    # Load sites
    if sites_file:
        sites_path = Path(sites_file)
    else:
        sites_path = DATA_DIR / "sites.txt"

    if not sites_path.exists():
        console.print(f"[yellow]No sites file found at {sites_path}[/yellow]")
        console.print("Create a file with one URL per line, or pass a file path as argument.")
        console.print(f"\nExample: python main.py {DATA_DIR}/sites.txt")
        return

    sites = load_sites(sites_path)
    console.print(f"[green]Loaded {len(sites)} sites to analyze[/green]\n")

    if not sites:
        console.print("[red]No sites to analyze[/red]")
        return

    # Override gateway URL if provided
    if gateway_url:
        config.gateway_url = gateway_url

    # Run analysis
    results = []
    async with MoltBotScraper(config=config) as scraper:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("Analyzing sites via MoltBot...", total=len(sites))

            for site in sites:
                progress.update(task, description=f"MoltBot analyzing {site[:40]}...")
                result = await scraper.analyze_site(site)
                results.append(result)
                progress.advance(task)

    # Save and display results
    json_path, csv_path = save_results(results, OUTPUT_DIR)

    console.print(f"\n[green]Results saved to:[/green]")
    console.print(f"  JSON: {json_path}")
    console.print(f"  CSV:  {csv_path}\n")

    print_summary(results)


if __name__ == "__main__":
    sites_file = sys.argv[1] if len(sys.argv) > 1 else None
    gateway_url = sys.argv[2] if len(sys.argv) > 2 else None
    asyncio.run(main(sites_file, gateway_url))
