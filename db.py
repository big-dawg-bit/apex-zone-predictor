"""SQLite storage for ring data.

Why SQLite: one file, no database server running next to the game, and a few
thousand games is tiny for it. Coordinates are stored as fractions (0-1) of
the map image, so the data stays valid no matter what size the map is drawn at.
"""
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "zones.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS games (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    map         TEXT NOT NULL,
    season      TEXT,
    mode        TEXT NOT NULL DEFAULT 'other'
                CHECK (mode IN ('algs', 'ranked', 'pubs', 'other')),
    source      TEXT NOT NULL DEFAULT 'manual',
    external_id TEXT UNIQUE,
    created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS rings (
    game_id INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    round   INTEGER NOT NULL CHECK (round >= 1),
    x       REAL NOT NULL CHECK (x BETWEEN 0 AND 1),
    y       REAL NOT NULL CHECK (y BETWEEN 0 AND 1),
    radius  REAL NOT NULL CHECK (radius > 0),
    PRIMARY KEY (game_id, round)
);

CREATE INDEX IF NOT EXISTS idx_games_map ON games(map);
"""


@contextmanager
def get_conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    # SQLite has foreign keys off by default; without this, deleting a game
    # would leave orphan rings behind.
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.executescript(SCHEMA)


def insert_game(conn, map_slug, rings, season=None, mode="other",
                source="manual", external_id=None):
    """Insert one game with its rings. Returns the new id, or None when the
    external_id already exists (so re-running an import doesn't duplicate)."""
    cur = conn.execute(
        """INSERT OR IGNORE INTO games (map, season, mode, source, external_id)
           VALUES (?, ?, ?, ?, ?)""",
        (map_slug, season, mode, source, external_id),
    )
    if cur.rowcount == 0:
        return None
    game_id = cur.lastrowid
    conn.executemany(
        "INSERT INTO rings (game_id, round, x, y, radius) VALUES (?, ?, ?, ?, ?)",
        [(game_id, r["round"], r["x"], r["y"], r["radius"]) for r in rings],
    )
    return game_id


def load_games(conn, map_slug, modes=None):
    """Return {game_id: {round: (x, y, radius)}} for one map."""
    sql = """SELECT r.game_id, r.round, r.x, r.y, r.radius
             FROM rings r JOIN games g ON g.id = r.game_id
             WHERE g.map = ?"""
    params = [map_slug]
    if modes:
        sql += f" AND g.mode IN ({','.join('?' * len(modes))})"
        params += list(modes)
    games = {}
    for row in conn.execute(sql, params):
        games.setdefault(row["game_id"], {})[row["round"]] = (
            row["x"], row["y"], row["radius"])
    return games


def average_radii(conn, map_slug):
    """Average radius per round, learned from the data itself. The UI uses
    this so you only have to click ring centers, not drag out ring sizes."""
    rows = conn.execute(
        """SELECT r.round, AVG(r.radius) AS radius, COUNT(*) AS n
           FROM rings r JOIN games g ON g.id = r.game_id
           WHERE g.map = ? GROUP BY r.round ORDER BY r.round""",
        (map_slug,),
    )
    return [{"round": r["round"], "radius": r["radius"], "n": r["n"]} for r in rows]


def list_maps(conn):
    rows = conn.execute(
        "SELECT map, COUNT(*) AS games FROM games GROUP BY map ORDER BY map")
    return [{"map": r["map"], "games": r["games"]} for r in rows]
