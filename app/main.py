"""FastAPI backend.

Run from the project root:  uvicorn app.main:app --host 0.0.0.0 --port 8000
--host 0.0.0.0 lets your phone on the same Wi-Fi open http://<pc-ip>:8000,
so the map sits on your phone instead of covering the game.
"""
import math
import os
import struct
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import List, Literal, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from . import db
from .predict import evaluate, predict

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
MAPS_DIR = STATIC_DIR / "maps"
Mode = Literal["algs", "ranked", "pubs", "other"]

# Maps you can upload an image for. This is an allowlist, not a description of
# the data: games.map stays a free string (see GameIn), because import_data.py
# shares that model and shouldn't reject a map this list hasn't heard of yet.
# Keep in sync with APEX_MAPS in static/maps.js.
MAP_SLUGS = ["storm-point", "worlds-edge", "e-district", "olympus",
             "broken-moon", "kings-canyon"]
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MIN_IMAGE_PX = 500
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class Ring(BaseModel):
    round: int = Field(ge=1)
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    radius: float = Field(gt=0, le=1)


def _check_ring_chain(rings: List[Ring]) -> List[Ring]:
    """Rounds must be 1, 2, 3... with no gaps, and each center must be inside
    the previous ring. This catches misclicks before they pollute the data."""
    rings = sorted(rings, key=lambda r: r.round)
    for i, r in enumerate(rings):
        if r.round != i + 1:
            raise ValueError("rounds must start at 1 with no gaps")
        if i > 0:
            p = rings[i - 1]
            if math.hypot(r.x - p.x, r.y - p.y) > p.radius * 1.08:
                raise ValueError(f"round {r.round} center is outside round {p.round}")
    return rings


class GameIn(BaseModel):
    map: str = Field(min_length=1, max_length=40)
    season: Optional[str] = None
    mode: Mode = "other"
    source: str = "manual"
    external_id: Optional[str] = None
    rings: List[Ring] = Field(min_length=2)

    @field_validator("rings")
    @classmethod
    def rings_form_a_chain(cls, v):
        return _check_ring_chain(v)


class PredictIn(BaseModel):
    map: str
    modes: Optional[List[Mode]] = None
    target: Literal["final", "next"] = "final"
    sigma: float = Field(default=0.6, gt=0, le=5)
    rings: List[Ring] = Field(min_length=1)

    @field_validator("rings")
    @classmethod
    def rings_form_a_chain(cls, v):
        return _check_ring_chain(v)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    yield


app = FastAPI(title="Apex Zone Predictor", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/setup")
def setup():
    return FileResponse(STATIC_DIR / "setup.html")


@app.get("/api/maps")
def maps():
    with db.get_conn() as conn:
        return db.list_maps(conn)


@app.get("/api/maps/{map_slug}/radii")
def radii(map_slug: str):
    with db.get_conn() as conn:
        return db.average_radii(conn, map_slug)


async def _read_capped_body(request: Request, limit: int) -> bytes:
    """Read the raw request body, giving up as soon as it goes over the limit.
    Reading the whole body first and checking the size afterwards isn't a limit:
    a huge upload is already in memory by then."""
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise HTTPException(413, "image is larger than 20 MB")
    chunks, total = [], 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > limit:
            raise HTTPException(413, "image is larger than 20 MB")
        chunks.append(chunk)
    if not chunks:
        raise HTTPException(400, "no image data")
    return b"".join(chunks)


def _png_square_size(data: bytes) -> int:
    """Return the side length of a square PNG, rejecting anything else.

    A PNG starts with an 8-byte signature, then a 4-byte chunk length, then the
    4-byte chunk name IHDR, then width and height as big-endian uint32. So the
    size lives at bytes 16:24 and no image library is needed to read it. The
    header checks come first so a truncated file is a 400, not a struct error
    turning into a 500.
    """
    if not data.startswith(PNG_SIGNATURE):
        raise HTTPException(400, "that isn't a PNG file")
    if len(data) < 24 or data[12:16] != b"IHDR":
        raise HTTPException(400, "the PNG header is damaged")
    width, height = struct.unpack(">2I", data[16:24])
    if width != height:
        raise HTTPException(
            400, f"the map image has to be square (this one is {width}x{height})")
    if width < MIN_IMAGE_PX:
        raise HTTPException(
            400, f"the map image has to be at least {MIN_IMAGE_PX} px wide "
                 f"(this one is {width})")
    return width


def _backup_path(maps_dir: Path, map_slug: str) -> Path:
    """A free name under maps/old/. Two saves in the same second would otherwise
    produce the same timestamp, and the move would destroy the first backup."""
    old_dir = maps_dir / "old"
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = old_dir / f"{map_slug}-{stamp}.png"
    n = 2
    while path.exists():
        path = old_dir / f"{map_slug}-{stamp}-{n}.png"
        n += 1
    return path


@app.put("/api/maps/{map_slug}/image")
async def upload_map_image(map_slug: str, request: Request):
    """Replace a map image with a PNG sent as the raw request body.

    Raw bytes rather than a multipart form so static/setup.html can PUT a canvas
    blob straight across and nothing new has to be installed to parse it. This
    handler is async only because it streams the body to enforce the size cap.

    The old image is moved aside, never overwritten: every coordinate in the
    database is a fraction of the image it was clicked on, so games logged
    against the old crop only line up with the old crop.
    """
    if map_slug not in MAP_SLUGS:
        # Also the path traversal guard: nothing is joined onto a path until the
        # slug has matched this list, and a slug with a / can't match the route.
        raise HTTPException(404, "unknown map")

    data = await _read_capped_body(request, MAX_IMAGE_BYTES)
    _png_square_size(data)

    maps_dir = MAPS_DIR  # read the global so the verification script can repoint it
    dest = maps_dir / f"{map_slug}.png"
    tmp = maps_dir / f"{map_slug}.png.tmp"
    maps_dir.mkdir(parents=True, exist_ok=True)
    backup = None
    try:
        # Write the new file first: that's the step that can run out of disk, and
        # until it succeeds the existing image is untouched. os.replace (not
        # rename, which fails on Windows when the target exists) then swaps it in
        # atomically, so a crash leaves either the old image or the new one,
        # never half a PNG for the canvas to draw.
        tmp.write_bytes(data)
        if dest.exists():
            backup = _backup_path(maps_dir, map_slug)
            backup.parent.mkdir(parents=True, exist_ok=True)
            os.replace(dest, backup)
        os.replace(tmp, dest)
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        if backup is not None and backup.exists() and not dest.exists():
            os.replace(backup, dest)  # put the old image back
        raise HTTPException(500, f"could not write the image: {exc}")

    shown = None
    if backup is not None:
        try:
            shown = backup.relative_to(STATIC_DIR.parent).as_posix()
        except ValueError:
            shown = backup.as_posix()  # MAPS_DIR was repointed, show it as-is
    return {"map": map_slug, "size": len(data), "backup": shown}


@app.post("/api/games", status_code=201)
def create_game(game: GameIn):
    with db.get_conn() as conn:
        game_id = db.insert_game(
            conn, game.map, [r.model_dump() for r in game.rings],
            season=game.season, mode=game.mode,
            source=game.source, external_id=game.external_id)
    if game_id is None:
        raise HTTPException(409, "a game with this external_id already exists")
    return {"id": game_id}


@app.get("/api/games")
def list_games(map: Optional[str] = None, limit: int = 50):
    sql = "SELECT id, map, season, mode, source, created_at FROM games"
    params = []
    if map:
        sql += " WHERE map = ?"
        params.append(map)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(min(limit, 500))
    with db.get_conn() as conn:
        return [dict(r) for r in conn.execute(sql, params)]


@app.delete("/api/games/{game_id}")
def delete_game(game_id: int):
    with db.get_conn() as conn:
        cur = conn.execute("DELETE FROM games WHERE id = ?", (game_id,))
    if cur.rowcount == 0:
        raise HTTPException(404, "game not found")
    return {"deleted": game_id}


@app.post("/api/predict")
def run_predict(body: PredictIn):
    with db.get_conn() as conn:
        games = db.load_games(conn, body.map, body.modes)
    observed = [r.model_dump() for r in body.rings]
    result = predict(games, observed, target=body.target, sigma=body.sigma)
    result["games_in_db"] = len(games)
    return result


@app.get("/api/evaluate/{map_slug}")
def run_evaluate(map_slug: str, rounds_seen: int = 2, sigma: float = 0.6):
    with db.get_conn() as conn:
        games = db.load_games(conn, map_slug)
    result = evaluate(games, rounds_seen=rounds_seen, sigma=sigma)
    if result is None:
        raise HTTPException(404, "not enough games on this map to evaluate")
    return result
