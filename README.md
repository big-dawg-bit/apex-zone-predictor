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

Add a top-down map image per map as `static/maps/<slug>.png`
(`storm-point.png`, `worlds-edge.png`, ...). It must be square and cover the
same area the coordinates were recorded on, or clicks and data won't line up.

## Files

| File | Role |
| --- | --- |
| `app/db.py` | SQLite schema and queries |
| `app/predict.py` | nearest-neighbour prediction and leave-one-out evaluation |
| `app/main.py` | API endpoints |
| `static/index.html` | the clickable map |
| `import_data.py` | import games from JSON |
| `seed_fake.py` | fake data for testing; `--clear` removes it |

## API

- `POST /api/predict` with `{map, rings, target: "final"|"next", modes}`
- `POST /api/games` to save a game, `DELETE /api/games/{id}` to remove one
- `GET /api/evaluate/{map}?rounds_seen=2` to test accuracy against a baseline

## Before trusting it

1. Run `python seed_fake.py --clear` once real data is in.
2. Check `/api/evaluate/<map>`. The model has to beat the baseline hit rate by a
   clear margin; otherwise it isn't learning anything about that map.
3. When the heatmap has two hot areas, trust the heatmap, not the cross. The
   cross is a weighted average and can land between two likely spots.
