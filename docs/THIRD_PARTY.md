# Vendored browser assets

- **HTMX 2.0.10** — `src/duress/web/static/vendor/htmx.min.js`; runtime browser enhancement. Its Zero-Clause BSD license is retained as `HTMX-LICENSE.txt` beside the asset. Upstream: https://github.com/bigskysoftware/htmx.
- **axe-core 4.10.3** — `tests/browser/vendor/axe.min.js`; test-only accessibility inspection. Its Mozilla Public License 2.0 is retained as `AXE-LICENSE.txt` beside the asset. Upstream: https://github.com/dequelabs/axe-core.

These assets are served or loaded locally; application browsing and accessibility tests do not fetch them from a CDN. Python dependency versions are pinned in `uv.lock`.
