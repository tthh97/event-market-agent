"""What was known by a requested day: the one rule every data source must respect.

A brief for day D may only use evidence that existed by the end of D in EVENT_TIMEZONE, and never
anything later than now. Market windows use only US sessions that have closed.
Run: imported by research_tools, brief_checks, events_db, market_returns and brief_run.
"""

from datetime import UTC, datetime, time, timedelta
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

from models import TIMEZONE

NEW_YORK = ZoneInfo("America/New_York")


def today():
    """The current day in EVENT_TIMEZONE."""
    return datetime.now(TIMEZONE).date()


def parse_timestamp(value):
    """ISO 8601 or RFC 2822 text to an aware datetime (naive means UTC). None if unparseable."""
    if not value:
        return None
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        try:
            result = parsedate_to_datetime(str(value))
        except (ValueError, TypeError):
            return None
    return result.replace(tzinfo=UTC) if result.tzinfo is None else result


def local_day(timestamp):
    """The EVENT_TIMEZONE day a timestamp falls on."""
    return timestamp.astimezone(TIMEZONE).date()


def known_by(timestamp, as_of):
    """True when the timestamp is before the end of as_of in EVENT_TIMEZONE and not in the future."""
    end_of_day = datetime.combine(as_of + timedelta(days=1), time.min, TIMEZONE)
    return timestamp < min(datetime.now(UTC), end_of_day)


def last_closed_session_day(as_of):
    """Latest day whose US session has closed by as_of. Today's New York session never counts."""
    return min(as_of, datetime.now(NEW_YORK).date() - timedelta(days=1))

