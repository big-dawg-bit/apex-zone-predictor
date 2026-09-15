"""Zone prediction by weighted nearest neighbours.

Idea: rings you've already seen are evidence. Past games whose rings started
in the same places are the best guide to where this game's rings go next.

Steps, and why each one is there:
1. Compare only rounds you've actually observed. A game can't be judged on
   rounds you haven't seen yet.
2. Divide each center distance by that round's radius. Being 5% of the map off
   is nothing for a huge round-1 ring but a completely different spot for a
   round-4 ring. Normalising makes later (more informative) rounds count more
   automatically.
3. Drop past games whose target ring lies outside your current ring. Every new
   ring must sit inside the previous one, so those outcomes are impossible now.
4. Turn distance into a weight with a Gaussian (exp(-d^2 / 2 sigma^2)). Close
   matches dominate, far ones fade out smoothly instead of a hard cutoff.
5. Report the effective sample size. Twenty matches where one has 95% of the
   weight is really a sample of one; this number tells you how much to trust
   the heatmap.
"""
import math

INSIDE_TOLERANCE = 1.08  # allow for imprecise clicks on the map


def _distance(observed, rings):
    terms = []
    for o in observed:
        gx, gy, _ = rings[o["round"]]
        scale = max(o["radius"], 1e-3)
        terms.append(((gx - o["x"]) ** 2 + (gy - o["y"]) ** 2) / scale ** 2)
    return math.sqrt(sum(terms) / len(terms))


def predict(games, observed, target="final", sigma=0.6, top_n=25):
    """games: {game_id: {round: (x, y, r)}}
    observed: list of {"round", "x", "y", "radius"}, rounds 1..k
    target: "final" or "next"
    """
    if not observed:
        return {"points": [], "matches": 0, "effective_n": 0.0,
                "estimate": None, "spread": None, "target_round": None}

    observed = sorted(observed, key=lambda o: o["round"])
    k = observed[-1]["round"]
    last = observed[-1]
    candidates = []

    for game_id, rings in games.items():
        if any(o["round"] not in rings for o in observed):
            continue
        final_round = max(rings)
        target_round = final_round if target == "final" else k + 1
        if target_round <= k or target_round not in rings:
            continue

        tx, ty, tr = rings[target_round]
        if math.hypot(tx - last["x"], ty - last["y"]) > last["radius"] * INSIDE_TOLERANCE:
            continue

        d = _distance(observed, rings)
        w = math.exp(-(d * d) / (2 * sigma * sigma))
        if w < 1e-6:
            continue
        candidates.append({"game_id": game_id, "round": target_round,
                           "x": tx, "y": ty, "radius": tr,
                           "distance": d, "weight": w})

    if not candidates:
        return {"points": [], "matches": 0, "effective_n": 0.0,
                "estimate": None, "spread": None, "target_round": None}

    candidates.sort(key=lambda c: c["weight"], reverse=True)
    total = sum(c["weight"] for c in candidates)
    for c in candidates:
        c["weight"] /= total

    ex = sum(c["x"] * c["weight"] for c in candidates)
    ey = sum(c["y"] * c["weight"] for c in candidates)
    spread = math.sqrt(sum(c["weight"] * ((c["x"] - ex) ** 2 + (c["y"] - ey) ** 2)
                           for c in candidates))
    effective_n = 1.0 / sum(c["weight"] ** 2 for c in candidates)

    return {
        "points": candidates[:top_n] if top_n else candidates,
        "all_points_count": len(candidates),
        "matches": len(candidates),
        "effective_n": round(effective_n, 1),
        "estimate": {"x": ex, "y": ey},
        "spread": spread,
        "target_round": "final" if target == "final" else k + 1,
    }


def evaluate(games, rounds_seen=2, sigma=0.6):
    """Leave-one-out test: hide each game, predict its final ring from the
    first `rounds_seen` rings, and measure the error.

    The baseline guesses "final center = center of the last seen ring". If the
    model can't beat that, it's only learning geometry, not real patterns.
    """
    model_err, base_err, hits, base_hits, n = 0.0, 0.0, 0, 0, 0
    for gid, rings in games.items():
        final_round = max(rings)
        if final_round <= rounds_seen or any(r not in rings for r in range(1, rounds_seen + 1)):
            continue
        observed = [{"round": r, "x": rings[r][0], "y": rings[r][1], "radius": rings[r][2]}
                    for r in range(1, rounds_seen + 1)]
        others = {g: v for g, v in games.items() if g != gid}
        result = predict(others, observed, target="final", sigma=sigma, top_n=0)
        if not result["estimate"]:
            continue
        fx, fy, fr = rings[final_round]
        e = math.hypot(result["estimate"]["x"] - fx, result["estimate"]["y"] - fy)
        b = math.hypot(observed[-1]["x"] - fx, observed[-1]["y"] - fy)
        model_err += e
        base_err += b
        # Same win rule as predict-the-ring: within 1.8x the final radius.
        hits += e <= 1.8 * fr
        base_hits += b <= 1.8 * fr
        n += 1
    if n == 0:
        return None
    return {"games_tested": n,
            "model_mean_error": model_err / n, "baseline_mean_error": base_err / n,
            "model_hit_rate": hits / n, "baseline_hit_rate": base_hits / n}
