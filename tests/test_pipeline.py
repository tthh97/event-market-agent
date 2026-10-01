"""Behavioral checks for data timing, citations, follow-ups, the database and price calculations."""
import json
import sqlite3
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pandas as pd
import pytest

import brief_run
import events_db
import jev_api
import point_in_time
import research_tools
from brief_checks import validate_brief
from brief_report import render_digest_html, render_html, save_reports
from brief_schema import Brief
from events_db import candidates, import_gdelt, load_event, run_view, save_run
from market_returns import calculate_windows


def event():
    return {'tracked_event_id': None, 'title': 'Factory closure', 'event_date': '2026-09-29', 'summary': 'A reported closure.',
            'category': 'operations', 'why_watch': 'Production may be interrupted.', 'source_ids': ['s1'],
            'exposures': [{'sector': 'industrials', 'channel': 'production', 'reasoning': 'Possible interruption.',
                           'status': 'hypothesis', 'source_ids': ['s1']}],
            'tickers': [{'symbol': 'CAT', 'kind': 'stock', 'name': 'Caterpillar', 'reason': 'Operates the plant.',
                         'source_ids': ['s1']}],
            'uncertainty': 'Duration unknown.', 'watch_next': 'Operator reopening statement.', 'status': 'developing'}


def evidence():
    return {'s1': {'source_id': 's1', 'url': 'https://example.com/report', 'title': 'Fixture report',
                   'published_at': '2026-09-29T10:00:00+00:00', 'excerpt': 'Fixture'}}


def payloads(brief):
    return [jev_api.event_payload(e.model_dump(mode='json'), evidence()) for e in brief.events]


def fake_jev(answers):
    """Stand-in for jev_api.typesafe_call returning fixed raw answers, and the requests it received."""
    calls = []

    def call(state, questions, timeout=90):
        calls.append({'state': state, 'questions': questions})
        return {'model': 'jev-test', 'usage': {'input_tokens': 50, 'output_tokens': 5}, 'answers': answers}
    return call, calls


def score(value):
    return {'type': 'score', 'score': value, 'confidence': 0.7, 'legend': {}, 'probabilities': {}}


def scored(brief, answers):
    return jev_api.assess(payloads(brief), fake_jev(answers)[0])


def save(brief, as_of, db, judgements=None, kind='brief', **kwargs):
    judgements = judgements or jev_api.unscored(payloads(brief))
    return save_run(kind, 'q', as_of, brief, evidence(), 1, 'disabled', judgements, db=db, **kwargs)


def fake_news(results):
    """Stand-in for research_tools.tavily_search returning fixed results, and the params it received."""
    calls = []

    def news(query, **params):
        calls.append(params)
        return {'results': results}
    return news, calls


def fake_prices(frame):
    return lambda tickers, start, end: frame[[t for t in tickers if t in frame.columns]]


def test_import_dates_and_idempotency(tmp_path):
    rows = []
    for i, day in enumerate(['20250929', '20260929', '20260929']):
        row = [''] * 58
        for idx, val in {0: str(i), 1: day, 6:'A',16:'B',26:'040',31:'10',32:'2',33:'3',50:'Place',51:'US',56:'20260929',57:'https://example.com/a'}.items():
            row[idx] = val
        rows.append('\t'.join(row))
    path = tmp_path/'input.CSV'
    path.write_text('\n'.join(rows))
    db = tmp_path/'test.db'
    assert import_gdelt(path, db)['rows'] == 3
    assert import_gdelt(path, db)['status'] == 'already_imported'
    found = candidates(date(2026,9,29), db=db)['candidates']
    assert found[0]['raw_rows'] == 2
    assert found[0]['cameo_codes'] == '040'
    assert candidates(date(2025,9,29), db=db)['candidates'] == []
    path.write_text('bad\trow')
    with pytest.raises(ValueError, match='58 columns'):
        import_gdelt(path, db)


def gdelt_row(i, url, mentions, day='20260929'):
    row = [''] * 58
    for idx, val in {0: str(i), 1: day, 26: '040', 31: str(mentions), 32: '1', 33: '1', 56: day, 57: url}.items():
        row[idx] = val
    return '\t'.join(row)


def test_gdelt_import_keeps_most_mentioned_urls_per_date(tmp_path, monkeypatch):
    monkeypatch.setattr(events_db, 'GDELT_KEEP_URLS', 2)
    rows = [gdelt_row(1, 'https://a.example', 50), gdelt_row(2, 'https://a.example', 5),
            gdelt_row(3, 'https://b.example', 30), gdelt_row(4, 'https://c.example', 10),
            gdelt_row(5, 'https://old.example', 1, day='20260928')]
    result = events_db.import_gdelt_text('\n'.join(rows), '20260929.export.CSV', tmp_path/'test.db')
    assert result['rows'] == 5 and result['rows_kept'] == 4
    found = candidates(date(2026,9,29), db=tmp_path/'test.db')['candidates']
    # Both rows of the top URL survive, so its row count is unchanged. c.example is dropped.
    assert [(c['url'], c['raw_rows']) for c in found] == [('https://a.example', 2), ('https://b.example', 1)]


def test_refresh_gdelt_downloads_unzips_and_skips_known_files(tmp_path, monkeypatch):
    import io
    import zipfile
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr('20260929.export.CSV', gdelt_row(1, 'https://a.example', 9))
    requests = []
    class Response:
        def __init__(self, code, content=b''):
            self.status_code, self.content = code, content
        def raise_for_status(self): pass
    def get(url, **kwargs):
        requests.append(url)
        return Response(200, buffer.getvalue()) if '20260929' in url else Response(404)
    monkeypatch.setattr(events_db.httpx, 'get', get)
    db = tmp_path/'test.db'
    assert events_db.refresh_gdelt(date(2026,9,29), db)['status'] == 'imported'
    assert requests == ['https://data.gdeltproject.org/events/20260929.export.CSV.zip']
    assert candidates(date(2026,9,29), db=db)['candidates'][0]['url'] == 'https://a.example'
    assert events_db.refresh_gdelt(date(2026,9,29), db)['status'] == 'already_imported'
    assert len(requests) == 1
    assert events_db.refresh_gdelt(date(2026,9,30), db)['status'] == 'not_published'


def test_citation_and_future_date_rejection():
    brief = Brief(events=[event()], limitations=[])
    validate_brief(brief, evidence(), date(2026,9,29), 5)
    with pytest.raises(ValueError, match='Unknown citation'):
        validate_brief(brief, {}, date(2026,9,29), 5)
    with pytest.raises(ValueError, match='Event date'):
        validate_brief(brief, evidence(), date(2026,9,28), 5)
    brief.events[0].event_date = None
    with pytest.raises(ValueError, match='publication date'):
        validate_brief(brief, evidence(), date(2026,9,28), 5)


def test_known_by_uses_end_of_day_in_event_timezone_and_never_the_future():
    # Asia/Singapore is UTC+8: 15:59 UTC is still 29 Sep there, 16:00 UTC is 30 Sep.
    assert point_in_time.known_by(datetime(2026,9,29,15,59,tzinfo=UTC), date(2026,9,29))
    assert not point_in_time.known_by(datetime(2026,9,29,16,0,tzinfo=UTC), date(2026,9,29))
    assert not point_in_time.known_by(datetime.now(UTC) + timedelta(hours=1), point_in_time.today() + timedelta(days=5))
    assert point_in_time.parse_timestamp('Tue, 29 Sep 2026 10:00:00 GMT') == datetime(2026,9,29,10,tzinfo=UTC)
    assert point_in_time.parse_timestamp('not a date') is None
    ny_yesterday = datetime.now(point_in_time.NEW_YORK).date() - timedelta(days=1)
    assert point_in_time.last_closed_session_day(date(2030,1,1)) == ny_yesterday
    assert point_in_time.last_closed_session_day(date(2026,9,1)) == date(2026,9,1)


def test_followup_attaches_to_the_saved_event_and_preserves_date(tmp_path):
    db = tmp_path/'test.db'
    brief = Brief(events=[event()], limitations=[])
    _, ids = save(brief, date(2026,9,29), db)
    validate_brief(brief, evidence(), date(2026,9,30), 1, followup_id=ids[0], db=db)
    assert brief.events[0].tracked_event_id == ids[0]
    window = {'ticker': 'XLI', 'window': 'D0_to_D0', 'baseline_date': '2026-09-29', 'start_session': '2026-09-30',
              'end_session': '2026-09-30', 'return_pct': 1.0, 'spy_return_pct': 0.5, 'excess_percentage_points': 0.5}
    _, updated_ids = save(brief, date(2026,9,30), db, kind='followup', market_metrics=[window])
    assert updated_ids == ids
    reloaded = load_event(ids[0], db)
    assert reloaded['as_of'] == '2026-09-30'
    saved = reloaded['event']
    assert (saved['event_id'], saved['title'], saved['event_date'], saved['update']) == \
        (ids[0], 'Factory closure', '2026-09-29', True)
    assert saved['source_ids'] == ['s1'] and saved['exposures'][0]['sector'] == 'industrials'
    assert reloaded['evidence']['s1']['url'] == 'https://example.com/report'
    assert events_db.saved_events(db=db) == [{'EventId': ids[0], 'Title': 'Factory closure', 'EventDate': '2026-09-29',
                                              'LastAssessedOn': '2026-09-30', 'Assessments': 2}]
    with sqlite3.connect(db) as con:
        assert con.execute('SELECT Ticker, ExcessPp FROM MarketWindow').fetchall() == [('XLI', 0.5)]
    moved = Brief(events=[{**event(), 'event_date': '2026-09-30'}], limitations=[])
    with pytest.raises(ValueError, match='original event date'):
        validate_brief(moved, evidence(), date(2026,9,30), 1, followup_id=ids[0], db=db)
    other = Brief(events=[{**event(), 'tracked_event_id': 'elsewhere'}], limitations=[])
    with pytest.raises(ValueError, match='different saved event'):
        validate_brief(other, evidence(), date(2026,9,30), 1, followup_id=ids[0], db=db)
    with pytest.raises(ValueError, match='exactly the tracked event'):
        validate_brief(Brief(events=[], limitations=[]), evidence(), date(2026,9,30), 1, followup_id=ids[0], db=db)


def test_brief_attaches_to_saved_event_instead_of_duplicating(tmp_path):
    # Reproduces the 30 Sep live run: a saved story came back in a later brief under a new ID.
    db = tmp_path/'test.db'
    first = Brief(events=[{**event(), 'event_date': None}], limitations=[])
    _, [saved_id] = save(first, date(2026,9,29), db)
    assert events_db.saved_events(date(2026,9,30), db=db)[0]['EventId'] == saved_id

    repeat = Brief(events=[{**event(), 'tracked_event_id': saved_id}], limitations=[])
    validate_brief(repeat, evidence(), date(2026,9,30), 5, db=db)
    _, ids = save(repeat, date(2026,9,30), db)
    assert ids == [saved_id]
    assert events_db.saved_events(db=db)[0]['Assessments'] == 2
    # The unknown onset date was filled once; now it is fixed.
    assert events_db.get_event(saved_id, db)['EventDate'] == '2026-09-29'
    moved = Brief(events=[{**event(), 'tracked_event_id': saved_id, 'event_date': '2026-09-28'}], limitations=[])
    with pytest.raises(ValueError, match='original event date'):
        validate_brief(moved, evidence(), date(2026,9,30), 5, db=db)

    twice = Brief(events=[repeat.events[0], repeat.events[0]], limitations=[])
    with pytest.raises(ValueError, match='same saved event'):
        validate_brief(twice, evidence(), date(2026,9,30), 5, db=db)
    invented = Brief(events=[{**event(), 'tracked_event_id': 'nope'}], limitations=[])
    with pytest.raises(ValueError, match='Unknown tracked_event_id'):
        validate_brief(invented, evidence(), date(2026,9,30), 5, db=db)


def test_first_sweep_reads_major_outlets_and_flags_every_source():
    news, calls = fake_news([
        {'url': 'https://www.reuters.com/a', 'title': 'R', 'content': 'x', 'published_date': '2026-09-29T01:00:00Z'},
        {'url': 'https://smallsite.example/b', 'title': 'S', 'content': 'x', 'published_date': '2026-09-29T02:00:00Z'}])
    session = research_tools.ResearchSession(date(2026,9,29), 2, news=news)
    flags = {s['url']: s['major_outlet'] for s in session.search('markets', major_outlets_only=True)['sources']}
    assert flags == {'https://www.reuters.com/a': True, 'https://smallsite.example/b': False}
    assert 'reuters.com' in calls[0]['include_domains']
    session.search('markets')
    assert 'include_domains' not in calls[1]
    assert not research_tools.is_major_outlet('https://notreuters.com/x')


def test_market_weekend_baseline_and_incomplete_windows():
    close = pd.DataFrame({'XLI':[100,110,121], 'SPY':[100,105,105]}, index=pd.to_datetime(['2026-09-25','2026-09-28','2026-09-29']))
    rows = calculate_windows(close, date(2026,9,26), date(2026,9,28))
    assert len(rows) == 1
    assert rows[0]['baseline_date'] == '2026-09-25'
    assert rows[0]['return_pct'] == pytest.approx(10)
    assert rows[0]['excess_percentage_points'] == pytest.approx(5)


def test_search_budget_dates_and_tool_invocation(tmp_path):
    news, _ = fake_news([
        {'url':'https://example.com/good','title':'Good','content':'Evidence','published_date':'2026-09-29T01:00:00Z'},
        {'url':'https://example.com/future','published_date':'2026-10-02T00:00:00Z'},
        {'url':'https://example.com/unknown'}])
    tools = research_tools.make_tools(research_tools.ResearchSession(date(2026,9,29), 1, news=news), tmp_path/'test.db')
    assert 'error' in tools['research_news'].invoke({'query':''})
    found = tools['research_news'].invoke({'query':'factory'})
    assert len(found['sources']) == 1
    assert 'error' in tools['research_news'].invoke({'query':'again'})
    sid = found['sources'][0]['source_id']
    assert tools['read_sources'].invoke({'source_ids':[sid]})[sid]['title'] == 'Good'
    assert 'error' in tools['read_sources'].invoke({'source_ids':['missing']})['missing']
    assert 'error' in tools['read_sources'].invoke({'source_ids':[]})
    unconfigured = research_tools.ResearchSession(date(2026,9,29), 1)
    assert unconfigured.search('factory') == {'error': 'News search is not configured.'}


def gpr_response(monkeypatch, frame):
    class Response:
        content = b'fixture'
        def raise_for_status(self): pass
    monkeypatch.setattr(events_db.httpx, 'get', lambda *a, **k: Response())
    monkeypatch.setattr(events_db.pd, 'read_excel', lambda *a, **k: frame)


def test_gpr_future_vintage_excluded(tmp_path, monkeypatch):
    db = tmp_path/'test.db'
    assert events_db.gpr_context(date(2026,9,30), db)['status'] == 'not_downloaded'
    gpr_response(monkeypatch, pd.DataFrame({'date':pd.to_datetime(['2026-09-28']), 'GPRD':[123.0]}))
    events_db.refresh_gpr(db)
    with sqlite3.connect(db) as con:
        con.execute("UPDATE DataImport SET ImportedAt='2026-09-30T16:30:00+00:00' WHERE Source='gpr'")
    assert events_db.gpr_context(date(2026,9,30), db)['status'] == 'excluded_for_historical_as_of'
    assert events_db.gpr_context(date(2026,10,1), db)['latest'] == {'date': '2026-09-28', 'gprd': 123.0}


def test_gpr_prefers_real_date_over_numeric_day(tmp_path, monkeypatch):
    gpr_response(monkeypatch, pd.DataFrame({'DAY':[20260928], 'date':pd.to_datetime(['2026-09-28']), 'GPRD':[123.0]}))
    assert events_db.refresh_gpr(tmp_path/'test.db')['latest_observation'] == '2026-09-28'


def test_html_report_puts_watch_list_first_and_escapes_text(tmp_path):
    db = tmp_path/'test.db'
    run_id, [event_id] = save(Brief(events=[{**event(), 'title': 'Plant <script>alert(1)</script> closure'}],
                                    limitations=[]), date(2026,9,29), db)
    page = render_html(run_view(run_id, db), date(2026,9,29), ['Fixture only'])
    assert page.index('Keep an eye on') < page.index('Details')
    assert '<script>' not in page and '&lt;script&gt;' in page
    assert f'href="#{event_id}"' in page and f'id="{event_id}"' in page
    assert 'https://example.com/report' in page and 'Fixture only' in page


def test_report_lists_saved_sources_and_exposure_type(tmp_path):
    db = tmp_path/'test.db'
    run_id, _ = save(Brief(events=[event()], limitations=[]), date(2026,9,29), db)
    page = render_html(run_view(run_id, db), date(2026,9,29), ['Fixture only'])
    assert 'https://example.com/report' in page and 'Fixture report' in page
    assert '<td>hypothesis</td>' in page and 'tag new">New' in page
    assert 'Fixture only' in page


def citing(source_id):
    """The fixture event, citing source_id everywhere."""
    e = event()
    e['source_ids'] = e['exposures'][0]['source_ids'] = e['tickers'][0]['source_ids'] = [source_id]
    return e


def test_runner_saves_validated_artifacts_with_stand_in_adapters(tmp_path):
    db = tmp_path/'test.db'
    news, _ = fake_news([{'url': 'https://example.com/report', 'title': 'Fixture report', 'content': 'Fixture',
                          'published_date': '2026-09-29T10:00:00Z'}])
    packets = []

    def agent(tools):
        assert set(tools) == {'research_news', 'read_sources', 'read_sql'}

        def invoke(message, config):
            # Like the lead: read the packet and cite a source the sweep retrieved.
            packet = json.loads(message['messages'][0]['content'])
            packets.append(packet)
            source_id = packet['search_results'][0]['sources'][0]['source_id']
            return {'structured_response': Brief(events=[citing(source_id)], limitations=['Offline fixture'])}
        return SimpleNamespace(invoke=invoke)

    prices = fake_prices(pd.DataFrame({'CAT': [1.0]}, index=pd.to_datetime(['2026-09-29'])))
    adapters = brief_run.Adapters(news=news, agent=agent, prices=prices, jev=None, gdelt=lambda day: None, db=db)
    result = brief_run.run(brief_run.Request(as_of=date(2026,9,29), question='fixture', jev=False), adapters)
    assert packets[0]['gdelt']['refresh'] == 'not_published' and packets[0]['max_events'] == 5
    assert result.judgements.status == 'disabled' and result.unlisted == []
    report = save_reports(result, tmp_path/'output', db)
    content = json.loads(report.with_suffix('.json').read_text())
    assert content['brief']['events'][0]['title'] == 'Factory closure'
    assert content['jev_judgements']['events'][0]['tickers'][0]['rank'] == 1
    assert load_event(result.event_ids[0], db)['event']['title'] == 'Factory closure'
    assert 'Offline fixture' in report.read_text() and report.with_suffix('.html').exists()


def test_followup_run_reuses_the_event_and_measures_sector_windows(tmp_path):
    db = tmp_path/'test.db'
    _, [event_id] = save(Brief(events=[event()], limitations=[]), date(2026,9,29), db)
    agent = lambda tools: SimpleNamespace(  # noqa: E731
        invoke=lambda message, config: {'structured_response': Brief(events=[event()], limitations=[])})
    close = pd.DataFrame({'XLI': [100.0, 103.0], 'SPY': [100.0, 101.0], 'CAT': [1.0, 1.0]},
                         index=pd.to_datetime(['2026-09-29', '2026-09-30']))
    adapters = brief_run.Adapters(news=fake_news([])[0], agent=agent, prices=fake_prices(close), jev=None,
                                  gdelt=lambda day: None, db=db)
    result = brief_run.run(brief_run.Request(as_of=date(2026,9,30), event_id=event_id, jev=False), adapters)
    assert result.event_ids == [event_id]
    assert [(m['ticker'], m['window']) for m in result.market['metrics']] == [('XLI', 'D0_to_D0')]
    assert result.market['metrics'][0]['excess_percentage_points'] == pytest.approx(2.0)
    assert run_view(result.run_id, db)[0]['update'] is True
    with pytest.raises(ValueError, match='precedes'):
        brief_run.run(brief_run.Request(as_of=date(2026,9,28), event_id=event_id), adapters)


def test_read_sql_is_read_only_and_uses_chinook_style_tables(tmp_path):
    db = tmp_path/'test.db'
    save(Brief(events=[event()], limitations=[]), date(2026,9,29), db)
    read_sql = research_tools.make_tools(research_tools.ResearchSession(date(2026,9,29)), db)['read_sql']
    result = read_sql.invoke({'query': 'SELECT e.Title, x.SectorId FROM Event e '
                              'JOIN Assessment a USING (EventId) JOIN Exposure x USING (AssessmentId)'})
    assert result.splitlines() == ['Title | SectorId', 'Factory closure | industrials']
    assert 'XLE' in read_sql.invoke({'query': "SELECT EtfTicker FROM Sector WHERE SectorId = 'energy'"})
    assert read_sql.invoke({'query': 'DELETE FROM Event'}).startswith('Error: only SELECT')
    assert 'readonly' in read_sql.invoke({'query': 'WITH x AS (SELECT 1) DELETE FROM Event'})
    assert read_sql.invoke({'query': 'SELECT * FROM NoSuchTable'}).startswith('Error')


def test_today_uses_time_range_and_past_dates_use_a_date_range():
    # Live test on 30 Sep: a date range returned mostly older articles, time_range the newest.
    today = point_in_time.today()

    def window(as_of, since=None):
        return research_tools.ResearchSession(as_of, 1, since).date_window()

    assert window(today) == {'time_range': 'day'}
    assert window(today, today - timedelta(days=3)) == {'time_range': 'week'}
    assert window(today, today - timedelta(days=20)) == {'time_range': 'month'}
    past = today - timedelta(days=2)
    assert window(past) == {'start_date': str(past - timedelta(days=1)), 'end_date': str(today - timedelta(days=1))}
    assert window(today, today - timedelta(days=60)) == {'start_date': str(today - timedelta(days=61)),
                                                         'end_date': str(today + timedelta(days=1))}


def test_claude_cost_prices_cache_reads_and_writes_separately():
    from models import claude_cost_usd
    usage = {'input_tokens': 1_000_000, 'output_tokens': 100_000,
             'input_token_details': {'cache_read': 600_000, 'ephemeral_5m_input_tokens': 200_000}}
    # 200k uncached at $2, 600k read at $0.20, 200k written at $2.50, 100k out at $10.
    expected = (200_000 * 2 + 600_000 * 0.2 + 200_000 * 2.5 + 100_000 * 10) / 1_000_000
    assert claude_cost_usd('claude-sonnet-5-5-20260901', usage) == pytest.approx(expected)
    assert claude_cost_usd('claude-haiku-4-5-20251001', {'input_tokens': 10, 'output_tokens': 5}) == pytest.approx(35e-6)
    assert claude_cost_usd('unknown-model', usage) is None


def test_jev_assessment_asks_severity_direction_impact_and_ticker_fit_and_is_saved(tmp_path):
    call, calls = fake_jev({'e0_severity': score(2.2), 'e0_x0': {'type': 'choice', 'choice': 'down', 'confidence': 0.8},
                            'e0_x0_impact': score(0.9), 'e0_t0': score(2.6)})
    brief = Brief(events=[event()], limitations=[])
    result = jev_api.assess(payloads(brief), call)
    assert list(calls[0]['questions']) == ['e0_severity', 'e0_x0', 'e0_x0_impact', 'e0_t0']
    assert calls[0]['state']['events'][0]['sources'] == {'s1': 'Fixture'}
    assert calls[0]['state']['events'][0]['tickers'][0]['symbol'] == 'CAT'
    judged = result.events[0]
    assert judged.exposures[0].direction == jev_api.Direction('down', 0.8)
    assert judged.severity == jev_api.Level(2.2, 'high', 0.7)
    assert judged.exposures[0].impact.level == 'small'
    assert judged.tickers[0].fit.level == 'core' and judged.tickers[0].rank == 1

    db = tmp_path/'test.db'
    usage = brief_run.usage_rows({'claude-sonnet-5-5': {'input_tokens': 1000, 'output_tokens': 100}}, result, 3)
    _, ids = save(brief, date(2026,9,29), db, judgements=result, usage=usage)
    with sqlite3.connect(db) as con:
        assert con.execute('SELECT Direction, Confidence, Model FROM ExposureDirection').fetchall() == [('down', 0.8, 'jev-test')]
        assert con.execute('SELECT Level, Score FROM AssessmentSeverity').fetchall() == [('high', 2.2)]
        assert con.execute('SELECT Level FROM ExposureImpact').fetchall() == [('small',)]
        assert con.execute('SELECT Ticker, Rank, JevLevel FROM WatchTicker').fetchall() == [('CAT', 1, 'core')]
        assert {r[0] for r in con.execute('SELECT Service FROM RunUsage')} == {'anthropic', 'jev', 'tavily'}
    fields = ('symbol', 'kind', 'name', 'reason', 'source_ids')
    assert [{k: t[k] for k in fields} for t in load_event(ids[0], db)['event']['tickers']] == event()['tickers']
    month = events_db.cost_summary(datetime.now(UTC).strftime('%Y-%m'), db=db)
    assert month['runs'] == 1 and month['claude_cost_usd'] == pytest.approx((1000 * 2 + 100 * 10) / 1e6, abs=1e-4)
    failing = jev_api.assess(payloads(brief), lambda *a, **k: (_ for _ in ()).throw(TimeoutError()))
    assert failing.status == 'unavailable' and failing.error_type == 'TimeoutError'
    assert failing.events[0].tickers[0].rank == 1


def test_scorecard_asks_jev_for_directions_only():
    call, calls = fake_jev({})
    jev_api.assess([jev_api.event_payload(event(), evidence())], call, parts=('direction',))
    assert list(calls[0]['questions']) == ['e0_x0']


def test_top_three_tickers_follow_jev_fit_and_benchmarks_are_rejected():
    names = ['AAA', 'BBB', 'CCC', 'DDD']
    tickers = [{'symbol': n, 'kind': 'stock', 'name': n, 'reason': 'r', 'source_ids': ['s1']} for n in names]
    brief = Brief(events=[{**event(), 'tickers': tickers}], limitations=[])
    ranked = scored(brief, {'e0_t0': score(0.5), 'e0_t1': score(2.9), 'e0_t3': score(1.5)}).events[0].tickers
    assert [(names[t.index], t.rank) for t in ranked] == [('BBB', 1), ('DDD', 2), ('AAA', 3), ('CCC', None)]
    # Without Jev the lead's order decides.
    unranked = jev_api.unscored(payloads(brief)).events[0].tickers
    assert [names[t.index] for t in unranked if t.rank] == names[:3]

    spy = Brief(events=[{**event(), 'tickers': [{**tickers[0], 'symbol': 'SPY'}]}], limitations=[])
    with pytest.raises(ValueError, match='Benchmark ticker'):
        validate_brief(spy, evidence(), date(2026,9,29), 5)
    twice = Brief(events=[{**event(), 'tickers': [tickers[0], tickers[0]]}], limitations=[])
    with pytest.raises(ValueError, match='Duplicate ticker'):
        validate_brief(twice, evidence(), date(2026,9,29), 5)
    uncited = Brief(events=[{**event(), 'tickers': [{**tickers[0], 'source_ids': ['s9']}]}], limitations=[])
    with pytest.raises(ValueError, match='Unknown citation'):
        validate_brief(uncited, evidence(), date(2026,9,29), 5)


def test_unlisted_tickers_are_dropped_before_jev():
    brief = Brief(events=[{**event(), 'tickers': [event()['tickers'][0], {**event()['tickers'][0], 'symbol': 'ZZZQ'}]}],
                  limitations=[])
    prices = fake_prices(pd.DataFrame({'CAT': [1.0]}, index=pd.to_datetime(['2026-09-29'])))
    assert brief_run.drop_unlisted_tickers(brief, date(2026,9,29), prices) == ['ZZZQ']
    assert [t.symbol for t in brief.events[0].tickers] == ['CAT']
    assert 'ZZZQ' in brief.limitations[0]


def test_html_orders_events_by_jev_severity_and_shows_metrics(tmp_path):
    db = tmp_path/'test.db'
    brief = Brief(events=[{**event(), 'title': 'Mild story'}, {**event(), 'title': 'Big story'}], limitations=[])
    judgements = scored(brief, {'e0_severity': score(0.4), 'e1_severity': score(2.8),
                                'e1_x0_impact': score(2.1), 'e1_t0': score(3.0)})
    run_id, _ = save(brief, date(2026,9,29), db, judgements=judgements)
    events = run_view(run_id, db)
    page = render_html(events, date(2026,9,29), [])
    assert page.index('Big story') < page.index('Mild story')
    assert 'lvl-severe' in page and 'material' in page and '>CAT<' in page
    assert '2.8/3' in page and 'core' in page


def test_scorecard_direction_hits_and_measure_start():
    from evals.scorecard import direction_hit, measure_from
    assert direction_hit('up', 1.2) is True and direction_hit('down', 1.2) is False
    assert direction_hit('down', -0.4) is True
    assert direction_hit('unclear', 2.0) is None and direction_hit('up', None) is None and direction_hit('up', 0) is None
    # A long-running story is measured from its first report, a fresh one from its event date.
    assert measure_from('2026-02-28', '2026-09-17', date(2026,9,17)) == date(2026,9,17)
    assert measure_from('2026-09-16', '2026-09-17', date(2026,9,17)) == date(2026,9,16)
    assert measure_from(None, '2026-09-20', date(2026,9,17)) == date(2026,9,20)


def test_digest_ranks_stories_by_peak_severity_and_keeps_each_day(tmp_path):
    db = tmp_path/'test.db'
    mild = Brief(events=[{**event(), 'title': 'Mild story'}], limitations=[])
    _, [mild_id] = save(mild, date(2026,9,28), db, judgements=scored(mild, {'e0_severity': score(0.6)}))
    big = Brief(events=[{**event(), 'title': 'Big story'}], limitations=[])
    _, [big_id] = save(big, date(2026,9,28), db,
                       judgements=scored(big, {'e0_severity': score(1.8), 'e0_t0': score(2.9)}))
    again = Brief(events=[{**event(), 'title': 'Big story', 'tracked_event_id': big_id}], limitations=[])
    save(again, date(2026,9,29), db, judgements=scored(again, {'e0_severity': score(2.7)}))
    events = events_db.digest(date(2026,9,27), date(2026,9,30), db)
    assert [x['event_id'] for x in events] == [big_id, mild_id]
    assert events[0]['max_severity'] == 2.7 and set(events[0]['days']) == {'2026-09-28', '2026-09-29'}
    assert events[0]['tickers'][0]['symbol'] == 'CAT' and events[0]['update'] is True
    page = render_digest_html(events, date(2026,9,27), date(2026,9,30))
    assert page.index('Big story') < page.index('Also tracked') < page.index('Mild story')
    assert page.count('class="s-') == 2
