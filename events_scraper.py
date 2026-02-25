"""Event scraper using MoltBot/OpenClaw."""

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from string import Template
from typing import Any
from urllib.parse import urlparse

from moltbot_client import MoltBotClient, MoltBotConfig
from moltbot_scraper import _extract_json_object
from events_models import EventItem, VenueResult
from config import (
    MOLTBOT_AGENT_COMPLETION_TIMEOUT,
    MOLTBOT_AGENT_CONCURRENCY,
)

logger = logging.getLogger(__name__)


EVENTS_PROMPT = Template(r"""You are an expert web scraper agent. Your task: extract EVERY SINGLE event from the venue page at $url.

== TOOL STRATEGY ==
1. Try **web_fetch** on $url first.
2. If web_fetch fails (403, blocked, "Just a moment", empty), immediately use the **browser** tool to navigate to $url instead. Do NOT retry web_fetch.
3. If the page has no events, look for /events, /calendar, /shows, /schedule links and try those.

== EXTRACTION RULES (READ CAREFULLY) ==
Go through the ENTIRE page content systematically. Count every event listing/card you see. You MUST extract ALL of them — not just the first few.

For EACH event, extract these fields:

- **event_name**: The actual title/name of the event (e.g. "Harry Potter and the Cursed Child").
  IMPORTANT: The event name is the main headline/title, NOT the genre or category tag.
  If you see a genre label like "Electronic" or "Concert" next to a title like "Alleycvt @ BMH", the event_name is "Alleycvt @ BMH", NOT "Electronic".
- **date**: Date in YYYY-MM-DD format. For date ranges use "YYYY-MM-DD to YYYY-MM-DD". Extract from date elements like "Feb 21, 2026" -> "2026-02-21". ALWAYS extract the date if visible.
- **time**: Doors/show time (e.g. "Doors: 7PM / Show: 8PM")
- **venue**: Venue name
- **artist**: Performer name if different from event_name
- **price**: Ticket price or range (e.g. "$$25-$$45", "Free", "$$0.00")
- **ticket_url**: The "Buy Tickets" link URL — look for <a> tags with text like "Buy Tickets", class "tickets", or href to ticket platforms
- **event_url**: Link to the event detail/info page — look for "Learn More", "More Info" links, or the main title link
- **image_url**: The event poster/image <img> src URL
- **category**: Concert, Theater, Comedy, etc.
- **description**: Brief description if available (max 200 chars)

== RETURN FORMAT ==
Return a single JSON object (no markdown fences):

{
  "venue_name": "Example Venue",
  "events": [
    {
      "event_name": "Artist Live in Concert",
      "date": "2026-03-15",
      "time": "8:00 PM",
      "venue": "Example Venue",
      "artist": "Artist Name",
      "price": "$$25-$$45",
      "ticket_url": "https://tickets.example.com/event/123",
      "event_url": "https://example.com/events/artist-live",
      "image_url": "https://example.com/images/artist.jpg",
      "category": "Concert",
      "description": "Live performance by Artist Name"
    }
  ],
  "total_events_found": 1,
  "error": null
}

== CRITICAL RULES ==
- You MUST extract EVERY event on the page. If you see 12 events, return 12. If you see 6, return 6. Do NOT skip any.
- Fill in EVERY field you can find — dates, prices, ticket URLs are almost always present on event listing pages. Look harder.
- Use "" for fields truly not available — do NOT leave out fields that are on the page.
- If the page has "Load More" or pagination, extract what is visible and set total_events_found to the count you extracted.
- If completely blocked, return JSON with error and empty events list.
- NEVER summarize or combine events. Each event is a separate entry.
- Double-check: does your events count match the number of event cards/listings on the page?
""")


EVENTS_RETRY_PROMPT = Template(r"""You previously only extracted $count events from $url, but the page likely has MORE events.

Use the **browser** tool to navigate to $url. Wait for the page to fully load (wait a few seconds for JS rendering). Then scroll down to see the FULL page.

STEP 1: Count how many event cards/listings are on the page. Each event typically has a title, date, and "Buy Tickets" button.
STEP 2: Extract EVERY single one — do NOT stop after a few.

For EACH event you MUST extract:
- **event_name**: The headline/title text (NOT genre labels like "Electronic" or "Concert")
- **date**: Convert to YYYY-MM-DD (e.g. "Feb 21, 2026" -> "2026-02-21", "Apr 7 - 12, 2026" -> "2026-04-07 to 2026-04-12")
- **time**: Doors/show times
- **venue**: Venue name
- **artist**: Performer if different from event_name
- **price**: From price elements or ticket info (e.g. "$$25-$$60")
- **ticket_url**: The href from "Buy Tickets" links — these often go to evenue.net, ticketmaster.com, seetickets.us, etc.
- **event_url**: The href from the event title link or "Learn More" / "More Info" link
- **image_url**: The <img> src for the event poster
- **category**: Concert, Theater, Comedy, etc.
- **description**: Brief description if available

Return a single JSON object (no markdown fences):
{
  "venue_name": "Venue Name",
  "events": [ ... one object per event ... ],
  "total_events_found": <number>,
  "error": null
}

CRITICAL: If you found $count before but there are actually more events on the page, you MUST include ALL of them this time.
""")


def _parse_events(raw_events: list) -> list[EventItem]:
    """Parse raw event dicts from agent response into EventItem objects."""
    items = []
    for raw in raw_events:
        if not isinstance(raw, dict):
            continue
        items.append(EventItem(
            event_name=str(raw.get("event_name") or ""),
            date=str(raw.get("date") or ""),
            time=str(raw.get("time") or ""),
            venue=str(raw.get("venue") or ""),
            artist=str(raw.get("artist") or ""),
            price=str(raw.get("price") or ""),
            ticket_url=str(raw.get("ticket_url") or ""),
            event_url=str(raw.get("event_url") or ""),
            image_url=str(raw.get("image_url") or ""),
            category=str(raw.get("category") or ""),
            description=str(raw.get("description") or "")[:200],
        ))
    return items


@dataclass
class EventsScraper:
    """Scraper that uses MoltBot to extract events from venue pages."""

    config: MoltBotConfig = field(default_factory=MoltBotConfig)
    client: MoltBotClient | None = None

    async def __aenter__(self):
        self.client = MoltBotClient(self.config)
        await self.client.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.client:
            await self.client.disconnect()

    async def scrape_venue(self, url: str, min_events_retry: int = 6) -> VenueResult:
        """Scrape events from a single venue URL.

        If the first attempt returns very few events (< min_events_retry),
        retry once with a follow-up prompt asking the agent to look harder.
        """
        if not url.startswith(("http://", "https://")):
            url = f"https://{url}"

        parsed = urlparse(url)
        domain = parsed.netloc
        session_key = f"agent:events:scraper-{uuid.uuid4().hex[:8]}"

        try:
            prompt = EVENTS_PROMPT.substitute(url=url, domain=domain)

            start_time = time.monotonic()
            result = await self.client.invoke_agent(
                prompt=prompt,
                tools=["web_fetch", "agent-browser", "playwright-cli"],
                session_key=session_key,
                completion_timeout=MOLTBOT_AGENT_COMPLETION_TIMEOUT,
            )
            elapsed = time.monotonic() - start_time

            venue_result = self._parse_response(url, result)
            venue_result.venue_url = url
            venue_result.load_time_seconds = elapsed
            logger.warning(
                "1st pass: %d events from %s (%.1fs)",
                len(venue_result.events), url, elapsed,
            )

            # Retry if too few events extracted (likely incomplete extraction)
            # Also retry on soft errors (agent gave up but site may still work)
            if len(venue_result.events) < min_events_retry:
                logger.warning(
                    "Only %d events (< %d threshold) from %s — retrying...",
                    len(venue_result.events), min_events_retry, url,
                )
                retry_key = f"agent:events:retry-{uuid.uuid4().hex[:8]}"
                retry_prompt = EVENTS_RETRY_PROMPT.substitute(
                    url=url,
                    count=len(venue_result.events),
                )
                retry_start = time.monotonic()
                retry_result = await self.client.invoke_agent(
                    prompt=retry_prompt,
                    tools=["web_fetch", "agent-browser", "playwright-cli"],
                    session_key=retry_key,
                    completion_timeout=MOLTBOT_AGENT_COMPLETION_TIMEOUT,
                )
                retry_elapsed = time.monotonic() - retry_start

                retry_venue = self._parse_response(url, retry_result)
                logger.warning(
                    "Retry: %d events from %s (%.1fs)",
                    len(retry_venue.events), url, retry_elapsed,
                )
                # Use retry result if it found more events
                if len(retry_venue.events) > len(venue_result.events):
                    retry_venue.venue_url = url
                    retry_venue.load_time_seconds = elapsed + retry_elapsed
                    return retry_venue

            return venue_result

        except TimeoutError:
            return VenueResult(
                venue_url=url,
                error_message="MoltBot agent timeout",
            )
        except Exception as e:
            return VenueResult(
                venue_url=url,
                error_message=str(e),
            )

    def _parse_response(self, url: str, result: Any) -> VenueResult:
        """Parse MoltBot agent response into VenueResult."""
        try:
            if isinstance(result, dict):
                if result.get("error"):
                    return VenueResult(venue_url=url, error_message=result["error"])
                if result.get("status") == "timeout":
                    return VenueResult(venue_url=url, error_message="Agent timed out")
                response_text = result.get("content", "")
            else:
                response_text = str(result)

            if not isinstance(response_text, str):
                response_text = str(response_text)

            response_text = response_text.strip()
            if not response_text:
                return VenueResult(venue_url=url, error_message="Agent returned empty response")

            data = _extract_json_object(response_text)
            if data is None:
                data = json.loads(response_text)

            events = _parse_events(data.get("events") or [])

            return VenueResult(
                venue_name=str(data.get("venue_name") or ""),
                venue_url=url,
                events=events,
                total_events_found=data.get("total_events_found") or len(events),
                error_message=data.get("error") or None,
            )

        except (json.JSONDecodeError, KeyError, TypeError) as e:
            return VenueResult(
                venue_url=url,
                error_message=f"Failed to parse agent response: {e}",
            )

    async def scrape_venues(self, urls: list[str],
                            concurrency: int = MOLTBOT_AGENT_CONCURRENCY) -> list[VenueResult]:
        """Scrape multiple venues with controlled concurrency."""
        semaphore = asyncio.Semaphore(concurrency)

        async def scrape_with_semaphore(url: str) -> VenueResult:
            async with semaphore:
                return await self.scrape_venue(url)

        tasks = [scrape_with_semaphore(url) for url in urls]
        return await asyncio.gather(*tasks)
