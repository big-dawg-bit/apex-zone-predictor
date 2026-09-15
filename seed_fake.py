"""Fill the database with FAKE games so the app can be tested before real data
arrives. These games are marked source='synthetic' and mode='other'.

    python seed_fake.py            # add 300 fake games per map
    python seed_fake.py --clear    # remove all synthetic games

Always run --clear before trusting predictions on real data.

How the fake rings are generated (so the predictor has something to find):
each map gets a few invented "hotspots". Ring 1 lands somewhere in the middle,
the hotspot nearest to ring 1 is most likely to attract the endgame, and every
next ring drifts toward it with noise, while always staying inside the
previous ring, just like the real game.
"""
import math
import random
import sys

from app import db

MAPS = ["storm-point", "worlds-edge", "e-district", "olympus", "broken-moon"]
RADII = [0.34, 0.22, 0.14, 0.085, 0.05, 0.028, 0.015]  # per round, fraction of map


def fake_game(rng, hotspots):
    x, y = rng.uniform(0.35, 0.65), rng.uniform(0.35, 0.65)
    rings = [{"round": 1, "x": x, "y": y, "radius": RADII[0]}]

    dists = [math.hypot(hx - x, hy - y) for hx, hy in hotspots]
    weights = [1 / (d + 0.05) ** 2 for d in dists]
    hx, hy = rng.choices(hotspots, weights=weights)[0]

    for i in range(1, len(RADII)):
        prev = rings[-1]
        room = prev["radius"] - RADII[i]  # new ring must fit inside the old one
        tx = prev["x"] + (hx - prev["x"]) * 0.6 + rng.gauss(0, room * 0.4)
        ty = prev["y"] + (hy - prev["y"]) * 0.6 + rng.gauss(0, room * 0.4)
        dx, dy = tx - prev["x"], ty - prev["y"]
        d = math.hypot(dx, dy)
        if d > room:
            tx, ty = prev["x"] + dx / d * room, prev["y"] + dy / d * room
        tx, ty = min(max(tx, 0.02), 0.98), min(max(ty, 0.02), 0.98)
        rings.append({"round": i + 1, "x": tx, "y": ty, "radius": RADII[i]})
    return rings


def main():
    db.init_db()
    if "--clear" in sys.argv:
        with db.get_conn() as conn:
            n = conn.execute("DELETE FROM games WHERE source = 'synthetic'").rowcount
        print(f"removed {n} synthetic games")
        return

    rng = random.Random(42)
    with db.get_conn() as conn:
        for m in MAPS:
            hotspots = [(rng.uniform(0.2, 0.8), rng.uniform(0.2, 0.8)) for _ in range(3)]
            for _ in range(300):
                db.insert_game(conn, m, fake_game(rng, hotspots),
                               season="fake", mode="other", source="synthetic")
    print(f"added {300 * len(MAPS)} synthetic games")


if __name__ == "__main__":
    main()
