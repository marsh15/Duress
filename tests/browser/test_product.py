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


def assert_populated_evidence_accessible(page):
    """Exercise populated tables and badges, including horizontally scrolling data."""
    page.add_script_tag(path=str(ROOT / 'tests/browser/vendor/axe.min.js'))
    violations = page.evaluate("async()=> (await axe.run()).violations.filter(x=>['serious','critical'].includes(x.impact)).map(x=>({id:x.id,nodes:x.nodes.map(n=>n.target)}))")
    assert violations == []
    for width in [390, 768]:
        page.set_viewport_size({'width': width, 'height': 844})
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    page.set_viewport_size({'width': 1440, 'height': 1000})


def test_workspace_navigation_registry_and_available_workflows(page):
    from playwright.sync_api import expect
    errors = []
    page.on('pageerror', lambda exc: errors.append(str(exc)))
    page.goto('/')
    expect(page.get_by_role('link', name='New investigation', exact=True)).to_have_attribute('href', '/investigations#new-investigation')
    expect(page.locator('#target-registry-list')).to_contain_text('razorpay-fixed')
    expect(page.locator('#investigation-history')).to_contain_text('showcase-m2')
    page.get_by_role('link', name='Targets', exact=True).click()
    expect(page.get_by_role('heading', level=1)).to_contain_text('Targets')
    expect(page.get_by_text('127.0.0.1', exact=False).first).to_be_visible()
    page.get_by_role('link', name='Investigations', exact=True).click()
    expect(page.get_by_text('Recorded M2 benchmark', exact=False).first).to_be_visible()
    page.goto('/investigations/showcase-m2')
    expect(page.get_by_role('heading', name='Characterization summary')).to_be_visible()
    expect(page.locator('.explanation-band')).to_contain_text('not a live or autonomous investigation')
    expect(page.get_by_role('heading', name='Recorded verify-fix comparison')).to_be_visible()
    assert errors == []


def test_download_replay_and_fix_verification_use_actual_api(page, server):
    from playwright.sync_api import expect
    page.goto('/dna/' + DNA_ID)
    expect(page.get_by_role('button', name='Start bounded reduction')).to_be_enabled()
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


def test_new_investigation_replay_verify_reduce_and_differential(page, server):
    """One real user journey, reconciled against the persisted service records."""
    from playwright.sync_api import expect

    page.goto('/investigations')
    expect(page.locator('#investigation-mode option[value="live"]')).to_be_disabled()
    page.locator('#investigation-target').select_option('razorpay-vulnerable')
    with page.expect_response(lambda r: r.url.endswith('/api/investigations') and r.request.method == 'POST') as accepted:
        page.get_by_role('button', name='Start investigation', exact=True).click()
    assert accepted.value.status == 202
    admission = accepted.value.json()
    assert admission['status'] == 'queued'
    investigation_id = admission['investigation_id']
    page.wait_for_url('**/investigations/' + investigation_id)
    # Progress lives in persisted records and survives browser navigation.
    page.goto('/')
    page.goto('/investigations/' + investigation_id)
    expect(page.get_by_role('heading', name='Lifecycle · Completed')).to_be_visible(timeout=180_000)
    expect(page.locator('.status-meta')).to_contain_text('FAIL')
    expect(page.locator('main')).to_contain_text('authored cached fixture')
    expect(page.get_by_role('heading', name='Findings', exact=True)).to_be_visible()
    assert_populated_evidence_accessible(page)

    record = httpx.get(server[0] + admission['status_url']).json()
    assert record['status'] == 'completed' and record['verdict'] == 'FAIL'
    assert record['mode_effective'] == 'cached'
    assert record['model']['provenance']['source'] == 'authored_fixture'
    assert record['trials_used'] == 30
    assert all(item['status']['outcome'] == 'confirmed' for item in record['hypotheses'])
    hypotheses = httpx.get(server[0] + admission['status_url'] + '/hypotheses').json()
    findings = httpx.get(server[0] + admission['status_url'] + '/findings').json()
    assert hypotheses['hypotheses'] == record['hypotheses']
    assert findings['findings'] == record['findings']
    assert all('schedule' in scenario for scenario in record['scenarios'])
    dna_id = record['dna_ids'][0]
    page.locator(f'a[href="/dna/{dna_id}"]').first.click()
    expect(page.get_by_role('heading', name='Failure DNA', exact=True)).to_be_visible()
    with page.expect_download() as info:
        page.get_by_role('link', name='Download portable JSON').click()
    downloaded = json.loads(Path(info.value.path()).read_text())
    stored = json.loads((server[1] / 'dna' / (dna_id + '.json')).read_text())
    assert downloaded == stored
    assert stored['reproducer']['failure_signature']['kind'] == 'duplicate_fulfilment'
    # Inspect the actual settled business rows, not HTTP acknowledgments alone.
    snapshot_link = page.get_by_role('link', name='Inspect retained business state')
    expect(snapshot_link).to_be_visible()
    snapshot_link.click()
    expect(page.get_by_role('heading', name='Observed business state')).to_be_visible()
    assert_populated_evidence_accessible(page)
    assert page.locator('section').filter(has=page.get_by_role('heading', name='Fulfilments', exact=True)).locator('tbody tr').count() > 1
    page.goto('/dna/' + dna_id)
    page.locator('#api-token').fill('browser-test-only')
    page.locator('#replay-existing').click()
    expect(page.locator('#replay-result')).to_contain_text('REPRODUCED', timeout=90_000)
    expect(page.locator('#replay-result')).to_contain_text('Preflight: passed')
    page.get_by_role('button', name='Verify fix', exact=True).click()
    expect(page.locator('#verify-result')).to_contain_text('VERIFIED', timeout=120_000)
    expect(page.locator('#verify-result')).to_contain_text('independence assumption')
    with page.expect_response(lambda r: r.url.endswith('/reduce') and r.request.method == 'POST') as reduced:
        page.get_by_role('button', name='Start bounded reduction').click()
    assert reduced.value.status == 202
    reduction_admission = reduced.value.json()
    page.wait_for_url('**/reductions/' + reduction_admission['reduction_id'], timeout=120_000)
    expect(page.get_by_role('heading', name='NOT_REDUCED', exact=True)).to_be_visible()
    expect(page.locator('main')).to_contain_text('no admissible reductions')
    assert_populated_evidence_accessible(page)
    reduction = httpx.get(server[0] + reduction_admission['status_url']).json()
    assert reduction['outcome'] == 'NOT_REDUCED'
    assert reduction['primary_dna_id'] == dna_id
    assert reduction['black_box'] is not None
    assert all(not candidate['admissible'] for candidate in reduction['result']['candidates'])

    page.goto('/differential')
    page.locator('#api-token').fill('browser-test-only')
    page.locator('#differential-policy').select_option('explore')
    with page.expect_response(lambda r: r.url.endswith('/api/differential') and r.request.method == 'POST') as compared:
        page.get_by_role('button', name='Run differential', exact=True).click()
    assert compared.value.status == 202
    # The page immediately navigates on acceptance; Chromium can discard the
    # previous response body. Read the persisted ID from that real navigation.
    page.wait_for_url('**/differential/diff-*')
    differential_id = page.url.rsplit('/', 1)[1]
    expect(page.get_by_role('heading', name='Scenario comparison matrix')).to_be_visible(timeout=180_000)
    assert_populated_evidence_accessible(page)
    comparison = httpx.get(server[0] + '/api/differential/' + differential_id).json()
    assert comparison['differential_id'] == differential_id
    assert comparison['summary'] == {
        'cells': 9, 'compared': 9, 'agree': 9, 'disagree': 0,
        'inconclusive': 0, 'unexpected_cells': [],
    }
    cells = {(x['scenario_id'].split('[')[0], x['variant']): x['observed'] for x in comparison['cells']}
    assert cells[('S3', 'naive_fix')] == 'PASS'
    assert cells[('S5', 'naive_fix')] == 'FAIL'
    assert cells[('S5', 'fixed')] == 'PASS'
    assert not list((server[1] / 'run').glob('*.pid'))


@pytest.mark.parametrize('route', ['/', '/investigations', '/investigations/showcase-m2', '/dna/' + DNA_ID, '/differential'])
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


@pytest.mark.parametrize('theme', ['dark', 'light'])
def test_redesign_themes_navigation_filters_and_mobile_drawer(page, theme):
    from playwright.sync_api import expect
    page.goto('/investigations')
    page.locator('#theme-preference').select_option(theme)
    page.reload()
    expect(page.locator('html')).to_have_attribute('data-theme', theme)
    page.keyboard.press('Control+k')
    expect(page.get_by_role('dialog', name='Navigate workspace')).to_be_visible()
    expect(page.locator('#command-search')).to_be_focused()
    page.locator('#command-search').fill('Failure DNA')
    page.keyboard.press('Enter')
    page.wait_for_url('**/dna')
    expect(page.locator('html')).to_have_attribute('data-theme', theme)
    page.locator('#dna-search').fill('no-such-artifact')
    expect(page.get_by_text('No records match these filters.', exact=True)).to_be_visible()
    page.locator('#dna-search').fill('')
    expect(page.locator('#dna-records article').first).to_be_visible()
    page.goto('/investigations')
    page.locator('#record-search').fill('showcase-m2')
    expect(page.locator('#investigation-records article:visible')).to_have_count(1)
    page.locator('#record-status').select_option('running')
    expect(page.get_by_text('No records match these filters.', exact=True)).to_be_visible()
    page.set_viewport_size({'width': 320, 'height': 720})
    expect(page.get_by_role('button', name='Open navigation', exact=True)).to_be_visible()
    assert page.locator('#app-sidebar').evaluate('(node) => node.inert')
    page.get_by_role('button', name='Open navigation', exact=True).click()
    expect(page.locator('.brand')).to_be_focused()
    page.keyboard.press('Escape')
    expect(page.get_by_role('button', name='Open navigation', exact=True)).to_be_focused()
    assert page.locator('#app-sidebar').evaluate('(node) => node.inert')
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')


@pytest.mark.parametrize('theme', ['dark', 'light'])
@pytest.mark.parametrize('route', ['/guide', '/design-system', '/projects', '/targets',
                                  '/investigations/showcase-m2', '/dna/' + DNA_ID, '/differential'])
def test_redesign_both_themes_axe_and_responsive(page, route, theme):
    page.goto(route)
    page.locator('#theme-preference').select_option(theme)
    page.wait_for_load_state('networkidle')
    page.add_script_tag(path=str(ROOT / 'tests/browser/vendor/axe.min.js'))
    violations = page.evaluate("async()=> (await axe.run()).violations.filter(x=>['serious','critical'].includes(x.impact)).map(x=>({id:x.id,nodes:x.nodes.map(n=>n.target)}))")
    assert violations == []
    for width in [320, 430, 768, 1024, 1600]:
        page.set_viewport_size({'width': width, 'height': 900})
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), (route, theme, width)


def test_schedule_inspector_and_measured_timing_use_retained_evidence(page):
    from playwright.sync_api import expect
    page.goto('/dna/' + DNA_ID)
    lane = page.locator('.schedule-lane').first
    expected = lane.get_attribute('data-inspect')
    lane.click()
    expect(page.get_by_role('dialog', name='Configured delivery', exact=False)).to_be_visible()
    assert json.loads(page.locator('#inspector-content').text_content()) == json.loads(expected)
    page.keyboard.press('Escape')
    expect(lane).to_be_focused()
    assert page.locator('.attempt-bar').count() > 0
    assert page.locator('.timing-scale').inner_text().startswith('Relative axis: 0')
    page.emulate_media(reduced_motion='reduce')
    assert page.evaluate("getComputedStyle(document.querySelector('.button')).transitionDuration") in ['1e-05s', '0.00001s']
