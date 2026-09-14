import pytest
from pathlib import Path

def test_button_design_system_tokens_and_family():
    template_path = Path(__file__).resolve().parents[1] / 'dashboard' / 'templates' / 'dashboard.html'
    content = template_path.read_text(encoding='utf-8')

    # Verify global typography inheritance for buttons
    assert 'button, input, select, textarea {' in content
    assert 'font-family: inherit;' in content

    # Verify global accessible keyboard focus-visible ring
    assert 'button:focus-visible {' in content
    assert 'outline: 2px solid var(--accent-cyan);' in content

    # Verify settings-btn class exists with proper border-radius and cursor
    assert '.settings-btn {' in content
    assert 'border-radius: 8px;' in content
    assert 'cursor: pointer;' in content

    # Verify action-btn class exists with hover and active states
    assert '.action-btn {' in content
    assert '.action-btn:hover {' in content
    assert '.action-btn:active {' in content
    assert '.action-btn:disabled {' in content

    # Verify duplicate conflicting .stream-tok-badge definition was removed
    # Previously appeared at both line ~848 and line ~885
    assert content.count('.stream-tok-badge {') == 1

    # Verify provider-tab-btn uses design system font family instead of unimported 'Cairo'
    assert "font-family: 'Cairo', sans-serif;" not in content
    assert "font-family: 'Plus Jakarta Sans', -apple-system, sans-serif;" in content

    # Verify request-detail-trigger has valid font shorthand / properties
    assert 'font:600 11.5px inherit;' not in content
    assert '.request-detail-trigger{' in content
    assert 'font-family:inherit;' in content
    assert 'font-size:11.5px;' in content
    assert 'font-weight:600;' in content
