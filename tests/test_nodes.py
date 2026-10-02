# tests/test_nodes.py
"""Each plain node on small inputs, and the graph's routing. No network and no model calls.

Run: uv run pytest
"""

import io
import sqlite3
import zipfile
from contextlib import closing
from datetime import date

import pandas as pd
import pytest
from langgraph.runtime import Runtime
from langgraph.store.memory import InMemoryStore

import db
import gdelt
import output
import prices
import research
import triage
import verify
from graph import start
from state import Claim, Finding, ScoredStory, Source, State, Story


def gkg_row(url, site, title, themes=("ECON_OILPRICE",), persons="", orgs="", places=""):
    """One GKG 2.1 row (27 tab-separated columns) with the fields gdelt reads."""
    cells = [""] * 27
    cells[gdelt.SITE], cells[gdelt.URL], cells[gdelt.PERSONS], cells[gdelt.ORGANIZATIONS] = site, url, persons, orgs
    cells[gdelt.THEMES] = ";".join(f"{theme},10" for theme in themes)
    cells[gdelt.LOCATIONS] = ";".join(f"1#{place}#US#US##0#0#US#1" for place in places.split(";") if place)
    cells[gdelt.EXTRAS] = f"<PAGE_TITLE>{title}</PAGE_TITLE>"
    return "\t".join(cells)


def story(url="https://x.com/oil-prices-jump", severity=None):
    return ScoredStory(url=url, title="oil prices jump", articles=10, sites=3, actors=["IRAN"],
                       themes=["ECON_OILPRICE"], places=["Tehran, Iran"], severity=severity)


def zipped(text):
    """GDELT ships each export as a zip holding one tab-separated file."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("x.export.CSV", text)
    return buffer.getvalue()


def gkg_files(rows_at, latest="20261001193000"):
    """A fake GDELT server: lastupdate.txt, and one zipped GKG file per update time in rows_at.
    Each row gets a GKGRECORDID made of its file's time and its line number, as GDELT's are."""
    asked = []

    def get(url):
        asked.append(url)
        if url.endswith("lastupdate.txt"):
            return f"1 a http://data.gdeltproject.org/gdeltv2/{latest}.gkg.csv.zip\n".encode()
        at = url.rsplit("/", 1)[1][:14]
        rows = rows_at(at)
        return None if rows is None else zipped("\n".join(f"{at}-{i}{row}" for i, row in enumerate(rows)))
    return get, asked


def test_top_stories_rank_market_headlines_by_sites_over_the_last_6_hours(tmp_path):
    def rows_at(at):
        if at == "20261001191500":
            return None  # GDELT skipped this update
        return [gkg_row(f"https://{site}.com/oil", f"{site}.com", "Oil jumps on Gulf risk", persons="donald trump",
                        places="Riyadh, Saudi Arabia") for site in ("a", "b", "c")] + [
                gkg_row("https://d.com/fed", "d.com", "Fed holds rates", ("ECON_INTEREST_RATES",)),
                gkg_row("https://e.com/cat", "e.com", "Cat rescued", ("WB_ANIMALS",))]

    get, asked = gkg_files(rows_at)
    as_of, stories = gdelt.top_stories(None, get, tmp_path / "events.db")
    assert gdelt.top_stories(None, get, tmp_path / "events.db") == (as_of, stories)  # second run reads the database
    gkg = [u for u in asked if u.endswith(".gkg.csv.zip")]
    assert len(gkg) == 24 and gkg[0].endswith("20261001193000.gkg.csv.zip") and gkg[-1].endswith("20261001134500.gkg.csv.zip")
    assert as_of == "2026-10-01"
    assert [(s.title, s.sites, s.articles) for s in stories] == [("Oil jumps on Gulf risk", 3, 69), ("Fed holds rates", 1, 23)]
    assert stories[0].themes == ["ECON_OILPRICE"] and stories[0].actors == ["donald trump"]
    assert stories[0].places == ["Riyadh, Saudi Arabia"]


def test_top_stories_for_a_day_read_its_last_6_hours(tmp_path):
    get, asked = gkg_files(lambda at: [gkg_row("https://a.com/oil", "a.com", "Oil jumps")])
    as_of, stories = gdelt.top_stories("2026-09-30", get, tmp_path / "events.db")
    gkg = [u for u in asked if u.endswith(".gkg.csv.zip")]
    assert as_of == "2026-09-30" and gkg[0].endswith("20260930234500.gkg.csv.zip") and gkg[-1].endswith("20260930180000.gkg.csv.zip")
    assert [s.title for s in stories] == ["Oil jumps"]


def test_top_stories_for_a_day_gdelt_has_not_reached_says_so(tmp_path):
    get, _ = gkg_files(lambda at: [])
    with pytest.raises(ValueError, match="newest GDELT update"):
        gdelt.top_stories("2026-10-02", get, tmp_path / "events.db")


def test_a_window_15_minutes_later_downloads_only_the_new_file(tmp_path):
    def rows_at(at):
        return [gkg_row(f"https://a.com/{at}", "a.com", "Oil jumps")]

    gdelt.top_stories(None, gkg_files(rows_at)[0], tmp_path / "events.db")
    get, asked = gkg_files(rows_at, latest="20261001194500")
    _, stories = gdelt.top_stories(None, get, tmp_path / "events.db")
    assert [u.rsplit("/", 1)[1] for u in asked if u.endswith(".gkg.csv.zip")] == ["20261001194500.gkg.csv.zip"]
    assert stories[0].articles == 24  # the 23 stored files and the new one, not the file that left the window
    with closing(sqlite3.connect(tmp_path / "events.db")) as connection:
        assert connection.execute("SELECT count(*), sum(rows) FROM gkg_file").fetchone() == (25, 25)


def test_triage_keeps_the_most_severe(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setattr(triage, "jev_scores", lambda stories: {i: (float(i), 0.5) for i in range(len(stories))})
    stories = [Story(**story(f"https://x.com/s{i}").model_dump(exclude={"severity", "confidence"})) for i in range(5)]
    result = triage.triage(State(stories=stories))
    assert [s.url for s in result["severe"]] == ["https://x.com/s4", "https://x.com/s3", "https://x.com/s2"]


def test_triage_without_jev_says_so(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    result = triage.triage(State(stories=[Story(**story().model_dump(exclude={"severity", "confidence"}))]))
    assert result["jev_status"].startswith("not configured") and result["severe"][0].severity is None


def test_verify_removes_unsourced_claims_and_invented_numbers(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    source = Source(url="https://a.com/1", title="Oil jumps", published="2026-09-29", excerpt="Brent rose 3% to $107.")
    claims = [Claim(text="Brent rose 3% to $107.", source_url=source.url, source_date="2026-09-30"),
              Claim(text="Brent rose 5%.", source_url=source.url, source_date="unknown"),
              Claim(text="Shipping slowed.", source_url="https://made-up.com", source_date="unknown")]
    result = verify.verify(State(findings=[Finding(topic="Oil", summary="s", claims=claims)], sources=[source]))
    kept = result["findings"][0].claims
    assert [(c.text, c.source_date) for c in kept] == [("Brent rose 3% to $107.", "2026-09-29")]
    assert result["rejected"][0].endswith("(number(s) 5 not in the source)")
    assert result["rejected"][1].endswith("(its URL is not an article search_news returned)")


def test_output_writes_markdown_and_replies(tmp_path, monkeypatch):
    monkeypatch.setattr(output, "FOLDER", tmp_path)
    monkeypatch.setattr(db, "PATH", tmp_path / "events.db")
    store = InMemoryStore()
    claim = Claim(text="Brent rose 3%.", source_url="https://a.com/1", source_date="2026-09-29", sector="energy")
    state = State(as_of="2026-09-30", jev_status="ok", severe=[story(severity=2.5)],
                  findings=[Finding(topic="Oil", summary="Oil rose.", claims=[claim])],
                  moves={"energy": (2.0, 1.8)}, price_window="Close 29 Sep to close 30 Sep.")
    result = output.output(state, {"configurable": {"thread_id": "t1"}}, Runtime(store=store))
    text = open(result["report"], encoding="utf-8").read()
    assert "Jev severity: 2.50 of 3" in text and "Sector: energy." in text
    assert "| energy | XLE | +2.0% | +1.8 pts |" in text and "Close 29 Sep to close 30 Sep." in text
    assert result["messages"][0].content == text
    with closing(db.connect()) as connection:
        assert connection.execute("SELECT thread_id, as_of, kind FROM run").fetchall() == [("t1", "2026-09-30", "events")]
        assert connection.execute("SELECT rank, severity FROM story").fetchall() == [(1, 2.5)]
        assert connection.execute("SELECT text, sector, unverified FROM claim").fetchall() == [("Brent rose 3%.", "energy", 0)]
        assert connection.execute("SELECT sector, move, vs_spy FROM price_move").fetchall() == [("energy", 2.0, 1.8)]
    assert [i.value["topic"] for i in store.search(("findings", "2026-09-30"))] == ["Oil"]


def test_research_recalls_the_last_7_days_of_findings():
    store = InMemoryStore()
    claim = [Claim(text="Brent rose 3%.", source_url="https://a.com/1", source_date="2026-09-29")]
    for day, topic in [("2026-09-23", "Too old"), ("2026-09-24", "Fed"), ("2026-09-30", "Oil"), ("2026-10-01", "Later")]:
        research.remember(store, day, "run", [Finding(topic=topic, summary="s.", claims=claim),
                                             Finding(topic="Nothing found", summary="s.", claims=[])])
    research.remember(store, "2026-09-30", "rerun", [Finding(topic="Oil", summary="s.", claims=claim)])
    assert research.recall(store, date(2026, 9, 30)) == ["- 2026-09-30: Oil. s.", "- 2026-09-24: Fed. s."]
    assert research.recall(None, date(2026, 9, 30)) == []


def test_follow_ups_skip_to_research():
    assert start(State()) == "gdelt"
    assert start(State(report="output/x.md")) == "research"


def test_verify_routes_claims_on_jev_confidence(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setattr(verify, "jev_checks", lambda claims, as_of: [
        (0.95, 0.9, "energy", 0.9), (0.6, 0.9, "energy", 0.5), (0.9, 0.2, "none", 0.9)])
    source = Source(url="https://a.com/1", title="Oil jumps", published="2026-09-29",
                    excerpt="Brent rose 3% to $107. Refiners gained.")
    claims = [Claim(text=t, source_url=source.url, source_date="unknown")
              for t in ("Brent rose 3%.", "Refiners gained.", "Brent hit $107.")]
    result = verify.verify(State(as_of="2026-09-30", findings=[Finding(topic="Oil", summary="s", claims=claims)],
                                 sources=[source]))
    kept = result["findings"][0].claims
    assert [(c.text, c.unverified, c.sector) for c in kept] == [("Brent rose 3%.", False, "energy"),
                                                                ("Refiners gained.", True, None)]
    assert result["rejected"] == ['Oil: "Brent hit $107." (Jev: it is about an earlier period)']
    assert result["verify_status"] == "ok: Jev checked 3 claims"


def test_sector_moves_measure_the_last_closed_session_against_spy():
    frame = pd.DataFrame({"XLE": [100.0, 102.0], "SPY": [500.0, 501.0]},
                         index=pd.to_datetime(["2026-09-29", "2026-09-30"]))
    asked = []
    moves, window = prices.sector_moves(["energy"], date(2026, 9, 30),
                                        lambda symbols, start, end: asked.append(symbols) or frame)
    assert asked == [["SPY", "XLE"]]
    assert moves["energy"] == pytest.approx((2.0, 1.8))
    assert all(type(value) is float for value in moves["energy"])  # numpy floats break the checkpoint
    assert window.startswith("Close 29 Sep to close 30 Sep")


def test_price_without_sectors_asks_for_no_prices():
    assert prices.price(State(as_of="2026-09-30")) == {"moves": {}, "price_window": ""}



def test_reader_keeps_only_articles_it_quotes(monkeypatch):
    notes = research.Notes(readings=[research.Reading(article=1, sentences=["Brent rose 3%."]),
                                     research.Reading(article=0, sentences=[]),
                                     research.Reading(article=7, sentences=["Made up."])])

    class Reader:
        def invoke(self, inputs, config):
            assert "[0] Fed holds" in inputs["messages"][0]["content"] and "[1] Oil jumps" in inputs["messages"][0]["content"]
            return {"structured_response": notes}
    monkeypatch.setattr(research, "create_agent", lambda *args, **kwargs: Reader())
    articles = [Source(url=f"https://a.com/{i}", title=t, published="2026-09-29", excerpt="text")
                for i, t in enumerate(("Fed holds", "Oil jumps"))]
    assert research.read_articles("oil price", articles) == {1: ["Brent rose 3%."]}
