# Apex zone predictor

Click the ring centers you see in-game; the app shows where the final (or next)
ring ended in past games that started the same way.

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows  (Linux/macOS: source .venv/bin/activate)
pip install -r requirements.txt
python seed_fake.py             # optional: fake data to test with
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Open http://localhost:8000. On your phone (same Wi-Fi), open
http://<your-pc-ip>:8000 so the map doesn't cover the game. You may have to
allow port 8000 through Windows Firewall.

## Map images

Open http://localhost:8000/setup to add the top-down map image the predictor
draws under your clicks:

1. Open the map in-game and zoom out fully.
2. `Win + Shift + S`, drag a box around the map.
3. Paste on the setup page with `Ctrl + V` (or drop the file / pick it).
4. Drag a square around the map, fine-tune with the slider and arrow keys, pick
   the map in the dropdown and save.

The crop is remembered per screenshot resolution, so every map ends up cropped
the same way: the in-game map sits in the same place on screen for all of them.
That matters, because every coordinate in the database is a fraction of the
image it was clicked on. A different crop for the same map shifts every ring
already logged there, so re-crop only if you mean to. The image you replace is
kept as `static/maps/old/<slug>-<timestamp>.png` (nothing prunes that folder).

## Files

| File | Role |
| --- | --- |
| `app/db.py` | SQLite schema and queries |
| `app/predict.py` | nearest-neighbour prediction and leave-one-out evaluation |
| `app/main.py` | API endpoints |
| `static/index.html` | the clickable map |
| `static/setup.html` | paste a screenshot, crop it square, save it as a map image |
| `static/maps.js` | the map slug/name list, shared by both pages |
| `import_data.py` | import games from JSON |
| `seed_fake.py` | fake data for testing; `--clear` removes it |

## API

- `POST /api/predict` with `{map, rings, target: "final"|"next", modes}`
- `POST /api/games` to save a game, `DELETE /api/games/{id}` to remove one
- `GET /api/evaluate/{map}?rounds_seen=2` to test accuracy against a baseline
- `PUT /api/maps/{map}/image` with a square PNG as the raw body (what /setup uses)

## Before trusting it

1. Run `python seed_fake.py --clear` once real data is in.
2. Check `/api/evaluate/<map>`. The model has to beat the baseline hit rate by a
   clear margin; otherwise it isn't learning anything about that map.
3. When the heatmap has two hot areas, trust the heatmap, not the cross. The
   cross is a weighted average and can land between two likely spots.
