from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_dashboard_uses_canonical_aipool_routes_and_prefixed_api_calls():
    template = (ROOT / "dashboard/templates/dashboard.html").read_text(encoding="utf-8")
    assert "window.location.pathname.toLowerCase().startsWith('/aipool')" in template
    assert "(hasAipool ? '/AiPool' : '') + cleanPath" in template
    assert "let path = '/AiPool/Dashboard'" in template
    assert "if (page === 'api') path = '/AiPool/API'" in template
    assert "else if (page === 'settings') path = '/AiPool/Settings'" in template


def test_dashboard_never_persists_or_accepts_bearer_sessions_in_browser_urls():
    template = (ROOT / "dashboard/templates/dashboard.html").read_text(encoding="utf-8")
    server = (ROOT / "dashboard/server.py").read_text(encoding="utf-8")
    assert "localStorage" not in template
    assert "X-Session-ID" not in server
    assert "query_session" not in server
    assert 'qs.get("session"' not in server
    assert 'qs.get("session_id"' not in server
    assert 'qs.get("token", [""])[0] if path == "/auth" else ""' in server
    assert "sensitive_query_keys" in server
    assert "url.search = '';" in template
