(() => {
  'use strict';
  const root = document.documentElement;
  const preference = document.querySelector('#theme-preference');
  preference.value = root.dataset.appearance || 'dark';
  preference.addEventListener('change', () => window.duressAppearance(preference.value));

  const sidebar = document.querySelector('#app-sidebar');
  const toggle = document.querySelector('.nav-toggle');
  const scrim = document.querySelector('.nav-scrim');
  const setNavigation = (open) => {
    document.body.classList.toggle('nav-open', open);
    toggle.setAttribute('aria-expanded', String(open));
    scrim.hidden = !open;
    document.querySelector('.app-body').inert = open;
    if (open) { sidebar.setAttribute('role', 'dialog'); sidebar.setAttribute('aria-modal', 'true'); }
    else { sidebar.removeAttribute('role'); sidebar.removeAttribute('aria-modal'); }
    // Inert prevents keyboard focus entering an off-screen mobile drawer.
    sidebar.inert = !open && matchMedia('(max-width: 900px)').matches;
    if (open) sidebar.querySelector('a').focus();
    else if (sidebar.contains(document.activeElement)) toggle.focus();
  };
  toggle.addEventListener('click', () => setNavigation(true));
  document.querySelector('.sidebar-close').addEventListener('click', () => setNavigation(false));
  scrim.addEventListener('click', () => setNavigation(false));
  const mobile = matchMedia('(max-width: 900px)');
  mobile.addEventListener('change', () => setNavigation(false));
  setNavigation(false);
  sidebar.addEventListener('keydown', (event) => {
    if (!document.body.classList.contains('nav-open') || event.key !== 'Tab') return;
    const controls = [...sidebar.querySelectorAll('a,button,select')].filter(x => x.getClientRects().length);
    if (event.shiftKey && document.activeElement === controls[0]) { event.preventDefault(); controls.at(-1).focus(); }
    else if (!event.shiftKey && document.activeElement === controls.at(-1)) { event.preventDefault(); controls[0].focus(); }
  });

  const dialog = document.querySelector('#command-palette');
  const search = document.querySelector('#command-search');
  const results = document.querySelector('#command-results');
  const commands = [
    ...Array.from(document.querySelectorAll('.nav-item'), link => [link.textContent.trim(), link.getAttribute('href')]),
    ['New investigation', '/investigations#new-investigation'], ['Guide and configuration', '/guide'], ['Design system', '/design-system'],
    ['Use dark theme', 'theme:dark'], ['Use light theme', 'theme:light'], ['Use system theme', 'theme:system'],
  ];
  window.addEventListener('duress:projects', event => { for (const project of event.detail) if (project.available) commands.push([`Project: ${project.project}`, `/projects/${encodeURIComponent(project.project)}`]); });
  document.querySelectorAll('[data-project-link]').forEach(link => commands.push([`Project: ${link.textContent.trim()}`, link.getAttribute('href')]));
  const artifact = document.querySelector('#dna-document');
  if (artifact) { try { const id = JSON.parse(artifact.textContent).dna_id; if (id) commands.push(['Copy artifact ID', `copy:${id}`]); } catch (_) {} }
  let selected = 0;
  let opener;
  const highlight = () => [...results.children].forEach((node, index) => node.toggleAttribute('data-selected', index === selected));
  const renderCommands = () => {
    selected = 0;
    results.replaceChildren();
    for (const [label, target] of commands.filter(([label]) => label.toLowerCase().includes(search.value.toLowerCase()))) {
      const special = target.startsWith('theme:') || target.startsWith('copy:');
      const item = document.createElement(special ? 'button' : 'a');
      item.className = 'command-result';
      item.textContent = label;
      if (!special) item.href = target;
      else item.type = 'button';
      item.addEventListener('click', () => {
        if (target.startsWith('theme:')) { preference.value = target.slice(6); window.duressAppearance(preference.value); }
        if (target.startsWith('copy:')) copy(target.slice(5), 'Artifact ID copied');
        closeCommands();
      });
      results.append(item);
    }
    if (!results.childElementCount) { const empty = document.createElement('p'); empty.className = 'empty-note'; empty.textContent = 'No matching pages or actions.'; results.append(empty); }
    highlight();
  };
  const openCommands = () => { opener = document.activeElement; search.value = ''; renderCommands(); dialog.showModal(); search.focus(); };
  const closeCommands = () => { dialog.close(); opener?.focus(); };
  document.querySelectorAll('[data-command-open]').forEach(button => button.addEventListener('click', openCommands));
  document.querySelector('[data-command-close]').addEventListener('click', closeCommands);
  search.addEventListener('input', renderCommands);
  dialog.addEventListener('keydown', event => {
    const items = [...results.querySelectorAll('a,button')];
    if (['ArrowDown', 'ArrowUp'].includes(event.key) && items.length) {
      event.preventDefault(); selected = (selected + (event.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length;
      highlight(); items[selected].scrollIntoView({block: 'nearest'});
    } else if (event.key === 'Enter' && event.target === search) { event.preventDefault(); items[selected]?.click(); }
  });
  dialog.addEventListener('click', event => { if (event.target === dialog) { const rect = dialog.getBoundingClientRect(); if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) closeCommands(); } });
  document.addEventListener('keydown', event => {
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') { event.preventDefault(); dialog.open ? closeCommands() : openCommands(); }
    if (event.key === 'Escape' && document.body.classList.contains('nav-open')) setNavigation(false);
  });

  let toastTimer;
  const notify = message => { const toast = document.querySelector('#workspace-toast'); toast.textContent = message; toast.hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => { toast.hidden = true; }, 3000); };
  async function copy(value, success) {
    try { await navigator.clipboard.writeText(value); notify(success); }
    catch (_) { notify('Copy unavailable. Select the text and copy it manually.'); }
  }
  document.addEventListener('click', event => {
    const button = event.target.closest('[data-copy]');
    if (button) copy(button.dataset.copy, button.dataset.copyMessage || 'Copied');
  });

  // Filters only hide rendered records; they never infer outcomes or alter evidence.
  document.querySelectorAll('[data-record-filter]').forEach(toolbar => {
    const container = document.querySelector(toolbar.dataset.recordFilter);
    if (!container) return;
    const input = toolbar.querySelector('input');
    const status = toolbar.querySelector('[data-filter-status]');
    const sort = toolbar.querySelector('[data-filter-sort]');
    const rows = [...container.children];
    const empty = document.createElement('p'); empty.className = 'filter-empty'; empty.hidden = true; empty.textContent = 'No records match these filters.'; container.after(empty);
    const apply = () => {
      let count = 0;
      for (const row of rows) {
        const match = row.textContent.toLowerCase().includes(input.value.toLowerCase()) && (!status?.value || row.dataset.status === status.value);
        row.hidden = !match; if (match) count++;
      }
      empty.hidden = count > 0;
      if (sort) {
        const ordered = [...rows];
        if (sort.value === 'target') ordered.sort((a,b) => (a.querySelector('h3,h2')?.textContent || a.textContent).localeCompare(b.querySelector('h3,h2')?.textContent || b.textContent));
        ordered.forEach(row => container.append(row));
      }
    };
    toolbar.addEventListener('input', apply); toolbar.addEventListener('change', apply);
  });

  // Generate an in-page index from actual retained sections, preserving native anchors.
  document.querySelectorAll('[data-section-index]').forEach(index => {
    const scope = index.parentElement;
    scope.querySelectorAll('section[aria-labelledby]').forEach(section => {
      const title = document.getElementById(section.getAttribute('aria-labelledby'));
      if (!title || section.closest('section[aria-labelledby]') !== section || section.parentElement.closest('section[aria-labelledby]')) return;
      if (!title.id || title.id === 'progress-title') return;
      const a = document.createElement('a'); a.href = `#${title.id}`; a.textContent = title.textContent.trim(); index.append(a);
    });
    index.hidden = !index.childElementCount;
  });
})();

(() => {
  const inspector = document.querySelector('#evidence-inspector');
  let origin;
  document.addEventListener('click', event => {
    const button = event.target.closest('[data-inspect]');
    if (!button) return;
    origin = button;
    document.querySelector('#inspector-title').textContent = button.dataset.inspectTitle || 'Evidence details';
    try { document.querySelector('#inspector-content').textContent = JSON.stringify(JSON.parse(button.dataset.inspect), null, 2); }
    catch (_) { document.querySelector('#inspector-content').textContent = 'Evidence could not be read.'; }
    inspector.showModal();
  });
  document.querySelector('[data-inspector-close]').addEventListener('click', () => inspector.close());
  inspector.addEventListener('close', () => origin?.focus());
  function initializeTimelines(scope) {
    scope.querySelectorAll('[data-attempt-timeline]:not([data-initialized])').forEach(timeline => {
      timeline.dataset.initialized = 'true';
      const lanes = [...timeline.querySelectorAll('[data-attempt-start]')];
      if (!lanes.length) { timeline.querySelector('.timing-scale').textContent = 'No comparable start/end timings were retained. Inspect the attempt table.'; return; }
      const starts = lanes.map(lane => Number(lane.dataset.attemptStart));
      const ends = lanes.map(lane => Number(lane.dataset.attemptEnd));
      const start = Math.min(...starts); const end = Math.max(...ends); const span = end - start;
      lanes.forEach((lane, i) => {
        const bar = lane.querySelector('.attempt-bar');
        bar.style.left = `${span > 0 ? (starts[i] - start) / span * 100 : 0}%`;
        bar.style.width = `${span > 0 ? (ends[i] - starts[i]) / span * 100 : 0}%`;
      });
      timeline.querySelector('.timing-scale').textContent = `Relative axis: 0 → ${(span * 1000).toFixed(2)} ms from the earliest retained attempt.`;
    });
  }
  initializeTimelines(document);
  document.addEventListener('htmx:afterSwap', () => initializeTimelines(document));
})();

(async () => {
  const readiness = document.querySelector('#workspace-readiness');
  const row = (label, value) => {
    const container = document.createElement('div'); const dt = document.createElement('dt'); const dd = document.createElement('dd');
    dt.textContent = label; dd.textContent = value; container.append(dt, dd); readiness?.append(container);
  };
  try {
    const response = await fetch('/api/health');
    if (!response.ok) throw new Error('Health unavailable');
    const health = await response.json();
    readiness?.replaceChildren(); row('Server', health.status === 'ok' ? 'Available' : 'Unavailable');
    row('Mode', health.hosted ? 'Hosted demonstration' : 'Local-first');
    row('Operation', health.busy ? 'An operation is active' : 'No active operation');
    row('Access', health.token_required ? 'Token required for protected actions' : 'Server access policy applies');
    if (health.hosted) {
      document.querySelectorAll('a[href="/projects"]').forEach(link => {
        if (link.classList.contains('nav-item')) link.hidden = true;
        else { link.href = '/guide'; if (link.id === 'workspace-project-link') link.textContent = 'Local project setup guide →'; }
      });
      return;
    }
    const projectResponse = await fetch('/api/projects');
    if (!projectResponse.ok) { row('Projects', 'Registry unavailable to this client'); return; }
    const {projects = []} = await projectResponse.json();
    row('Registered projects', String(projects.length));
    // Registration is not a connectivity or contract-validation check.
    row('Observer readiness', 'Run doctor in a project to check');
    window.dispatchEvent(new CustomEvent('duress:projects', {detail: projects}));
    const select = document.querySelector('#active-project');
    projects.filter(project => project.available).forEach(project => {
      const option = document.createElement('option'); option.value = `/projects/${encodeURIComponent(project.project)}`; option.textContent = project.project; select.append(option);
      if (location.pathname.startsWith(`${option.value}/`) || location.pathname === option.value) select.value = option.value;
    });
    document.querySelector('#project-switcher').hidden = !projects.length;
    select.addEventListener('change', () => { location.href = select.value; });
    if (projects.length) {
      const title = document.querySelector('#welcome-title');
      if (title) title.textContent = 'Continue your reliability investigation.';
      const label = document.querySelector('#workspace-welcome-label');
      if (label) label.textContent = `${projects.length} registered project${projects.length === 1 ? '' : 's'} in this workspace`;
    }
  } catch (_) {
    readiness?.replaceChildren(); row('Server', 'Readiness unavailable. Reload to retry.');
  }
})();

(() => {
  const region = document.querySelector('[data-external-poll]');
  if (!region) return;
  const controller = new AbortController();
  let timer;
  window.addEventListener('pagehide', () => { clearTimeout(timer); controller.abort(); }, {once: true});
  const poll = async () => {
    try {
      const response = await fetch(region.dataset.externalPoll, {signal: controller.signal});
      if (!response.ok) throw new Error('Saved status unavailable');
      const record = await response.json();
      const state = record.payload?.status;
      if (['completed', 'failed', 'interrupted', 'timed_out'].includes(state)) { location.reload(); return; }
      if (!['queued', 'running'].includes(state)) throw new Error('Status not recognized');
      timer = setTimeout(poll, 1500);
    } catch (_) {
      if (!controller.signal.aborted) {
        const message = document.querySelector('#external-poll-message');
        if (message) message.textContent = 'Saved status could not be refreshed. Reload this page to retry, or open the JSON status link.';
      }
    }
  };
  timer = setTimeout(poll, 1500);
})();
