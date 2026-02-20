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


EVENTS_PROMPT = Template(r"""You are an expert web scraper agent. Extract ALL events from the venue/events page at $url.

TOOL STRATEGY:
- Try **web_fetch** FIRST — it is faster and more reliable.
- Only use the **browser** tool if web_fetch fails (empty, blocked, or JS-rendered content).
- NEVER retry the same tool on the same URL more than once.

== PHASE 1: NAVIGATE ==
1. Use web_fetch to fetch $url
2. If that returns no event data (empty or blocked), try the browser tool
3. If the main URL has no events, look for links like /events, /calendar, /shows, /schedule on the page and try those

== PHASE 2: EXTRACT EVENTS ==
From the page content, extract EVERY event you can find. For each event collect:

- **event_name**: Name/title of the event
- **date**: Date of the event (format: YYYY-MM-DD if possible, otherwise as shown)
- **time**: Start time, doors time, or showtime (e.g. "8:00 PM", "Doors 7PM / Show 8PM")
- **venue**: Venue name where the event takes place
- **artist**: Performer/artist name (if different from event name)
- **price**: Ticket price or price range (e.g. "$25", "$15-$45", "Free")
- **ticket_url**: Direct URL to buy tickets
- **event_url**: URL to the event detail page
- **image_url**: URL of the event image/poster
- **category**: Type of event (Concert, Comedy, Theater, Sports, Festival, etc.)
- **description**: Short description if available (max 200 chars)

== PHASE 3: RETURN JSON ==
Return your findings as a single JSON object. Do NOT wrap it in markdown code fences.

{
  "venue_name": "Example Venue",
  "events": [
    {
      "event_name": "Artist Live in Concert",
      "date": "2026-03-15",
      "time": "8:00 PM",
      "venue": "Example Venue",
      "artist": "Artist Name",
      "price": "$25-$45",
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

CRITICAL RULES:
- Extract ALL events visible on the page, not just a few.
- Use empty string "" for any field you cannot find — do NOT use null.
- If the page has pagination or "load more", note how many total events exist but only extract what's visible.
- If blocked, return the JSON with error filled in and empty events list.
- Stop making requests after 3 consecutive failures.
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

    async def scrape_venue(self, url: str) -> VenueResult:
        """Scrape events from a single venue URL."""
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
                tools=["agent-browser", "playwright-cli"],
                session_key=session_key,
                completion_timeout=MOLTBOT_AGENT_COMPLETION_TIMEOUT,
            )
            elapsed = time.monotonic() - start_time

            venue_result = self._parse_response(url, result)
            venue_result.venue_url = url
            venue_result.load_time_seconds = elapsed
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
