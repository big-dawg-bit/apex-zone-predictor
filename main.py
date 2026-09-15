"""FastAPI backend.

Run from the project root:  uvicorn app.main:app --host 0.0.0.0 --port 8000
--host 0.0.0.0 lets your phone on the same Wi-Fi open http://<pc-ip>:8000,
so the map sits on your phone instead of covering the game.
"""
import math
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List, Literal, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from . import db
from .predict import evaluate, predict

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
Mode = Literal["algs", "ranked", "pubs", "other"]


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


@app.get("/api/maps")
def maps():
    with db.get_conn() as conn:
        return db.list_maps(conn)


@app.get("/api/maps/{map_slug}/radii")
def radii(map_slug: str):
    with db.get_conn() as conn:
        return db.average_radii(conn, map_slug)


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
