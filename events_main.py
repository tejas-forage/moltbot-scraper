"""Main entry point for the events scraper."""

import asyncio
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

logging.basicConfig(level=logging.WARNING, format="%(name)s: %(message)s")

import pandas as pd
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.table import Table
from rich.panel import Panel

from config import OUTPUT_DIR, DATA_DIR, MOLTBOT_GATEWAY_URL, MOLTBOT_AUTH_TOKEN
from events_models import VenueResult

console = Console()


def load_sites(file_path: Path | str) -> list[str]:
    """Load venue URLs from a file (txt, csv, or json)."""
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
        for col in ["url", "site", "domain", "website"]:
            if col in df.columns:
                return df[col].tolist()
        return df.iloc[:, 0].tolist()
    else:
        with open(file_path) as f:
            return [line.strip() for line in f if line.strip() and not line.startswith("#")]

    return []


def save_results(results: list[VenueResult], output_dir: Path):
    """Save event results to JSON and CSV."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # JSON — full nested structure
    json_path = output_dir / f"events_{timestamp}.json"
    data = [r.to_dict() for r in results]
    with open(json_path, "w") as f:
        json.dump(data, f, indent=2)

    # CSV — one row per event, flattened
    csv_path = output_dir / f"events_{timestamp}.csv"
    rows = []
    for r in results:
        rows.extend(r.to_flat_rows())

    if rows:
        df = pd.DataFrame(rows)
        df.to_csv(csv_path, index=False)
    else:
        pd.DataFrame().to_csv(csv_path, index=False)

    return json_path, csv_path


def print_summary(results: list[VenueResult]):
    """Print a summary table of results."""
    table = Table(title="Events Scraper Summary")

    table.add_column("Venue", style="cyan", no_wrap=True)
    table.add_column("Events", justify="right")
    table.add_column("Status", style="green")
    table.add_column("Time (s)", justify="right")

    for r in results:
        status = "[red]Error[/red]" if r.error_message else "[green]OK[/green]"
        table.add_row(
            (r.venue_name or r.venue_url)[:40],
            str(r.total_events_found),
            status,
            str(round(r.load_time_seconds, 1)),
        )

    console.print(table)

    total_venues = len(results)
    total_events = sum(r.total_events_found for r in results)
    errors = sum(1 for r in results if r.error_message)

    console.print(f"\n[bold]Total venues:[/bold] {total_venues}")
    console.print(f"[bold]Total events extracted:[/bold] {total_events}")
    console.print(f"[bold]Errors:[/bold] {errors}")


async def main():
    """Run the events scraper."""
    from events_scraper import EventsScraper
    from moltbot_scraper import check_moltbot_connection
    from moltbot_client import MoltBotConfig

    args = sys.argv[1:]
    sites_file = args[0] if args else None
    gateway_url = args[1] if len(args) > 1 else None

    console.print(Panel.fit(
        "[bold blue]Events Scraper[/bold blue]\n"
        "[dim]Powered by MoltBot/OpenClaw[/dim]",
        border_style="blue"
    ))

    # Load sites
    sites_path = Path(sites_file) if sites_file else DATA_DIR / "tegna_sites.txt"
    console.print(f"[dim]Using sites file: {sites_path}[/dim]")

    if not sites_path.exists():
        console.print(f"[yellow]No sites file found at {sites_path}[/yellow]")
        console.print("Create a file with one venue URL per line, or pass a file path as argument.")
        console.print(f"\nExample: python events_main.py {DATA_DIR}/tegna_sites.txt")
        return

    sites = load_sites(sites_path)
    console.print(f"[green]Loaded {len(sites)} venue URLs[/green]\n")

    if not sites:
        console.print("[red]No venues to scrape[/red]")
        return

    # Check MoltBot connection
    config = MoltBotConfig(
        gateway_url=MOLTBOT_GATEWAY_URL,
        auth_token=MOLTBOT_AUTH_TOKEN,
    )
    if gateway_url:
        config.gateway_url = gateway_url

    console.print("[yellow]Checking MoltBot Gateway connection...[/yellow]")
    connected, error = await check_moltbot_connection(config)
    if not connected:
        console.print("[red]Error: Cannot connect to MoltBot Gateway![/red]")
        if error:
            console.print(f"\n[red]Details:[/red]\n{error}")
        console.print("\nMake sure MoltBot is running:")
        console.print("  1. Install: [cyan]npm install -g openclaw@latest[/cyan]")
        console.print("  2. Start:   [cyan]openclaw gateway --port 18789[/cyan]")
        return

    console.print("[green]Connected to MoltBot Gateway[/green]\n")

    # Scrape events
    results = []
    async with EventsScraper(config=config) as scraper:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("Scraping events...", total=len(sites))

            for site in sites:
                progress.update(task, description=f"Scraping {site[:50]}...")
                result = await scraper.scrape_venue(site)
                results.append(result)
                progress.advance(task)

    if not results:
        return

    # Save and display
    json_path, csv_path = save_results(results, OUTPUT_DIR)

    console.print(f"\n[green]Results saved to:[/green]")
    console.print(f"  JSON: {json_path}")
    console.print(f"  CSV:  {csv_path}\n")

    print_summary(results)


if __name__ == "__main__":
    asyncio.run(main())
