"""Turn an ALGS game dump into the JSON that import_data.py accepts.

Why this is a separate step from import_data.py: importing is easy, but the
numbers have to be moved from someone else's coordinate system into yours first,
and that transform is the part that can silently ruin the database. Every
coordinate the app stores is a fraction of the square you cropped on /setup, so
a dump converted with the wrong world bounds imports cleanly, validates fine,
and points the heatmap at the wrong side of the map. /api/evaluate will still
show the model beating the baseline, because ring geometry alone is enough to do
that. So this script refuses to convert a map it has not been calibrated for.

Three steps, in order:

  1. python convert_algs.py --inspect dump.json
     Print the structure of a real API response and guess which fields hold the
     ring data, then fix "fields" in algs_mapping.json. The ring schema is not
     documented by apexlegendsstatus.com, so the paths shipped in that file are
     guesses and almost certainly need correcting.

  2. python convert_algs.py --calibrate storm-point --ref WX,WY,FX,FY --ref WX,WY,FX,FY
     Work out the world bounds of your crop from two landmarks you can find in
     both the source data and your own map image, and write them into
     algs_mapping.json.

  3. python convert_algs.py dump.json -o games.json
     Convert, validating every game the way the API does, then:
     python import_data.py games.json

Delete this script and algs_mapping.json to remove the whole thing; nothing
else in the project imports them.
"""
import argparse
import json
import math
import sys
from pathlib import Path

from app.main import GameIn

CONFIG_PATH = Path(__file__).resolve().parent / "algs_mapping.json"
RING_HINT = ("ring", "circle", "zone", "radius", "center", "centre", "pos", "stage", "round")


# ---------------------------------------------------------------- reading JSON

def dig(obj, path):
    """Follow a dotted path like "center.x" into nested dicts. Returns None if
    any step is missing, so a mapping that is wrong shows up as missing data
    rather than a traceback halfway through a file."""
    if path is None:
        return None
    cur = obj
    for part in str(path).split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.lstrip("-").isdigit():
            try:
                cur = cur[int(part)]
            except IndexError:
                return None
        else:
            return None
    return cur


def load_config():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


# ------------------------------------------------------------------- --inspect

def describe(value, depth=0, max_depth=6):
    """One line per key, so an unfamiliar response can be read at a glance."""
    pad = "  " * depth
    if isinstance(value, dict):
        if depth >= max_depth:
            print(f"{pad}{{...{len(value)} keys...}}")
            return
        for k, v in list(value.items())[:40]:
            if isinstance(v, (dict, list)):
                kind = "object" if isinstance(v, dict) else f"array[{len(v)}]"
                star = "  <-- ring data?" if any(h in k.lower() for h in RING_HINT) else ""
                print(f"{pad}{k}: {kind}{star}")
                describe(v, depth + 1, max_depth)
            else:
                star = "  <-- ring data?" if any(h in k.lower() for h in RING_HINT) else ""
                print(f"{pad}{k}: {json.dumps(v)[:70]}{star}")
    elif isinstance(value, list):
        if not value:
            print(f"{pad}(empty)")
            return
        print(f"{pad}[0] of {len(value)}:")
        describe(value[0], depth + 1, max_depth)
        if len(value) > 1:
            print(f"{pad}... {len(value) - 1} more of the same shape")


def scan_for_numbers(obj, trail="", found=None):
    """Collect every numeric leaf with a ring-ish name. Whatever the response
    calls its fields, the coordinates are numbers, so this narrows the search."""
    found = [] if found is None else found
    if isinstance(obj, dict):
        for k, v in obj.items():
            scan_for_numbers(v, f"{trail}.{k}" if trail else k, found)
    elif isinstance(obj, list) and obj:
        scan_for_numbers(obj[0], f"{trail}[]", found)
    elif isinstance(obj, (int, float)) and not isinstance(obj, bool):
        if any(h in trail.lower() for h in RING_HINT):
            found.append((trail, obj))
    return found


def inspect(path):
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    print(f"=== structure of {path} ===")
    describe(raw)
    hits = scan_for_numbers(raw)
    print("\n=== numeric fields with ring-ish names ===")
    if not hits:
        print("none found - look through the structure above by hand")
    for trail, val in hits[:40]:
        print(f"  {trail} = {val}")
    print("\nPut the real paths into 'fields' in algs_mapping.json.")
    print("Coordinates are probably in world units; --calibrate turns them into")
    print("fractions of your crop.")
    return 0


# ----------------------------------------------------------------- --calibrate

def calibrate(map_slug, refs):
    """Solve the world bounds of a square crop from two landmarks.

    Each --ref is WORLD_X,WORLD_Y,FRAC_X,FRAC_Y: where a landmark sits in the
    source's coordinates, and where it sits on your cropped image (0-1, read off
    the /setup readout or measured in any image editor). Two landmarks fix the
    scale and the offset; pick them far apart, because a short baseline
    multiplies any measuring error.
    """
    if len(refs) < 2:
        sys.exit("--calibrate needs two --ref points")
    (wx1, wy1, fx1, fy1), (wx2, wy2, fx2, fy2) = refs[0], refs[1]

    dfx, dfy = fx2 - fx1, fy2 - fy1
    if abs(dfx) < 1e-6 and abs(dfy) < 1e-6:
        sys.exit("the two reference points are on top of each other")

    cfg = load_config()
    y_up = cfg["defaults"]["y_axis_up"]
    # span = world units across the full width of the crop. Derived from each
    # axis separately; a real disagreement means a bad measurement or a crop
    # that isn't square.
    spans = []
    if abs(dfx) > 1e-6:
        spans.append(abs((wx2 - wx1) / dfx))
    if abs(dfy) > 1e-6:
        spans.append(abs((wy2 - wy1) / dfy))
    span = sum(spans) / len(spans)
    if len(spans) == 2:
        disagree = abs(spans[0] - spans[1]) / span
        print(f"x-axis span {spans[0]:.1f}, y-axis span {spans[1]:.1f} "
              f"({disagree * 100:.1f}% apart)")
        if disagree > 0.02:
            print("  warning: those should match on a square crop. Check the "
                  "reference points before trusting this.")

    # Invert the forward transform used in to_fraction() for point 1.
    center_x = wx1 + (0.5 - fx1) * span
    center_y = wy1 - (0.5 - fy1) * span if y_up else wy1 + (0.5 - fy1) * span

    cfg["maps"].setdefault(map_slug, {})
    cfg["maps"][map_slug] = {"calibrated": True, "center_x": round(center_x, 2),
                             "center_y": round(center_y, 2), "span": round(span, 2)}
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
        f.write("\n")
    print(f"\n{map_slug}: center ({center_x:.1f}, {center_y:.1f}), span {span:.1f} "
          f"world units -> written to {CONFIG_PATH.name}")
    print("Check it: convert a few games and see whether the final rings land "
          "where the ALGS VOD shows them.")
    return 0


# ------------------------------------------------------------------- transform

def to_fraction(wx, wy, bounds, y_up):
    """World coordinates -> fraction of the cropped image (0-1, y downward)."""
    span = bounds["span"]
    fx = (wx - bounds["center_x"]) / span + 0.5
    # Image y grows downward. If the source's y grows north, it has to flip, or
    # every ring lands mirrored across the middle of the map.
    fy = (bounds["center_y"] - wy) / span + 0.5 if y_up else (wy - bounds["center_y"]) / span + 0.5
    return fx, fy


def convert_game(raw, cfg, stats):
    fields = cfg["fields"]
    gf = fields["game"]
    defaults = cfg["defaults"]

    raw_map = dig(raw, gf["map"])
    if raw_map is None:
        stats["no_map"] += 1
        return None
    slug = fields["map_aliases"].get(str(raw_map), str(raw_map))
    bounds = cfg["maps"].get(slug)
    if bounds is None:
        stats["unknown_map"].add(str(raw_map))
        return None
    if not bounds.get("calibrated") or not bounds.get("span"):
        # The whole point of this guard: without bounds the numbers would still
        # convert, just to the wrong places.
        stats["uncalibrated"].add(slug)
        return None

    raw_rings = dig(raw, gf["rings_path"])
    if not isinstance(raw_rings, list) or not raw_rings:
        stats["no_rings"] += 1
        return None

    rf = gf["ring"]
    y_up = defaults["y_axis_up"]
    fallback = defaults["fallback_radii"]
    span = bounds["span"]

    picked = []
    for r in raw_rings:
        wx, wy = dig(r, rf["x"]), dig(r, rf["y"])
        if not isinstance(wx, (int, float)) or not isinstance(wy, (int, float)):
            continue
        order = dig(r, rf["round"])
        picked.append((order if isinstance(order, (int, float)) else len(picked),
                       wx, wy, dig(r, rf["radius"])))
    if len(picked) < 2:
        stats["too_few_rings"] += 1
        return None

    picked.sort(key=lambda t: t[0])
    source_rounds = [p[0] for p in picked]
    # Renumber to 1..n because the API's rounds may be 0-based, and the app
    # requires 1..n with no gaps. Report gaps rather than hiding them: a missing
    # middle ring renumbered into a chain is a fabricated chain.
    if source_rounds != list(range(source_rounds[0], source_rounds[0] + len(source_rounds))):
        stats["gappy_rounds"] += 1

    rings = []
    for i, (_, wx, wy, wr) in enumerate(picked):
        fx, fy = to_fraction(wx, wy, bounds, y_up)
        if isinstance(wr, (int, float)) and wr > 0:
            radius = wr / span
            # Kept for the scale check below: real ring sizes as a share of the
            # map are roughly known, so they can expose a wrong span.
            stats["source_radii"].setdefault(i, []).append(radius)
        else:
            # No radius in the source. predict.py divides distances by the
            # radius, so a wrong one changes how much each round counts: these
            # are the averages seed_fake.py uses, not measurements.
            radius = fallback[min(i, len(fallback) - 1)]
            stats["radius_guessed"] += 1
        rings.append({"round": i + 1, "x": round(fx, 5), "y": round(fy, 5),
                      "radius": round(min(max(radius, 1e-4), 1.0), 5)})

    off = [r for r in rings if not (0 <= r["x"] <= 1 and 0 <= r["y"] <= 1)]
    if off:
        # Nearly always a calibration error rather than odd data.
        stats["outside_image"] += 1
        return None

    game = {"map": slug, "rings": rings,
            "mode": defaults["mode"], "source": defaults["source"]}
    ext = dig(raw, gf["external_id"])
    if ext is not None:
        game["external_id"] = f"algs-{ext}"   # re-running the import won't duplicate
    season = dig(raw, gf["season"])
    if season is not None:
        game["season"] = str(season)

    try:
        # The API's own rules: rounds 1..n with no gaps, each center inside the
        # previous ring. A game that fails this is misaligned, not just odd.
        GameIn(**game)
    except Exception as exc:
        stats["failed_validation"] += 1
        if stats["failed_validation"] <= 3:
            msg = " | ".join(str(exc).splitlines()[1:]) or str(exc)
            print(f"  rejected: {msg}")
        return None
    return game


def median(values):
    s = sorted(values)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def check_scale(stats, cfg):
    """Catch a wrong span by checking ring sizes against known ring sizes.

    Needed because scaling every ring about the middle of the map keeps them
    inside the image and keeps each one inside the last, so a span that is out
    by a factor still passes every validation the app has. Ring radii as a share
    of the map are roughly fixed by the game, so they give the scale away.
    """
    fallback = cfg["defaults"]["fallback_radii"]
    ratios = [median(vals) / fallback[i]
              for i, vals in sorted(stats["source_radii"].items())
              if i < len(fallback) and vals]
    if not ratios:
        return False
    factor = median(ratios)
    print(f"\nscale check: converted rings are {factor:.2f}x the size rings "
          f"normally are on this map")
    if 0.75 <= factor <= 1.35:
        print("  looks right")
        return False
    print(f"  WARNING: that is well off. The span is probably wrong by about "
          f"this factor.")
    print(f"  Try span x {factor:.3f} (re-run --calibrate, or edit "
          f"algs_mapping.json), then convert again.")
    print("  Importing as-is would give you data that validates but sits at the "
          "wrong scale.")
    return True


def spot_check(games, n=3):
    """Print a few final rings. A mirrored y axis survives every check here -
    the whole chain mirrors together - so one human look at a VOD is the only
    thing that catches it."""
    if not games:
        return
    print(f"\nspot check - compare these against the VOD or the ALGS map atlas:")
    for g in games[:n]:
        last = g["rings"][-1]
        ident = g.get("external_id", g["map"])
        print(f"  {ident}: final ring at x={last['x']:.3f} y={last['y']:.3f} "
              f"({'left' if last['x'] < 0.5 else 'right'}, "
              f"{'top' if last['y'] < 0.5 else 'bottom'} of your crop)")
    print("  If these are consistently mirrored top-for-bottom, flip "
          "'y_axis_up' in algs_mapping.json.")


def convert(path, out_path):
    cfg = load_config()
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)

    games_raw = dig(raw, cfg["fields"]["games_path"])
    if games_raw is None and isinstance(raw, list):
        games_raw = raw  # a bare array of games
    if not isinstance(games_raw, list):
        sys.exit(f"no list of games at '{cfg['fields']['games_path']}'. "
                 f"Run --inspect {path} and fix 'games_path'.")

    stats = {"no_map": 0, "unknown_map": set(), "uncalibrated": set(), "no_rings": 0,
             "too_few_rings": 0, "gappy_rounds": 0, "radius_guessed": 0,
             "outside_image": 0, "failed_validation": 0, "source_radii": {}}
    out = [g for g in (convert_game(g, cfg, stats) for g in games_raw) if g]

    print(f"\nread {len(games_raw)} games, converted {len(out)}")
    for label, key in [("no map field", "no_map"), ("no rings", "no_rings"),
                       ("fewer than 2 usable rings", "too_few_rings"),
                       ("rings outside the image", "outside_image"),
                       ("failed the app's validation", "failed_validation")]:
        if stats[key]:
            print(f"  skipped {stats[key]}: {label}")
    if stats["unknown_map"]:
        print(f"  unknown map names: {sorted(stats['unknown_map'])}")
        print("    add them to 'map_aliases' in algs_mapping.json")
    if stats["uncalibrated"]:
        print(f"  NOT CALIBRATED, so skipped: {sorted(stats['uncalibrated'])}")
        print("    run --calibrate for each before converting")
    if stats["gappy_rounds"]:
        print(f"  note: {stats['gappy_rounds']} games had non-consecutive source "
              f"rounds and were renumbered")
    if stats["radius_guessed"]:
        print(f"  note: {stats['radius_guessed']} rings had no radius in the "
              f"source and used the fallback table")

    scale_warning = check_scale(stats, cfg)
    spot_check(out)

    if not out:
        print("\nnothing to write")
        return 1
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
        f.write("\n")
    print(f"\nwrote {out_path}")
    print(f"next: python import_data.py {out_path}")
    print("then check /api/evaluate/<map> - if the model doesn't clearly beat "
          "the baseline, suspect the calibration before the model.")
    return 0


def ref_arg(text):
    parts = text.split(",")
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("--ref wants WORLD_X,WORLD_Y,FRAC_X,FRAC_Y")
    try:
        return tuple(float(p) for p in parts)
    except ValueError:
        raise argparse.ArgumentTypeError("--ref values must all be numbers")


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("dump", nargs="?", help="JSON file from the ALGS API")
    p.add_argument("-o", "--out", default="games.json", help="where to write (default games.json)")
    p.add_argument("--inspect", metavar="FILE", help="print a response's structure and exit")
    p.add_argument("--calibrate", metavar="MAP_SLUG", help="solve world bounds for one map")
    p.add_argument("--ref", action="append", type=ref_arg, default=[],
                   metavar="WX,WY,FX,FY", help="landmark for --calibrate; give two")
    args = p.parse_args()

    if args.inspect:
        return inspect(args.inspect)
    if args.calibrate:
        return calibrate(args.calibrate, args.ref)
    if not args.dump:
        p.print_help()
        return 1
    return convert(args.dump, args.out)


if __name__ == "__main__":
    sys.exit(main())
