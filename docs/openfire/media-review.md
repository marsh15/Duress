# Release media verification

README screenshots were refreshed on 27 October from the running accepted `3284471` application. Each route, viewport, theme and SHA256 is recorded in [capture provenance](screenshots/provenance.json). Historical design captures remain unchanged. Current images use real retained records, not a fresh-execution claim.

Architecture source is an editable Excalidraw v2 scene, exported using `@excalidraw/excalidraw` 0.18.1. The PNG and SVG were generated from that scene and checked against `docs/ARCHITECTURE.md`; the exported SVG contains no remote assets. Excalidraw’s sketch outlines use system sans-serif text for readability. No diagram tooling is added to the runtime.

The final MP4 is 1920×1080 H.264, 15fps, exactly 90 seconds, silent with captions. Full-frame FFmpeg decoding passed, and complete real-time Chromium playback ended at 90 seconds with no media error. The corrected state scene shows all three fulfilment rows. [Playback observations](demo-playback-review.json) and [capture provenance](demo-capture-provenance.json) preserve scope. The poster uses the opening frame; evidence IDs and trial counts match retained records. No fresh Alan invocation is claimed.

The README uses bare GitHub attachment URLs for native video rendering. A separate browser player supplies controls, keyboard playback, fullscreen and direct MP4 fallbacks. Ordinary HTML video tags are sanitized by GitHub. The static video page is not the hosted Duress application.

A separate 22-second 1920×1080/30fps H.264/AAC brag clip is [included](duress-brag-22s.mp4) above the full demo. It reuses real source captures with fixed Geist captions, slight UI motion, brief dips between scenes and original quiet A-minor pad/pulse music. Full playback reached 22 seconds/660 frames with no error; full-frame decoding passed. Mean audio level −33.9 dBFS, peak −26.3 dBFS. [Review](brag-playback-review.json). The first frame is the poster. Sources/render intermediates remain in the local brag-output/work directory.

The README uses the owner's two canonical GitHub attachment URLs as bare lines for GitHub-native video rendering, with repository MP4s and the independent browser player retained as fallbacks. After publication, an anonymous GitHub README browser showed both native players in the correct order (22s then 90s), loaded their metadata without errors, and played both successfully with controls. The streams match the reviewed files. Canonical attachment URLs can return 404 directly; the README renderer supplies signed native media streams.
