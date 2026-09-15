// The map list, shared by index.html and setup.html so a new map only has to be
// added in one place. Each slug is both the games.map value in the database and
// the filename at static/maps/<slug>.png: never rename one once games are logged,
// or those games point at a map that no longer exists. The order here is the
// dropdown order. Keep in sync with MAP_SLUGS in app/main.py.
window.APEX_MAPS = [
  ["storm-point", "Storm Point"], ["worlds-edge", "World's Edge"],
  ["e-district", "E-District"], ["olympus", "Olympus"],
  ["broken-moon", "Broken Moon"], ["kings-canyon", "Kings Canyon"],
];
