"""Import games from a JSON file into the database.

Usage:  python import_data.py path/to/games.json

Expected format (a list of games):
[
  {
    "external_id": "algs-y6s2-na-g14",     # optional, prevents duplicates
    "map": "storm-point",
    "season": "S30",
    "mode": "algs",
    "rings": [
      {"round": 1, "x": 0.52, "y": 0.47, "radius": 0.33},
      {"round": 2, "x": 0.58, "y": 0.41, "radius": 0.21}
    ]
  }
]

x, y and radius are fractions of the map width (0-1, y goes down).
Whatever format Hugo's data comes in, the plan is to write a small converter
to this shape. If his data is in in-game units, the converter needs the map's
world bounds: x_frac = (world_x - min_x) / (max_x - min_x).
"""
import json
import sys

from app import db
from app.main import GameIn


def main(path):
    db.init_db()
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)

    added = skipped = invalid = 0
    with db.get_conn() as conn:
        for i, item in enumerate(raw):
            try:
                # Reuse the API's validation so imported data follows the
                # same rules as data entered in the app.
                game = GameIn(source=item.get("source", "import"), **{
                    k: v for k, v in item.items() if k != "source"})
            except Exception as e:
                invalid += 1
                print(f"game {i} skipped: " + " | ".join(str(e).splitlines()[1:]))
                continue
            game_id = db.insert_game(
                conn, game.map, [r.model_dump() for r in game.rings],
                season=game.season, mode=game.mode,
                source=game.source, external_id=game.external_id)
            if game_id is None:
                skipped += 1
            else:
                added += 1

    print(f"added {added}, already present {skipped}, invalid {invalid}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python import_data.py games.json")
    main(sys.argv[1])
