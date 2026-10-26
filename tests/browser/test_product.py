"""Real browser coverage against only the repository's local application.

DURESS_BROWSER_TESTS=1 uv run pytest -q tests/browser
Install the dev browser once with `uv run playwright install chromium`.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import httpx
import pytest

pytestmark = [pytest.mark.browser, pytest.mark.skipif(
    os.getenv("DURESS_BROWSER_TESTS") != "1", reason="opt in with DURESS_BROWSER_TESTS=1")]
ROOT = Path(__file__).resolve().parents[2]
DNA_ID = "e8007dd56adae8ae"


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    home = tmp_path_factory.mktemp("browser-home")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    command = (
        "from pathlib import Path; from duress.api import create_app,Settings; import uvicorn; "
        f"uvicorn.run(create_app(Settings(root=Path({str(ROOT)!r}),token='browser-test-only',"
        f"per_ip_per_hour=1000,per_day=1000)),host='127.0.0.1',port={port})"
    )
    env = {**os.environ, "DURESS_HOME": str(home)}
    log = (home / 'server.log').open('w')
    process = subprocess.Popen([sys.executable, "-c", command], cwd=ROOT, env=env,
                               stdout=log, stderr=log)
    url = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            if process.poll() is not None:
                pytest.fail((home / 'server.log').read_text())
            try:
                if httpx.get(url + '/api/health').status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(.1)
        else:
            pytest.fail('local app did not become ready')
        yield url, home
    finally:
        process.terminate()
        process.wait(timeout=20)
        log.close()


@pytest.fixture
def page(server):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(viewport={"width": 1440, "height": 1000},
                                      base_url=server[0], accept_downloads=True)
        page = context.new_page()
        page.set_default_timeout(15_000)
        yield page
        context.close()
        browser.close()


def test_workspace_navigation_registry_and_pending_features(page):
    from playwright.sync_api import expect
    errors = []
    page.on('pageerror', lambda exc: errors.append(str(exc)))
    page.goto('/')
    expect(page.get_by_role('button', name='Investigation unavailable')).to_be_disabled()
    expect(page.locator('#target-registry-list')).to_contain_text('razorpay-fixed')
    expect(page.locator('#investigation-history')).to_contain_text('showcase-m2')
    page.get_by_role('link', name='Targets', exact=True).click()
    expect(page.get_by_role('heading', level=1)).to_contain_text('Targets')
    expect(page.get_by_text('127.0.0.1', exact=False).first).to_be_visible()
    page.get_by_role('link', name='Investigations', exact=True).click()
    expect(page.get_by_text('Recorded M2 benchmark', exact=False).first).to_be_visible()
    page.goto('/investigations/showcase-m2')
    expect(page.get_by_role('heading', name='Characterization summary')).to_be_visible()
    expect(page.get_by_text('not a live or autonomous investigation', exact=False)).to_be_visible()
    expect(page.get_by_role('heading', name='Recorded verify-fix comparison')).to_be_visible()
    assert errors == []


def test_download_replay_and_fix_verification_use_actual_api(page, server):
    from playwright.sync_api import expect
    page.goto('/dna/' + DNA_ID)
    expect(page.get_by_role('button', name='Reduction unavailable')).to_be_disabled()
    with page.expect_download() as info:
        page.get_by_role('link', name='Download portable JSON').click()
    downloaded = json.loads(Path(info.value.path()).read_text())
    assert downloaded['dna_id'] == DNA_ID
    assert downloaded['reproducer']['release_condition'] == 'parties'
    page.locator('#api-token').fill('browser-test-only')
    page.locator('#replay-existing').click()
    expect(page.locator('#replay-result')).to_contain_text('REPRODUCED', timeout=90_000)
    expect(page.locator('#replay-result')).to_contain_text('10')
    page.get_by_role('button', name='Verify fix', exact=True).click()
    expect(page.locator('#verify-result')).to_contain_text('VERIFIED', timeout=120_000)
    expect(page.locator('#verify-result')).to_contain_text('independence assumption')
    expect(page.locator('#verify-result')).to_contain_text('20')
    link = page.locator('#verify-result a[href^="/operations/"]')
    expect(link).to_have_count(1)
    link.click()
    expect(page.get_by_role('heading', level=1)).to_contain_text('Verified')
    expect(page.locator('main')).to_contain_text('VERIFIED')
    # Both outcomes were saved from real service responses, not browser fixtures.
    records = [json.loads(path.read_text()) for path in (server[1] / 'operations').glob('*.json')]
    assert any(x['kind'] == 'replay' and x['payload']['outcome']['outcome'] == 'REPRODUCED' for x in records)
    assert any(x['kind'] == 'verify_fix' and x['payload']['outcome'] == 'VERIFIED' for x in records)
    assert not list((server[1] / 'run').glob('*.pid'))


def test_form_errors_tamper_rejection_and_token_stays_in_memory(page, tmp_path):
    from playwright.sync_api import expect
    page.goto('/dna/' + DNA_ID)
    page.locator('#replay-import').click()
    expect(page.locator('#import-message')).to_contain_text('Choose a JSON file')
    page.locator('#replay-existing').click()
    expect(page.locator('#replay-result')).to_contain_text('token', timeout=15_000)
    page.locator('#api-token').fill('browser-test-only')
    document = json.loads((ROOT / 'showcase/dna' / (DNA_ID + '.json')).read_text())
    document['reproducer']['schedule']['deliveries'][0]['event_id'] = 'changed'
    artifact = tmp_path / 'tampered.json'
    artifact.write_text(json.dumps(document))
    page.locator('#import-file').set_input_files(artifact)
    page.locator('#replay-import').click()
    expect(page.locator('#import-result')).to_contain_text('REJECTED', timeout=15_000)
    expect(page.locator('#import-result')).to_contain_text('tampered:digest')
    expect(page.locator('#import-message')).to_contain_text('rejected before execution')
    page.reload()
    expect(page.locator('#api-token')).to_have_value('')
    assert page.evaluate('JSON.stringify(localStorage)') == '{}'


@pytest.mark.parametrize('route', ['/', '/investigations/showcase-m2', '/dna/' + DNA_ID])
def test_axe_accessibility_mobile_and_keyboard(page, route):
    from playwright.sync_api import expect
    page.goto(route)
    page.wait_for_load_state('networkidle')
    page.add_script_tag(path=str(ROOT / 'tests/browser/vendor/axe.min.js'))
    violations = page.evaluate("async()=> (await axe.run()).violations.filter(x=>['serious','critical'].includes(x.impact)).map(x=>({id:x.id,nodes:x.nodes.map(n=>n.target)}))")
    assert violations == []
    page.keyboard.press('Tab')
    expect(page.get_by_role('link', name='Skip to content')).to_be_focused()
    for width in [390, 768]:
        page.set_viewport_size({'width': width, 'height': 844})
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), route
        expect(page.get_by_role('heading', level=1)).to_be_visible()


def test_empty_errors_and_escaping(page, server):
    from playwright.sync_api import expect
    home = server[1]
    (home / 'investigations').mkdir(exist_ok=True)
    (home / 'investigations/empty.json').write_text(json.dumps({
        'investigation_id':'empty','target_id':'<img src=x onerror=alert(1)>',
        'status':'error','errors':['<script>alert(1)</script>'],
        'hypotheses':[], 'scenarios':[], 'trials':[], 'dna_ids':[]
    }))
    page.goto('/investigations/empty')
    expect(page.locator('main')).to_contain_text('<script>alert(1)</script>')
    assert page.locator('img[src="x"]').count() == 0
    assert page.locator('script:not([src])').count() == 0
    response = page.goto('/investigations/not-real')
    assert response.status == 404
    expect(page.get_by_role('link', name='Return to workbench')).to_be_visible()
