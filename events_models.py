"""Data models for event extraction."""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class EventItem:
    """A single event extracted from a venue page."""

    event_name: str = ""
    date: str = ""
    time: str = ""
    venue: str = ""
    artist: str = ""
    price: str = ""
    ticket_url: str = ""
    event_url: str = ""
    image_url: str = ""
    category: str = ""
    description: str = ""

    def to_dict(self) -> dict:
        return {
            "event_name": self.event_name,
            "date": self.date,
            "time": self.time,
            "venue": self.venue,
            "artist": self.artist,
            "price": self.price,
            "ticket_url": self.ticket_url,
            "event_url": self.event_url,
            "image_url": self.image_url,
            "category": self.category,
            "description": self.description,
        }


@dataclass
class VenueResult:
    """Result of scraping a single venue's events page."""

    venue_name: str = ""
    venue_url: str = ""
    events: list[EventItem] = field(default_factory=list)
    total_events_found: int = 0
    error_message: Optional[str] = None
    load_time_seconds: float = 0.0

    def to_dict(self) -> dict:
        return {
            "venue_name": self.venue_name,
            "venue_url": self.venue_url,
            "total_events_found": self.total_events_found,
            "events": [e.to_dict() for e in self.events],
            "error_message": self.error_message,
            "load_time_seconds": round(self.load_time_seconds, 2),
        }

    def to_flat_rows(self) -> list[dict]:
        """Flatten events for CSV — one row per event with venue info."""
        rows = []
        for e in self.events:
            row = e.to_dict()
            row["venue_source"] = self.venue_name
            row["venue_source_url"] = self.venue_url
            rows.append(row)
        return rows
