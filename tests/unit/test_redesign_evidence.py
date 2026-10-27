"""Presentation must not invent state differences from incomplete observations."""
from duress.web import _jinja_environment, render_report


def snapshot(rows, **overrides):
    return {'available': True, 'valid': True, 'settled': True,
            'entities': {'tickets': rows}, **overrides}


def test_state_diff_counts_duplicate_row_contents_without_assuming_identity():
    macro = _jinja_environment().get_template('_evidence_ui.html').module.state_diff
    html = macro(snapshot([{'id': 'A'}]), snapshot([{'id': 'A'}, {'id': 'A'}]))
    assert '1 added row contents · 0 removed row contents' in html
    assert html.count('class="row-added"') == 1


def test_state_diff_refuses_incomplete_observation_and_missing_entity():
    macro = _jinja_environment().get_template('_evidence_ui.html').module.state_diff
    for before in [snapshot([], settled=False), snapshot([], entities={})]:
        html = macro(before, snapshot([{'id': 'A'}]))
        assert 'Comparison unavailable' in html
        assert 'class="row-added"' not in html


def test_portable_report_embeds_themes_and_fonts_without_interactive_buttons():
    html = render_report({'investigation_id': 'report-1', 'status': 'completed',
                          'target_id': 'test', 'hypotheses': [], 'scenarios': [],
                          'trials': [], 'events': [], 'dna_ids': []})
    assert '--bg-canvas: #0C1115' in html
    assert 'data:font/woff2;base64,' in html
    assert '<button' not in html
    assert '<script' not in html


def test_hosted_shell_has_no_external_project_navigation():
    from duress.web import _render
    html = _render('index.html', ui_hosted=True, showcase=None)
    assert 'href="/projects"' not in html
    assert 'href="/guide"' in html
    assert 'New investigation' in html
