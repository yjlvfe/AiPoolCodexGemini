"""DOM-only read and update-failure verification; never dismiss failures as success."""
import json
import os
from playwright.sync_api import sync_playwright

base=os.environ.get('AIPOOL_TEST_URL','http://127.0.0.1:18544')
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True,args=['--no-sandbox'])
    errors=[]
    page=browser.new_page()
    page.on('pageerror',lambda e:errors.append(str(e)))
    dimensions=[]
    for width in (320,390,768,1440):
        page.set_viewport_size({'width':width,'height':900})
        page.goto(base,wait_until='domcontentloaded')
        page.wait_for_function('typeof triggerUpdate === "function"')
        page.wait_for_function('document.getElementById("system-version-pill").textContent.startsWith("v")')
        overflow=page.evaluate('document.documentElement.scrollWidth > innerWidth')
        dimensions.append({'width':width,'overflow':overflow})
        assert not overflow,dimensions
    if os.environ.get('AIPOOL_VERIFY_LIVE_LOGS')=='1':
        page.wait_for_function('document.getElementById("activity-stream-list").innerText.includes("Codex") && document.getElementById("activity-stream-list").innerText.includes("Antigravity")')
        print('Live DOM: Codex and Antigravity request cards present')
    page.on('dialog',lambda dialog:dialog.accept())
    page.route('**/api/settings/update',lambda route:route.fulfill(status=200,content_type='application/json',body=json.dumps({'success':False,'message':'Fixture update denied'})))
    page.evaluate('triggerUpdate()')
    assert page.locator('#update-result-title').inner_text()=='Update failed — not completed'
    assert not page.locator('#btn-update-suite').is_disabled()
    page.unroute('**/api/settings/update')
    page.route('**/api/settings/update',lambda route:route.abort())
    page.evaluate('triggerUpdate()')
    assert page.locator('#update-result-title').inner_text()=='Update result not confirmed'
    assert not page.locator('#btn-update-suite').is_disabled()
    assert not errors,errors
    print(json.dumps({'viewports':dimensions,'javascript_errors':errors,'failure_ui':'verified','connection_loss_ui':'verified'}))
    browser.close()
