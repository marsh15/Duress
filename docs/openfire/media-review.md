# Release media verification

README screenshots were refreshed on 27 October from the running accepted `3284471` application. Each route, viewport, theme and SHA256 is recorded in [capture provenance](screenshots/provenance.json). Historical design captures remain unchanged. Current images use real retained records, not a fresh-execution claim.

Architecture source is an editable Excalidraw v2 scene, exported using `@excalidraw/excalidraw` 0.18.1. The PNG and SVG were generated from that scene and checked against `docs/ARCHITECTURE.md`; the exported SVG contains no remote assets. Excalidraw’s sketch outlines use system sans-serif text for readability. No diagram tooling is added to the runtime.

The final MP4 is 1920×1080 H.264, 15fps, exactly 90 seconds, silent with captions. Full-frame FFmpeg decoding passed, and complete real-time Chromium playback ended at 90 seconds with no media error. The corrected state scene shows all three fulfilment rows. [Playback observations](demo-playback-review.json) and [capture provenance](demo-capture-provenance.json) preserve scope. The poster uses the opening frame; evidence IDs and trial counts match retained records. No fresh Alan invocation is claimed.

The README poster opens a dedicated browser video player. GitHub sanitizes embedded HTML video in README rendering; the linked player supplies controls, keyboard playback, fullscreen and a direct MP4 fallback. This static video page is not a hosted Duress application.
