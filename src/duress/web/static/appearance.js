(() => {
  const root = document.documentElement;
  let preference = 'dark';
  try { preference = localStorage.getItem('duress.appearance') || 'dark'; } catch (_) {}
  if (!['dark', 'light', 'system'].includes(preference)) preference = 'dark';
  const query = window.matchMedia('(prefers-color-scheme: dark)');
  const apply = () => {
    root.dataset.theme = preference === 'system' ? (query.matches ? 'dark' : 'light') : preference;
    root.dataset.appearance = preference;
  };
  window.duressAppearance = (value) => {
    preference = value;
    try { localStorage.setItem('duress.appearance', value); } catch (_) {}
    apply();
  };
  query.addEventListener('change', apply);
  apply();
})();
