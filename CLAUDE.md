# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

All commands run from the project root (`app` must be importable as a package).

```bash
.venv\Scripts\activate                              # Windows; Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000     # serves API + UI at http://localhost:8000
python seed_fake.py                                 # 300 synthetic games per map (source='synthetic')
python seed_fake.py --clear                         # delete synthetic games only
python import_data.py path/to/games.json            # bulk import (see import_data.py docstring for the format)
```

`--host 0.0.0.0` is deliberate: the intended setup is the map open on a phone at
`http://<pc-ip>:8000` while the game runs fullscreen on the PC.

There is no test suite, linter, or formatter configured. The stand-in for tests is the
leave-one-out evaluator: `GET /api/evaluate/<map>?rounds_seen=2`. After changing anything in
`app/predict.py`, hit that endpoint and check `model_hit_rate` still beats `baseline_hit_rate`
by a clear margin — the baseline is "final ring = center of the last ring seen", so a model that
merely ties it has learned geometry, not map patterns.

## Architecture

A single-map-image-per-map coordinate system holds the whole design together: **every x, y and
radius everywhere in the stack is a fraction of the map image (0–1, y down)**, never pixels or
in-game units. That is why the DB has `CHECK (x BETWEEN 0 AND 1)`, why the canvas is a fixed
1000×1000 logical square scaled by CSS, and why a map image dropped into `static/maps/<slug>.png`
must be square and cover exactly the area the stored coordinates were recorded on. A converter
for external data must normalize into this space (`x_frac = (world_x - min_x) / (max_x - min_x)`).

Data flow: canvas clicks → `state.rings` (fractions) → `POST /api/predict` → `db.load_games`
→ `predict()` → weighted candidate points → heatmap + crosshair redraw. Nothing is persisted
until "Log a game" mode posts to `/api/games`.

- `app/db.py` — SQLite (`data/zones.db`, created on demand). Two tables: `games` (map, mode,
  `external_id UNIQUE` for idempotent imports) and `rings` (one row per round, PK `(game_id, round)`).
  `get_conn()` is a commit/rollback context manager that enables `PRAGMA foreign_keys` on every
  connection — required, or deleting a game orphans its rings. `load_games()` returns the shape
  the predictor wants: `{game_id: {round: (x, y, radius)}}`.
- `app/predict.py` — pure functions, no DB access, so they are testable and reusable by `evaluate()`.
  `predict()` scores each past game by RMS center distance **divided by that round's radius**, so
  later (tighter, more informative) rounds automatically dominate the match; converts distance to a
  Gaussian weight (`sigma`, default 0.6); and discards past games whose target ring falls outside the
  last observed ring, since the real game can't produce those. `effective_n` (inverse Herfindahl of
  the weights) is the trust signal — 20 matches where one holds most of the weight is a sample of one.
- `app/main.py` — Pydantic models double as the validation layer for both the API and `import_data.py`
  (which imports `GameIn` on purpose, so imported and hand-entered data obey identical rules).
  `_check_ring_chain` enforces gapless rounds starting at 1 and each center inside the previous ring.
- `static/index.html` — the predictor UI: no build step, no framework, vanilla canvas.
  Ring radii are *not* clicked; they come from `GET /api/maps/{slug}/radii` (averaged from real data
  per round) with a hard-coded `FALLBACK_RADII` used only for maps with no data yet. Predict requests
  carry a monotonic `state.requestId` so a slow response from an older click can't overwrite a newer one.

- `static/setup.html` — crops a pasted screenshot to a square and `PUT`s it to
  `/api/maps/{slug}/image`, which validates the PNG by parsing IHDR by hand (no image library) and
  moves the previous image to `static/maps/old/` rather than overwriting it. The crop is cached in
  `localStorage` per screenshot resolution so every map is cropped identically — which is the whole
  point, since a different crop for a map invalidates the fractional coordinates already stored for it.
- `static/maps.js` — `window.APEX_MAPS`, the slug/display-name list both pages load.

`INSIDE_TOLERANCE`/`* 1.08` appears in three places (`app/predict.py`, `_check_ring_chain`, and the
canvas click handler) and they must stay in agreement, or the UI will accept a click the API rejects.
The map slug list is duplicated three ways: `static/maps.js` (`APEX_MAPS`, 6, the UI roster),
`app/main.py` (`MAP_SLUGS`, 6, the image-upload allowlist) and `seed_fake.py` (`MAPS`, 5). Keep the
first two in step. `MAP_SLUGS` deliberately does *not* constrain `GameIn.map`, which stays a free
string because `import_data.py` shares that model.

`static/maps/**/*.png` is gitignored: map images are EA screenshots and stay local, as does
`data/zones.db`.
