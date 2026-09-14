import pytest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def test_delegate_c_form_controls_integrity():
    html = (ROOT / "dashboard" / "templates" / "dashboard.html").read_text(encoding="utf-8")
    
    # 1. Verify modal-input focus and danger styles
    assert ".modal-input:focus" in html
    assert "var(--accent-primary, #6366f1)" in html
    assert ".modal-input.modal-input-danger" in html
    assert "modal-input-danger" in html
    
    # 2. Verify settings-btn styling and disabled state
    assert ".settings-btn" in html
    assert ".settings-btn:disabled" in html
    assert "pointer-events: none" in html
    
    # 3. Verify dropdown wrappers and accessibility attributes
    assert 'role="combobox"' in html
    assert 'aria-haspopup="listbox"' in html
    assert 'id="modal-edit-refill-trigger"' in html
    assert 'id="modal-edit-exp-trigger"' in html
    assert "aria-expanded" in html
    
    # 4. Verify relative wrappers for trigger + dropdown (prevents width mismatch)
    assert '<div style="position: relative; flex: 1;">\n                                        <div id="modal-edit-refill-trigger"' in html
    assert '<div style="position: relative; flex: 1;">\n                                        <div id="modal-edit-exp-trigger"' in html
