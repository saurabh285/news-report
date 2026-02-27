import pytest

import main


def test_summarize_function():
    from ai.tools import summarize
    short = "This is sentence one. Here is two! And three?"
    # default max_sentences=3 should return all three
    assert summarize(short) == short
    # fewer sentences returns subset
    assert summarize(short, max_sentences=1) == "This is sentence one."
    # empty text returns empty
    assert summarize("") == ""


def test_run_free_pipeline(monkeypatch, tmp_path, caplog):
    # prepare fake data
    config = {"feeds": ["feed1"], "email_recipient": "a@b.com", "ai": {"max_per_feed": 5}}

    # stub functions from ai.tools
    called = {}

    def fake_fetch_rss(url):
        called.setdefault('rss', []).append(url)
        return [{"title": "T1", "url": "http://example.com/1", "published_ts": 1}]

    def fake_fetch_article_text(url):
        return {"url": url, "text": "Hello world. Second sentence."}

    def fake_send_email_html(subject, html, to):
        called['email'] = {'subject': subject, 'html': html, 'to': to}
        return {"ok": True, "error": None}

    monkeypatch.setattr(main, '_get_recipient', lambda cfg: config['email_recipient'])
    from ai import tools
    monkeypatch.setattr(tools, 'fetch_rss', fake_fetch_rss)
    monkeypatch.setattr(tools, 'fetch_article_text', fake_fetch_article_text)
    monkeypatch.setattr(tools, 'send_email_html', fake_send_email_html)

    # run free pipeline
    main._run_free(config)

    # verify email was sent
    assert 'email' in called
    assert config['email_recipient'] == called['email']['to']
    assert 'Daily News Digest' in called['email']['subject']
    html = called['email']['html']
    assert '<html' in html.lower()
    assert 'Hello world' in html

    # run again simulating a fallback situation
    called.clear()
    main._run_free(config, fallback=True)
    assert 'email' in called
    assert called['email']['subject'].startswith('[FREE MODE]')
    assert 'Agent unavailable' in called['email']['html']


def test_mode_selection_and_fallback(monkeypatch):
    config_agent = {"feeds": [], "email_recipient": "x", "ai": {"mode": "agent"}}
    # make load_config return our dict
    monkeypatch.setattr(main, 'load_config', lambda path="": config_agent)

    # track calls
    calls = []

    def fail_agent(cfg):
        calls.append('agent')
        raise RuntimeError("boom")

    def good_free(cfg, fallback: bool = False):
        calls.append('free')

    monkeypatch.setattr(main, '_run_agent', fail_agent)
    monkeypatch.setattr(main, '_run_free', good_free)

    # calling main should not raise since fallback succeeds
    main.main()
    assert calls == ['agent', 'free']

    # if free also fails, main should exit with SystemExit
    def bad_free(cfg):
        calls.append('free2')
        raise RuntimeError("ouch")

    monkeypatch.setattr(main, '_run_agent', fail_agent)
    monkeypatch.setattr(main, '_run_free', bad_free)
    with pytest.raises(SystemExit):
        main.main()

    # when mode is explicitly free, agent is never invoked
    config_free = {"feeds": [], "email_recipient": "x", "ai": {"mode": "free"}}
    monkeypatch.setattr(main, 'load_config', lambda path="": config_free)
    # restore good free stub
    monkeypatch.setattr(main, '_run_free', good_free)
    calls.clear()
    main.main()
    assert calls == ['free']
