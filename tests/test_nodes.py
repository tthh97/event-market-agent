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
import digest
import gdelt
import output
import prices
import research
import triage
import verify
from graph import start
from state import Claim, Finding, Move, ScoredStory, Source, State, Story


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
    stories = [Story(**story(f"https://x.com/s{i}").model_dump(exclude={"severity", "confidence"})) for i in range(7)]
    result = triage.triage(State(stories=stories))
    assert [s.url for s in result["severe"]] == [f"https://x.com/s{i}" for i in (6, 5, 4, 3, 2)]


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
    claims = [Claim(text="Brent rose 3%.", source_url="https://a.com/1", source_date="2026-09-29", sector="energy",
                    country="us"),
              Claim(text="Indian lenders face higher funding costs.", source_url="https://a.com/2",
                    source_date="2026-09-29", sector="financials", country="india")]
    moves = [Move(sector="energy", country="us", etf="XLE", benchmark="SPY", move=2.0, vs_benchmark=1.8),
             Move(sector="financials", country="india", etf="INDA", benchmark="ACWI", move=-1.0, vs_benchmark=-0.5)]
    state = State(as_of="2026-09-30", jev_status="ok", severe=[story(severity=2.5)],
                  findings=[Finding(topic="Oil", summary="Oil rose.", claims=claims)],
                  moves=moves, price_window="Close 29 Sep to close 30 Sep.")
    result = output.output(state, {"configurable": {"thread_id": "t1"}}, Runtime(store=store))
    text = open(result["report"], encoding="utf-8").read()
    assert "Jev severity: 2.50 of 3" in text and "Sector: energy, us." in text and "Sector: financials, india." in text
    assert "| energy | us, sector ETF | XLE | +2.0% | +1.8 pts vs SPY |" in text
    assert "| financials | india, whole market | INDA | -1.0% | -0.5 pts vs ACWI |" in text
    assert "Close 29 Sep to close 30 Sep." in text
    assert result["messages"][0].content == text
    with closing(db.connect()) as connection:
        assert connection.execute("SELECT thread_id, as_of, kind FROM run").fetchall() == [("t1", "2026-09-30", "events")]
        assert connection.execute("SELECT rank, severity, topic, summary, themes FROM story").fetchall() == [
            (1, 2.5, "Oil", "Oil rose.", "ECON_OILPRICE")]
        assert connection.execute("SELECT text, sector, country, unverified FROM claim ORDER BY text").fetchall() == [
            ("Brent rose 3%.", "energy", "us", 0), ("Indian lenders face higher funding costs.", "financials", "india", 0)]
        assert connection.execute("SELECT sector, country, etf, benchmark, move, vs_benchmark FROM market_move "
                                  "ORDER BY sector").fetchall() == [("energy", "us", "XLE", "SPY", 2.0, 1.8),
                                                                    ("financials", "india", "INDA", "ACWI", -1.0, -0.5)]
    assert [i.value["topic"] for i in store.search(("findings", "2026-09-30"))] == ["Oil"]


def test_output_writes_an_html_page_next_to_the_markdown(tmp_path, monkeypatch):
    monkeypatch.setattr(output, "FOLDER", tmp_path)
    monkeypatch.setattr(db, "PATH", tmp_path / "events.db")
    claims = [Claim(text="Brent rose 3% <script>alert(1)</script>.", source_url="https://a.com/1",
                    source_date="2026-09-29", sector="energy", country="us", unverified=True),
              Claim(text="A bad link.", source_url="javascript:alert(1)", source_date="unknown")]
    state = State(as_of="2026-09-30", jev_status="ok", severe=[story(severity=2.5)],
                  findings=[Finding(topic="Oil & gas", summary="Oil rose.", claims=claims)],
                  rejected=['Oil: "Brent rose 9%." (number(s) 9 not in the source)'],
                  moves=[Move(sector="energy", country="us", etf="XLE", benchmark="SPY", move=2.0, vs_benchmark=1.8)],
                  price_window="Close 29 Sep to close 30 Sep.")
    result = output.output(state, {"configurable": {"thread_id": "t1"}}, Runtime(store=InMemoryStore()))
    page = open(result["report"].removesuffix(".md") + ".html", encoding="utf-8").read()
    assert page.startswith("<!doctype html>") and "Oil &amp; gas" in page and "2.50 of 3" in page
    assert '<a href="https://a.com/1">' in page and "Unverified" in page and "&lt;script&gt;" in page
    assert "<script>" not in page and 'href="javascript:' not in page
    assert "XLE" in page and "+2.0%" in page and "+1.8 pts" in page and "vs SPY" in page and "Brent rose 9%" in page
    assert "energy · us" in page


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
        (0.95, 0.9, "energy", 0.9, "global", 0.8), (0.6, 0.9, "energy", 0.5, "us", 0.9),
        (0.9, 0.9, "energy", 0.9, "us", 0.5), (0.9, 0.2, "none", 0.9, "us", 0.9)])
    source = Source(url="https://a.com/1", title="Oil jumps", published="2026-09-29",
                    excerpt="Brent rose 3% to $107. Refiners gained. Pipelines shut.")
    claims = [Claim(text=t, source_url=source.url, source_date="unknown")
              for t in ("Brent rose 3%.", "Refiners gained.", "Pipelines shut.", "Brent hit $107.")]
    result = verify.verify(State(as_of="2026-09-30", findings=[Finding(topic="Oil", summary="s", claims=claims)],
                                 sources=[source]))
    kept = result["findings"][0].claims
    # A tag needs Jev sure of both the sector and the country; an unsure country drops the whole tag.
    assert [(c.text, c.unverified, c.sector, c.country) for c in kept] == [
        ("Brent rose 3%.", False, "energy", "global"), ("Refiners gained.", True, None, None),
        ("Pipelines shut.", False, None, None)]
    assert result["rejected"] == ['Oil: "Brent hit $107." (Jev: it is about an earlier period)']
    assert result["verify_status"] == "ok: Jev checked 4 claims"


def test_market_moves_price_us_sectors_against_spy_and_other_markets_against_acwi():
    frame = pd.DataFrame({"XLE": [100.0, 102.0], "SPY": [500.0, 501.0], "INDA": [50.0, 49.0],
                          "IXC": [40.0, 40.4], "ACWI": [100.0, 100.5]},
                         index=pd.to_datetime(["2026-09-29", "2026-09-30"]))
    asked = []
    tags = [("energy", "us"), ("financials", "india"), ("energy", "global")]
    moves, window = prices.market_moves(tags, date(2026, 9, 30),
                                        lambda symbols, start, end: asked.append(symbols) or frame)
    assert asked == [["ACWI", "INDA", "IXC", "SPY", "XLE"]]
    got = {(m.sector, m.country): (m.etf, m.benchmark, m.move, m.vs_benchmark) for m in moves}
    assert got[("energy", "us")] == ("XLE", "SPY", pytest.approx(2.0), pytest.approx(1.8))
    assert got[("financials", "india")] == ("INDA", "ACWI", pytest.approx(-2.0), pytest.approx(-2.5))
    assert got[("energy", "global")] == ("IXC", "ACWI", pytest.approx(1.0), pytest.approx(0.5))
    assert all(type(m.move) is float and type(m.vs_benchmark) is float for m in moves)  # numpy floats break the checkpoint
    assert window.startswith("Close 29 Sep to close 30 Sep")


def test_price_without_sectors_asks_for_no_prices():
    assert prices.price(State(as_of="2026-09-30")) == {"moves": [], "price_window": ""}


def test_old_thread_moves_load_as_us():
    state = State(moves={"energy": (2.0, 1.8)})
    assert state.moves == [Move(sector="energy", country="us", etf="XLE", benchmark="SPY", move=2.0, vs_benchmark=1.8)]


def test_upgrade_moves_old_price_rows_to_us(tmp_path):
    path = tmp_path / "events.db"
    with closing(sqlite3.connect(path)) as connection:
        connection.executescript("""
            CREATE TABLE story (run_id TEXT, rank INTEGER, title TEXT, url TEXT, severity REAL, articles INTEGER,
                                sites INTEGER, PRIMARY KEY (run_id, rank));
            CREATE TABLE claim (run_id TEXT, topic TEXT, text TEXT, source_url TEXT, source_date TEXT, sector TEXT,
                                unverified INTEGER, PRIMARY KEY (run_id, text));
            CREATE TABLE price_move (run_id TEXT, sector TEXT, move REAL, vs_spy REAL, PRIMARY KEY (run_id, sector));
            INSERT INTO claim VALUES ('r1', 'Oil', 'Brent rose 3%.', 'https://a.com/1', '2026-09-29', 'energy', 0);
            INSERT INTO claim VALUES ('r1', 'Oil', 'Talks resumed.', 'https://a.com/1', '2026-09-29', NULL, 0);
            INSERT INTO price_move VALUES ('r1', 'energy', 2.0, 1.8);""")
        connection.commit()
    for _ in range(2):  # a second connect finds nothing left to upgrade
        with closing(db.connect(path)) as connection:
            assert connection.execute("SELECT * FROM market_move").fetchall() == [("r1", "energy", "us", "XLE", "SPY", 2.0, 1.8)]
            assert connection.execute("SELECT text, country FROM claim ORDER BY text").fetchall() == [
                ("Brent rose 3%.", "us"), ("Talks resumed.", None)]
            assert not connection.execute("SELECT 1 FROM sqlite_master WHERE name = 'price_move'").fetchone()
            assert {"topic", "summary", "themes"} <= {row[1] for row in connection.execute("PRAGMA table_info(story)")}



def test_a_link_two_searches_return_keeps_both_excerpts(monkeypatch):
    # 22 Sep run: two searches returned one Reuters URL with different parts of the article, and the
    # second overwrote the first, so verify removed a true claim as "number(s) 40, 6 not in the source".
    url = "https://www.reuters.com/saudi-pipeline"
    excerpts = {"pipeline restarts": "Reaching a rate of 40% of capacity will take a full 6 to 8 weeks.",
                "Aramco Yanbu": "One cargo was scheduled to load at Yanbu, lowest since September 8."}

    class Tavily:
        def __init__(self, api_key):
            pass

        def search(self, query, **kwargs):
            return {"results": [{"url": url, "title": "Saudi restarts pipeline", "published_date": "2026-09-22",
                                 "content": excerpts[query]}]}

    claim = Claim(text="Reaching a rate of 40% of capacity will take a full 6 to 8 weeks.", source_url=url,
                  source_date="2026-09-22")

    class Agent:
        def __init__(self, tools):
            self.search = tools[0]

        def invoke(self, inputs, config):
            for query in excerpts:
                self.search.invoke({"query": query})
            return {"structured_response": Finding(topic="Saudi", summary="s.", claims=[claim])}

    monkeypatch.setenv("TAVILY_API_KEY", "test")
    monkeypatch.setattr(research, "TavilyClient", Tavily)
    monkeypatch.setattr(research, "read_articles", lambda query, articles: {0: [articles[0].excerpt]})
    monkeypatch.setattr(research, "create_agent", lambda model, tools, **kwargs: Agent(tools))
    sources = {}
    research.research_topic("Saudi pipeline", date(2026, 9, 22), sources)
    assert all(text in sources[url].excerpt for text in excerpts.values())
    assert verify.review(claim, sources[url], None) == claim.model_copy(update={"source_date": "2026-09-22"})


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


def test_digest_uses_the_newest_run_per_day_and_files_unknown_themes_under_other(tmp_path, monkeypatch):
    monkeypatch.setattr(output, "FOLDER", tmp_path)
    monkeypatch.setattr(db, "PATH", tmp_path / "events.db")
    claim = Claim(text="Brent rose 3%.", source_url="https://a.com/1", source_date="2026-10-02", sector="energy",
                  country="global")
    move = Move(sector="energy", country="global", etf="IXC", benchmark="ACWI", move=1.0, vs_benchmark=0.5)
    for thread, topic in [("t1", "Old run"), ("t2", "Oil")]:  # two runs of 2 Oct; the second one counts
        state = State(as_of="2026-10-02", jev_status="ok", severe=[story(severity=2.5)],
                      findings=[Finding(topic=topic, summary=f"{topic} summary.", claims=[claim])],
                      moves=[move], price_window="Close 01 Oct to close 02 Oct.")
        output.output(state, {"configurable": {"thread_id": thread}}, Runtime(store=InMemoryStore()))
        with closing(db.connect()) as connection, connection:  # runs made in the same second sort by created_at
            connection.execute("UPDATE run SET created_at = ? WHERE thread_id = ?", (f"2026-10-02 0{thread[1]}:00:00", thread))
    days = digest.load("2026-10-01", "2026-10-04")
    assert [d["day"] for d in days] == ["2026-10-02"]
    assert days[0]["stories"][0]["summary"] == "Oil summary."
    assert days[0]["stories"][0]["claims"][0]["country"] == "global"
    assert days[0]["market"] == [{"sector": "energy", "country": "global", "etf": "IXC", "benchmark": "ACWI",
                                  "move": "+1.0%", "vs": "+0.5 pts"}]
    writing = digest.Writing(headline="Oil week", lead="Oil </script> moved.", themes=[digest.Theme(key="oil", name="Oil")],
                             story_themes=[digest.StoryTheme(story="2026-10-02#1", theme="made-up")],
                             threads=[digest.Thread(theme="oil", days="2 Oct", title="Oil", paragraphs=["Brent rose."])])
    html = digest.page("2026-10-01", "2026-10-04", days, writing)
    assert "/*DATA*/null" not in html and '"theme": "other"' in html and '"key": "other"' in html
    assert "Oil <\\/script> moved." in html and "Oil </script> moved." not in html

