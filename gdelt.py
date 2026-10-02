# gdelt.py
"""gdelt node: the market stories most widely published in the last 6 hours, from GDELT's GKG. No LLM.

GDELT 2.0 publishes a Global Knowledge Graph (GKG) file every 15 minutes: one row per article, with
its page title, site, themes, people, organizations and places. This node joins the 24 newest files
(6 hours), keeps articles tagged with a market theme, groups them by page title, and ranks stories
by how many sites published them. With an as-of day it reads the 6 hours ending at 23:45 UTC that
day, or at the newest update if the day is not over. Each file's market rows are appended to
data/events.db (db.py) once, so a window that overlaps an earlier one downloads only the new files.
"""

import html
import io
import re
import zipfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import date, datetime, time, timedelta

import httpx

import db
from state import State, Story

LAST_UPDATE = "http://data.gdeltproject.org/gdeltv2/lastupdate.txt"
GKG = "http://data.gdeltproject.org/gdeltv2/{at}.gkg.csv.zip"
UPDATES = 24  # 6 hours of 15-minute files
TOP_STORIES = 30
MARKET_THEMES = {"ECON_OILPRICE", "ENV_OIL", "ECON_STOCKMARKET", "ECON_INTEREST_RATES", "ECON_INFLATION",
                 "ECON_CENTRALBANK", "ECON_TRADE_DISPUTE", "ECON_BANKRUPTCY", "FUELPRICES", "ENV_NATURALGAS",
                 "ECON_CURRENCY_EXCHANGE_RATE", "ECON_DEBT", "ECON_EARNINGSREPORT"}
# GKG 2.1 columns that we use.
RECORD, SITE, URL, THEMES, LOCATIONS, PERSONS, ORGANIZATIONS, EXTRAS = 0, 3, 4, 8, 9, 11, 13, 26
PAGE_TITLE = re.compile(r"<PAGE_TITLE>(.*?)</PAGE_TITLE>")


def http_get(url):
    """The response body, or None when GDELT has no such file (404)."""
    response = httpx.get(url, follow_redirects=True, timeout=120)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return response.content


def unzip(data):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return archive.read(archive.namelist()[0]).decode("utf-8", errors="replace")


def themes(row):
    """The market themes of one GKG row, empty for other rows."""
    if len(row) <= EXTRAS:
        return set()
    return {theme.split(",")[0] for theme in row[THEMES].split(";") if theme} & MARKET_THEMES


def record(line):
    """(record_id, site, url, title, themes, actors, places) of a market-themed GKG line with a page
    title, or None for any other line. Lists are ;-separated."""
    row = line.split("\t")
    market = themes(row)
    title = PAGE_TITLE.search(row[EXTRAS]) if market else None
    if not title or not row[RECORD]:
        return None
    actors = {name for column in (PERSONS, ORGANIZATIONS) for name in row[column].split(";") if name}
    places = {place.split("#")[1] for place in row[LOCATIONS].split(";") if place.count("#") > 1}
    return (row[RECORD], row[SITE], row[URL], html.unescape(title.group(1)).strip(),
            ";".join(sorted(market)), ";".join(sorted(actors)), ";".join(sorted(places)))


def market_rows(as_of, get, connection):
    """(as_of, the market rows of the 6 hours ending at as_of's last update or the newest one).
    Downloads only the files of the window that are not in the database yet."""
    latest = datetime.strptime(get(LAST_UPDATE).decode().split()[2].rsplit("/", 1)[1][:14], "%Y%m%d%H%M%S")
    end = latest
    if as_of:
        day = date.fromisoformat(as_of)
        if day > latest.date():
            raise ValueError(f"The newest GDELT update is {latest:%Y-%m-%d %H:%M} UTC, before {day}.")
        end = min(latest, datetime.combine(day, time(23, 45)))
    times = [f"{end - timedelta(minutes=15 * i):%Y%m%d%H%M%S}" for i in range(UPDATES)]
    stored = {at for (at,) in connection.execute(
        f"SELECT at FROM gkg_file WHERE at IN ({', '.join('?' * len(times))})", times)}
    missing = [at for at in times if at not in stored]
    with ThreadPoolExecutor(8) as pool:
        files = list(pool.map(lambda at: get(GKG.format(at=at)), missing))
    for at, data in zip(missing, files):
        rows = [] if data is None else [r for line in unzip(data).splitlines() if (r := record(line))]  # None: GDELT skipped it
        with connection:  # one transaction, so a file is marked stored only together with its rows
            connection.executemany("INSERT INTO gkg_row VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
                                   [(r[0], at, *r[1:]) for r in rows])
            connection.execute("INSERT INTO gkg_file (at, rows) VALUES (?, ?) ON CONFLICT DO NOTHING", (at, len(rows)))
    rows = connection.execute("SELECT site, url, title, themes, actors, places FROM gkg_row "
                              "WHERE at BETWEEN ? AND ? ORDER BY at DESC, rowid", (times[-1], times[0])).fetchall()
    return as_of or end.date().isoformat(), rows


def stories(rows, top=TOP_STORIES):
    """The `top` stories: rows grouped by page title, ranked by how many sites published the title."""
    by_title = {}
    for site, url, title, market, actors, places in rows:
        s = by_title.setdefault(title.lower(), {"title": title, "url": url, "articles": 0, "sites": set(),
                                                "actors": set(), "themes": set(), "places": set()})
        s["articles"] += 1
        s["sites"].add(site)
        s["actors"].update(filter(None, actors.split(";")))
        s["themes"].update(market.split(";"))
        s["places"].update(filter(None, places.split(";")))
    ranked = sorted(by_title.values(), key=lambda s: (-len(s["sites"]), -s["articles"], s["title"]))[:top]
    return [Story(url=s["url"], title=s["title"], articles=s["articles"], sites=len(s["sites"]),
                  actors=sorted(s["actors"])[:8], themes=sorted(s["themes"]), places=sorted(s["places"])[:5])
            for s in ranked]


def top_stories(as_of=None, get=http_get, path=None):
    """(as_of, the TOP_STORIES most widely published market stories) for the 6 hours ending at the
    newest GDELT update, or at the end of as_of. Raises ValueError for a day GDELT has not reached.
    path is the database file, events.db by default."""
    with closing(db.connect(path)) as connection:
        as_of, rows = market_rows(as_of, get, connection)
    return as_of, stories(rows)


def gdelt(state: State) -> dict:
    """Reads as_of. Writes as_of and stories."""
    as_of, top = top_stories(state.as_of)
    return {"as_of": as_of, "stories": top}
