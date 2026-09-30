# tests/test_pipeline.py
"""Behavioral checks for data timing, citations, follow-ups, the database and price calculations."""
import json
import sqlite3
from datetime import date, timedelta

import pandas as pd
import pytest

import cli
import events_db
import jev_api
import lead_agent
import research_tools
from brief_checks import validate_brief
from brief_report import render, render_html
from brief_schema import Brief
from events_db import candidates, import_gdelt, load_event, save_run
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


def test_followup_appends_and_preserves_date(tmp_path):
    db = tmp_path/'test.db'
    brief = Brief(events=[event()], limitations=[])
    _, ids = save_run('brief', 'q', date(2026,9,29), brief, evidence(), 1, 'disabled', db=db)
    prior = load_event(ids[0], db)
    validate_brief(brief, evidence(), date(2026,9,30), 1, prior)
    window = {'ticker': 'XLI', 'window': 'D0_to_D0', 'baseline_date': '2026-09-29', 'start_session': '2026-09-30',
              'end_session': '2026-09-30', 'return_pct': 1.0, 'spy_return_pct': 0.5, 'excess_percentage_points': 0.5}
    _, updated_ids = save_run('followup', 'q', date(2026,9,30), brief, evidence(), 1, 'disabled',
                              event_id=ids[0], market_metrics=[window], db=db)
    assert updated_ids == ids
    reloaded = load_event(ids[0], db)
    assert reloaded['as_of'] == '2026-09-30'
    assert reloaded['event'] == {**event(), 'tracked_event_id': ids[0]}
    assert reloaded['evidence']['s1']['url'] == 'https://example.com/report'
    assert events_db.list_events(db) == [{'event_id': ids[0], 'as_of': '2026-09-30', 'title': 'Factory closure',
                                         'assessments': 2}]
    with sqlite3.connect(db) as con:
        assert con.execute('SELECT Ticker, ExcessPp FROM MarketWindow').fetchall() == [('XLI', 0.5)]
    brief.events[0].event_date = date(2026,9,30)
    with pytest.raises(ValueError, match='original event date'):
        validate_brief(brief, evidence(), date(2026,9,30), 1, prior)


def test_brief_attaches_to_saved_event_instead_of_duplicating(tmp_path, monkeypatch):
    # Reproduces the 30 Sep live run: a saved story came back in a later brief under a new ID.
    monkeypatch.setattr(events_db, 'DB_PATH', tmp_path/'test.db')
    first = Brief(events=[{**event(), 'event_date': None}], limitations=[])
    _, [saved_id] = save_run('brief', 'q', date(2026,9,29), first, evidence(), 1, 'disabled')
    assert events_db.saved_events(date(2026,9,30))[0]['EventId'] == saved_id

    repeat = Brief(events=[{**event(), 'tracked_event_id': saved_id}], limitations=[])
    validate_brief(repeat, evidence(), date(2026,9,30), 5)
    _, ids = save_run('brief', 'q', date(2026,9,30), repeat, evidence(), 1, 'disabled')
    assert ids == [saved_id]
    assert events_db.list_events()[0]['assessments'] == 2
    # The unknown onset date was filled once; now it is fixed.
    assert events_db.get_event(saved_id)['EventDate'] == '2026-09-29'
    moved = Brief(events=[{**event(), 'tracked_event_id': saved_id, 'event_date': '2026-09-28'}], limitations=[])
    with pytest.raises(ValueError, match='original event date'):
        validate_brief(moved, evidence(), date(2026,9,30), 5)

    twice = Brief(events=[repeat.events[0], repeat.events[0]], limitations=[])
    with pytest.raises(ValueError, match='same saved event'):
        validate_brief(twice, evidence(), date(2026,9,30), 5)
    invented = Brief(events=[{**event(), 'tracked_event_id': 'nope'}], limitations=[])
    with pytest.raises(ValueError, match='Unknown tracked_event_id'):
        validate_brief(invented, evidence(), date(2026,9,30), 5)


def test_first_sweep_reads_major_outlets_and_flags_every_source(monkeypatch):
    monkeypatch.setenv('TAVILY_API_KEY', 'test')
    calls = []
    class Client:
        def __init__(self, **kwargs): pass
        def search(self, **kwargs):
            calls.append(kwargs)
            return {'results': [
                {'url': 'https://www.reuters.com/a', 'title': 'R', 'content': 'x', 'published_date': '2026-09-29T01:00:00Z'},
                {'url': 'https://smallsite.example/b', 'title': 'S', 'content': 'x', 'published_date': '2026-09-29T02:00:00Z'}]}
    monkeypatch.setattr(research_tools, 'TavilyClient', Client)
    session = research_tools.ResearchSession(date(2026,9,29), 2)
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


def test_search_budget_dates_and_tool_invocation(monkeypatch):
    monkeypatch.setenv('TAVILY_API_KEY', 'test')
    class Client:
        def __init__(self, **kwargs): pass
        def search(self, **kwargs):
            return {'results': [
                {'url':'https://example.com/good','title':'Good','content':'Evidence','published_date':'2026-09-29T01:00:00Z'},
                {'url':'https://example.com/future','published_date':'2026-10-02T00:00:00Z'},
                {'url':'https://example.com/unknown'}]}
    monkeypatch.setattr(research_tools, 'TavilyClient', Client)
    monkeypatch.setattr(research_tools, 'SESSION', research_tools.ResearchSession(date(2026,9,29), 1))
    assert 'error' in research_tools.research_news.invoke({'query':''})
    found = research_tools.research_news.invoke({'query':'factory'})
    assert len(found['sources']) == 1
    assert 'error' in research_tools.research_news.invoke({'query':'again'})
    sid = found['sources'][0]['source_id']
    assert research_tools.read_sources.invoke({'source_ids':[sid]})[sid]['title'] == 'Good'
    assert 'error' in research_tools.read_sources.invoke({'source_ids':['missing']})['missing']
    assert 'error' in research_tools.read_sources.invoke({'source_ids':[]})


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


def test_html_report_puts_watch_list_first_and_escapes_text():
    risky = {**event(), 'title': 'Plant <script>alert(1)</script> closure'}
    page = render_html(Brief(events=[risky], limitations=['Fixture only']), evidence(), date(2026,9,29), ['abc123'])
    assert page.index('Keep an eye on') < page.index('Details')
    assert '<script>' not in page and '&lt;script&gt;' in page
    assert 'href="#abc123"' in page and 'id="abc123"' in page
    assert 'https://example.com/report' in page and 'Fixture only' in page


def test_render_uses_saved_sources_and_code_metrics():
    result = render(Brief(events=[event()], limitations=['Fixture only']), evidence(), date(2026,9,29), ['test'])
    assert 'https://example.com/report' in result
    assert 'hypothesis' in result
    assert 'Fixture only' in result


def test_jev_batches_independent_questions(monkeypatch):
    monkeypatch.setenv('TYPESAFE_API_KEY', 'test')
    calls = []
    class Response:
        def model_dump(self, **kwargs):
            return {'answers':{}, 'usage':{}}
    class Client:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def system_one(self, **kwargs):
            calls.append(kwargs)
            return Response()
    monkeypatch.setattr(jev_api, 'TypeSafeClient', Client)
    assert jev_api.classify(evidence())['status'] == 'ok'
    assert len(calls) == 1
    assert len(calls[0]['questions']) == 2
    monkeypatch.delenv('TYPESAFE_API_KEY')
    assert jev_api.classify(evidence())['status'] == 'not_configured'


def test_runner_saves_validated_artifacts_without_live_model(tmp_path, monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setenv('ANTHROPIC_API_KEY', 'test')
    monkeypatch.setenv('TAVILY_API_KEY', 'test')
    monkeypatch.setattr(cli, 'OUTPUT', tmp_path/'output')
    monkeypatch.setattr(events_db, 'DB_PATH', tmp_path/'test.db')
    def search(self, query, major_outlets_only=False):
        self.calls += 1
        self.evidence.update(evidence())
        return {'sources':list(self.evidence.values())}
    monkeypatch.setattr(research_tools.ResearchSession, 'search', search)
    fake = SimpleNamespace(invoke=lambda *a, **k: {'structured_response':Brief(events=[event()], limitations=['Offline fixture'])})
    monkeypatch.setattr(lead_agent, 'agent', fake)
    monkeypatch.setattr(cli, 'listed', lambda symbols, as_of: (set(symbols), 'ok'))
    cli.main(['brief','fixture','--date','2026-09-29','--no-jev'])
    files = list((tmp_path/'output').glob('*.json'))
    assert len(files) == 1
    content = json.loads(files[0].read_text())
    assert content['brief']['events'][0]['title'] == 'Factory closure'
    assert load_event(content['event_ids'][0], db=tmp_path/'test.db')['event']['title'] == 'Factory closure'
    assert files[0].with_suffix('.md').exists()
    assert files[0].with_suffix('.html').exists()


def test_read_sql_is_read_only_and_uses_chinook_style_tables(tmp_path, monkeypatch):
    db = tmp_path/'test.db'
    monkeypatch.setattr(events_db, 'DB_PATH', db)
    save_run('brief', 'q', date(2026,9,29), Brief(events=[event()], limitations=[]), evidence(), 1, 'disabled')
    result = research_tools.read_sql.invoke({'query': 'SELECT e.Title, x.SectorId FROM Event e '
                                             'JOIN Assessment a USING (EventId) JOIN Exposure x USING (AssessmentId)'})
    assert result.splitlines() == ['Title | SectorId', 'Factory closure | industrials']
    assert 'XLE' in research_tools.read_sql.invoke({'query': "SELECT EtfTicker FROM Sector WHERE SectorId = 'energy'"})
    assert research_tools.read_sql.invoke({'query': 'DELETE FROM Event'}).startswith('Error: only SELECT')
    assert 'readonly' in research_tools.read_sql.invoke({'query': 'WITH x AS (SELECT 1) DELETE FROM Event'})
    assert research_tools.read_sql.invoke({'query': 'SELECT * FROM NoSuchTable'}).startswith('Error')


def test_today_uses_time_range_and_past_dates_use_a_date_range():
    # Live test on 30 Sep: a date range returned mostly older articles, time_range the newest.
    today = research_tools.datetime.now(research_tools.TIMEZONE).date()

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


def fake_jev(monkeypatch, answers):
    """Stand-in Jev client returning fixed raw answers. Returns the list of requests it received."""
    monkeypatch.setenv('TYPESAFE_API_KEY', 'test')
    calls = []
    class Response:
        def model_dump(self, **kwargs):
            return {'model': 'jev-test', 'usage': {'input_tokens': 50, 'output_tokens': 5}, 'answers': answers}
    class Client:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def system_one(self, **kwargs):
            calls.append(kwargs)
            return Response()
    monkeypatch.setattr(jev_api, 'TypeSafeClient', Client)
    return calls


def score(value):
    return {'type': 'score', 'score': value, 'confidence': 0.7, 'legend': {}, 'probabilities': {}}


def test_jev_assessment_asks_severity_direction_impact_and_ticker_fit_and_is_saved(tmp_path, monkeypatch):
    calls = fake_jev(monkeypatch, {'e0_severity': score(2.2), 'e0_x0': {'type': 'choice', 'choice': 'down', 'confidence': 0.8},
                                   'e0_x0_impact': score(0.9), 'e0_t0': score(2.6)})
    brief = Brief(events=[event()], limitations=[])
    result = jev_api.assess([jev_api.event_payload(brief.events[0].model_dump(mode='json'), evidence())])
    assert list(calls[0]['questions']) == ['e0_severity', 'e0_x0', 'e0_x0_impact', 'e0_t0']
    assert calls[0]['state']['events'][0]['sources'] == {'s1': 'Fixture'}
    assert calls[0]['state']['events'][0]['tickers'][0]['symbol'] == 'CAT'
    assert result['answers']['e0_x0'] == {'direction': 'down', 'confidence': 0.8}
    assert result['answers']['e0_severity'] == {'score': 2.2, 'level': 'high', 'confidence': 0.7}
    assert result['answers']['e0_x0_impact']['level'] == 'small'
    assert result['answers']['e0_t0']['level'] == 'core'

    db = tmp_path/'test.db'
    usage = cli.usage_rows({'claude-sonnet-5-5': {'input_tokens': 1000, 'output_tokens': 100}},
                           {'status': 'disabled'}, result, 3)
    _, ids = save_run('brief', 'q', date(2026,9,29), brief, evidence(), 3, 'disabled', judgements=result, usage=usage, db=db)
    with sqlite3.connect(db) as con:
        assert con.execute('SELECT Direction, Confidence, Model FROM ExposureDirection').fetchall() == [('down', 0.8, 'jev-test')]
        assert con.execute('SELECT Level, Score FROM AssessmentSeverity').fetchall() == [('high', 2.2)]
        assert con.execute('SELECT Level FROM ExposureImpact').fetchall() == [('small',)]
        assert con.execute('SELECT Ticker, Rank, JevLevel FROM WatchTicker').fetchall() == [('CAT', 1, 'core')]
        assert {r[0] for r in con.execute('SELECT Service FROM RunUsage')} == {'anthropic', 'jev', 'tavily'}
    assert load_event(ids[0], db)['event']['tickers'] == event()['tickers']
    month = events_db.cost_summary(research_tools.datetime.now(research_tools.UTC).strftime('%Y-%m'), db=db)
    assert month['runs'] == 1 and month['claude_cost_usd'] == pytest.approx((1000 * 2 + 100 * 10) / 1e6, abs=1e-4)


def test_scorecard_asks_jev_for_directions_only(monkeypatch):
    calls = fake_jev(monkeypatch, {})
    jev_api.assess([jev_api.event_payload(event(), evidence())], parts=('direction',))
    assert list(calls[0]['questions']) == ['e0_x0']


def test_top_three_tickers_follow_jev_fit_and_benchmarks_are_rejected():
    names = ['AAA', 'BBB', 'CCC', 'DDD']
    tickers = [{'symbol': n, 'kind': 'stock', 'name': n, 'reason': 'r', 'source_ids': ['s1']} for n in names]
    answers = {'e0_t0': {'score': 0.5}, 'e0_t1': {'score': 2.9}, 'e0_t3': {'score': 1.5}}
    ranked = jev_api.ranked_tickers({**event(), 'tickers': tickers}, 0, answers)
    assert [(t['symbol'], t['rank']) for t in ranked] == [('BBB', 1), ('DDD', 2), ('AAA', 3), ('CCC', None)]
    # Without Jev the lead's order decides.
    assert [t['symbol'] for t in jev_api.ranked_tickers({**event(), 'tickers': tickers}, 0, {}) if t['rank']] == names[:3]

    spy = Brief(events=[{**event(), 'tickers': [{**tickers[0], 'symbol': 'SPY'}]}], limitations=[])
    with pytest.raises(ValueError, match='Benchmark ticker'):
        validate_brief(spy, evidence(), date(2026,9,29), 5)
    twice = Brief(events=[{**event(), 'tickers': [tickers[0], tickers[0]]}], limitations=[])
    with pytest.raises(ValueError, match='Duplicate ticker'):
        validate_brief(twice, evidence(), date(2026,9,29), 5)
    uncited = Brief(events=[{**event(), 'tickers': [{**tickers[0], 'source_ids': ['s9']}]}], limitations=[])
    with pytest.raises(ValueError, match='Unknown citation'):
        validate_brief(uncited, evidence(), date(2026,9,29), 5)


def test_unlisted_tickers_are_dropped_before_jev(monkeypatch):
    brief = Brief(events=[{**event(), 'tickers': [event()['tickers'][0], {**event()['tickers'][0], 'symbol': 'ZZZQ'}]}],
                  limitations=[])
    monkeypatch.setattr(cli, 'listed', lambda symbols, as_of: ({'CAT'}, 'ok'))
    assert cli.drop_unlisted_tickers(brief, date(2026,9,29)) == ['ZZZQ']
    assert [t.symbol for t in brief.events[0].tickers] == ['CAT']
    assert 'ZZZQ' in brief.limitations[0]


def test_html_orders_events_by_jev_severity_and_shows_metrics():
    first, second = {**event(), 'title': 'Mild story'}, {**event(), 'title': 'Big story'}
    judgements = {'answers': {'e0_severity': {'score': 0.4, 'level': 'minor'},
                              'e1_severity': {'score': 2.8, 'level': 'severe'},
                              'e1_x0_impact': {'score': 2.1, 'level': 'material'},
                              'e1_t0': {'score': 3.0, 'level': 'core'}}}
    brief = Brief(events=[first, second], limitations=[])
    page = render_html(brief, evidence(), date(2026,9,29), ['a1', 'b2'], judgements=judgements)
    assert page.index('Big story') < page.index('Mild story')
    assert 'lvl-severe' in page and 'material' in page and '>CAT<' in page
    text = render(brief, evidence(), date(2026,9,29), ['a1', 'b2'], judgements=judgements)
    assert text.index('Big story') < text.index('Mild story')
    assert 'Jev severity:** severe 2.8/3' in text and '1. **CAT**' in text


def test_scorecard_direction_hits_and_measure_start():
    from evals.scorecard import direction_hit, measure_from
    assert direction_hit('up', 1.2) is True and direction_hit('down', 1.2) is False
    assert direction_hit('down', -0.4) is True
    assert direction_hit('unclear', 2.0) is None and direction_hit('up', None) is None and direction_hit('up', 0) is None
    # A long-running story is measured from its first report, a fresh one from its event date.
    assert measure_from('2026-02-28', '2026-09-17', date(2026,9,17)) == date(2026,9,17)
    assert measure_from('2026-09-16', '2026-09-17', date(2026,9,17)) == date(2026,9,16)
    assert measure_from(None, '2026-09-20', date(2026,9,17)) == date(2026,9,20)
