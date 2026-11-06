# Frigate Timelapse HLS

An incremental, today-so-far timelapse viewer for Frigate recordings. The Python
worker reads the original recordings, accelerates them with FFmpeg, and publishes
HLS video throughout the day. A React player provides a poster, comfortable touch
controls, and a calendar of retained days.

## Local development in WSL

Requirements: Python 3.14+, uv, native Linux Node.js 22.12+ (Node 24 LTS recommended),
npm, Docker, and FFmpeg. NVIDIA decoding/encoding requires working NVDEC/NVENC
support in the installed FFmpeg and the Windows NVIDIA driver exposed to WSL.

Make sure `node` **and `npm`** resolve to Linux executables. A Windows npm on WSL's
PATH can launch Windows Node and fail when installing native frontend dependencies.

```bash
uv sync --locked
cd web
npm ci
npm run build
cd ..
mkdir -p output
```

Configure `config.toml` for your camera, Frigate API, timezone, and local recordings
mount. Keep local state/output paths writable by your Linux user. The recordings
mount only needs read access. Configuration values can also be supplied with the
existing `FTHLS_` environment variables.

Start the generator in a terminal or the PyCharm module run configuration:

```bash
uv run frigate-timelapse-hls run-loop
```

In another terminal, start the development media server:

```bash
docker compose -f docker-compose.dev.yml up -d
```

The built frontend is available at <http://localhost:6773>. For frontend hot reload:

```bash
cd web
npm run dev
```

Open <http://localhost:5173>. Vite proxies `/published/` to nginx at port 6773.
Set `MEDIA_ORIGIN` when the media server lives elsewhere. The Compose file uses
`create_host_path: false`, so missing bind sources fail clearly rather than being
created as root. Create `output/` yourself and build `web/dist/` first.

To index existing videos without starting video generation:

```bash
uv run frigate-timelapse-hls publish-library
```

Restart an already-running generator after updating its Python code. The web
library is reconciled every five seconds while `run-loop` is running; existing
archives are discovered without re-encoding. The browser refreshes metadata every
ten seconds while visible. A one-off `publish-library` does not keep polling.

## Player

- No autoplay. Posters and library metadata load immediately; HLS loads only after
  **Play**, **Resume**, or a desktop click on the player. Play starts at the beginning,
  including on growing days. Desktop clicks toggle playback; mobile taps reveal a
  centered Play/Pause button. Double-tap either side to seek on either device.
- **Archive** provides a calendar with month/year selection and dated links.
  Today's timeline grows as new segments become available. Reaching the end waits
  for more video until the stream is finalized. Partial past days remain playable.
- The custom timeline has a 48px interaction area. Tap or drag to choose a position;
  dragging previews the time and seeks only when released. Buffered ranges are shown.
- Double-tap the left/right side to skip. Desktop left/right arrows use the same
  interval. Settings store one browser-wide skip distance (default 5 seconds).
- Speed ranges from 0.5× to 4× in exact 0.1× increments. The slider gives 60% of
  its travel to 0.5×–1.5×; −/+ buttons provide precise adjustment. Speed always
  starts at 1× on a new visit or day and is never saved in browser storage.
  Tap the speed button for a small non-modal panel while watching. Dismiss it with
  the speed button, Escape, or a tap outside. The cog beside Archive opens settings.
- Hold the video to temporarily double playback speed, capped at 4×. Releasing or
  cancelling restores the selected speed. This does not change the rendering speed.
- On mobile, portrait controls remain visible outside the video area; the center
  button fades while playing and remains visible while paused. In paused landscape,
  tapping outside the controls hides or restores the entire interface.
- Pinch to zoom on mobile, then pan with one finger. Zoom keeps the video proportions
  and can fill the portrait video area without covering the permanent controls.
  Double-tap seeking still works when zoomed; hold-to-boost is disabled. **Reset zoom**
  appears in the bottom controls. Opening another day resets zoom.
- Play positions are saved per day and generation, with an explicit Resume option.
- Fullscreen requests landscape orientation when supported. Picture-in-picture and
  screen wake lock depend on browser capabilities; wake lock generally requires HTTPS.
  Native iPhone fullscreen may use the platform's own controls. Android Chrome is
  the primary mobile target.

## Posters

```toml
[thumbnail]
offset_seconds = 180
```

Default: `0`. Environment equivalent: `FTHLS_THUMBNAIL_OFFSET_SECONDS=180`.
Offsets must be nonnegative and fall inside the schedule.

The generator selects the frame at schedule start plus the offset, or the next
recorded frame within the schedule if there is a gap. It requests
`/api/{camera}/recordings/{timestamp}/snapshot.jpg?height=720` from Frigate and
saves the JPEG beside the video. The browser never needs direct Frigate access.

One background worker fetches posters with an eight-second timeout and at most
five attempts, at least 30 seconds apart. A missing poster does not stop generation.
A valid cached poster survives resumes and source-recording deletion. Changing the
active day's offset invalidates its poster until the replacement is available.
Older videos without sufficient schedule metadata use placeholders.

## Production

Images are published as `0x464e/frigate-timelapse-hls`. See `docker-compose.yml`
for the volume and GPU configuration. To build a local review image:

```bash
docker build -t frigate-timelapse-hls:local .
```

The Docker build compiles the frontend in a separate Node stage. The runtime runs
nginx and the Python generator, with no Node server. Port **6773** and all existing
`/published/` media URLs are preserved. Existing Traefik/Authentik routes can stay
as they are; serve UI, metadata, posters, and video through the same authenticated
origin. Hashed frontend assets are cached immutably; publication files revalidate.

The UI supports `/`, `/archive?month=YYYY-MM`, and `/days/YYYY-MM-DD`.
`/published/library.json` is the versioned, read-only library interface. Day entries
include readiness, published video duration, status, generation identity, media
URLs, and known schedule information. Metadata and snapshot playlists are published
atomically. Historical output is kept indefinitely; this app does not delete it.

## Verification

```bash
uv run pytest
uv run ruff check src tests
uv run mypy src
cd web
npm run typecheck
npm run lint
npm test
npm run build
npx playwright install chromium
npm run test:browser
```

Browser tests use short synthetic HLS media on desktop Chromium and an Android
viewport. Set `PLAYWRIGHT_CHROMIUM_EXECUTABLE` to reuse an existing compatible
Chromium. Actual phone rotation, PiP, and wake lock still require a physical-device
check. Real-recording smoke tests should use temporary output/state and no more
than 30–60 seconds of source footage; never regenerate a full production day just
to test the UI.

An optional integration test performs Frigate GET requests, reads at most 30 seconds
of today's recordings (schedule start + 3 minutes), and uses temporary output and
SQLite state. It uses your configured FFmpeg acceleration and bounds subprocess
execution. It never modifies NAS recordings or existing timelapses:

```bash
FTHLS_REAL_INTEGRATION=1 uv run pytest tests/test_real_integration.py
```
