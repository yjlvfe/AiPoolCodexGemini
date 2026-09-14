import pytest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_allowed_models_grid_responsive_integrity():
    """Delegate E: Ensure provider allowed models list adapts to mobile viewports without horizontal clipping."""
    html = (ROOT / "dashboard" / "templates" / "dashboard.html").read_text(encoding="utf-8")

    # 1. Models list class exists with minmax(0, 1fr) preventing grid item blowouts
    assert ".models-list-grid {" in html
    assert "grid-template-columns: repeat(2, minmax(0, 1fr));" in html

    # 2. Under mobile breakpoint (<=480px), models stack into 1 clean column
    assert "@media (max-width: 480px)" in html
    assert ".models-list-grid {" in html
    assert "grid-template-columns: 1fr;" in html

    # 3. Model lists use the CSS class instead of brittle inline 1fr 1fr
    assert 'id="codex-models-list" class="models-list-grid"' in html
    assert 'id="gemini-models-list" class="models-list-grid"' in html
