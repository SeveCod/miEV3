import logging, os, sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS kia_snapshots (
  ts TEXT PRIMARY KEY, soc REAL, charging INTEGER, plugged INTEGER,
  range_km REAL, odometer REAL, lat REAL, lon REAL, battery_12v REAL, car_updated_at TEXT
);
CREATE TABLE IF NOT EXISTS charge_sessions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, start_ts TEXT, end_ts TEXT,
  soc_start REAL, soc_end REAL, kwh_est REAL, kwh_meter REAL, cost_eur REAL, source TEXT
);
CREATE TABLE IF NOT EXISTS trips (
  day TEXT, start_time TEXT, distance_km REAL, drive_min REAL, idle_min REAL,
  avg_speed REAL, max_speed REAL, PRIMARY KEY (day, start_time)
);
CREATE TABLE IF NOT EXISTS v2c_readings (
  ts TEXT PRIMARY KEY, charge_power_w REAL, charge_energy_kwh REAL,
  house_power_w REAL, charge_state INTEGER, intensity REAL, raw TEXT
);
CREATE TABLE IF NOT EXISTS daily_stats (
  day TEXT PRIMARY KEY, total_wh REAL, engine_wh REAL, climate_wh REAL, electronics_wh REAL,
  battery_care_wh REAL, regen_wh REAL, distance_km REAL
);
CREATE TABLE IF NOT EXISTS state (k TEXT PRIMARY KEY, v TEXT);
-- Ajustes editados desde el panel (settings.py), superpuestos a .env. Sin punto y coma en comentarios
CREATE TABLE IF NOT EXISTS settings (k TEXT PRIMARY KEY, v TEXT);
"""

# Columnas añadidas a kia_snapshots después del esquema inicial (ALTER idempotente en init)
SNAPSHOT_EXTRA = {
    "charge_power_kw": "REAL", "charge_remaining_min": "REAL",
    "charge_limit_ac": "INTEGER", "charge_limit_dc": "INTEGER",
    "soh": "REAL", "batt_temp_min": "REAL", "batt_temp_max": "REAL",
    "locked": "INTEGER", "doors_open": "INTEGER", "windows_open": "INTEGER",
    "consumption_30d": "REAL", "total_consumed_wh": "REAL", "total_regen_wh": "REAL",
}
SNAPSHOT_COLUMNS = ("ts", "soc", "charging", "plugged", "range_km", "odometer", "lat", "lon",
                    "battery_12v", "car_updated_at", *SNAPSHOT_EXTRA)
TRIP_COLUMNS = ("day", "start_time", "distance_km", "drive_min", "idle_min", "avg_speed", "max_speed")
DAILY_COLUMNS = ("day", "total_wh", "engine_wh", "climate_wh", "electronics_wh",
                 "battery_care_wh", "regen_wh", "distance_km")

# Marcas de tiempo en hora local sin desfase (antes del 2026-10-11) que init() pasa a UTC
TS_COLUMNS = (("kia_snapshots", "ts"), ("v2c_readings", "ts"),
              ("charge_sessions", "start_ts"), ("charge_sessions", "end_ts"))

def utcnow():
    """Marca de tiempo única del proyecto: ISO en UTC con offset (no se repite en el cambio de hora)."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def _to_utc(s):
    return datetime.fromisoformat(s).replace(tzinfo=config.LOCAL_TZ).astimezone(timezone.utc) \
        .isoformat(timespec="seconds")

def _naive(s):
    return bool(s) and "+" not in s and not s.endswith("Z")

@contextmanager
def conn():
    c = sqlite3.connect(config.DB_PATH)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()

def _m1_base(c):
    """Esquema de la beta 0.1: tablas, columnas añadidas a kia_snapshots y marcas pasadas a UTC
    (bases anteriores al control de versiones). Idempotente."""
    for stmt in SCHEMA.split(";"):
        if stmt.strip():
            c.execute(stmt)
    have = {r["name"] for r in c.execute("PRAGMA table_info(kia_snapshots)")}
    for col, typ in SNAPSHOT_EXTRA.items():
        if col not in have:
            c.execute(f"ALTER TABLE kia_snapshots ADD COLUMN {col} {typ}")
    for table, col in TS_COLUMNS:
        for (old,) in c.execute(f"SELECT {col} FROM {table} WHERE {col} NOT LIKE '%+%' "
                                f"AND {col} NOT LIKE '%Z'").fetchall():
            try:
                new = _to_utc(old)
            except (TypeError, ValueError):   # una fila ilegible no debe impedir arrancar
                logging.warning("%s.%s ilegible, se deja como está: %r", table, col, old)
                continue
            c.execute(f"UPDATE {table} SET {col}=? WHERE {col}=?", (new, old))
    r = c.execute("SELECT v FROM state WHERE k='session_start_ts'").fetchone()
    if r and _naive(r["v"]):
        try:
            c.execute("UPDATE state SET v=? WHERE k='session_start_ts'", (_to_utc(r["v"]),))
        except ValueError:
            logging.warning("session_start_ts ilegible: %r", r["v"])

# Migraciones numeradas: la N lleva la base de la versión N-1 a la N (PRAGMA user_version).
# Un cambio de esquema = una función nueva AL FINAL; las existentes no se tocan nunca.
MIGRATIONS = (_m1_base,)
SCHEMA_VERSION = len(MIGRATIONS)

def init():
    """Aplica las migraciones pendientes en una sola transacción. BEGIN IMMEDIATE hace que, si la
    API y el colector arrancan a la vez, uno espere al otro (antes podían chocar en un ALTER)."""
    c = sqlite3.connect(config.DB_PATH, timeout=30, isolation_level=None)
    c.row_factory = sqlite3.Row
    try:
        c.execute("BEGIN IMMEDIATE")
        version = c.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise RuntimeError(f"La base de datos es de una versión más nueva de MiEV3 (esquema {version}, "
                               f"este código entiende hasta el {SCHEMA_VERSION}). Actualiza MiEV3.")
        for n in range(version, SCHEMA_VERSION):
            MIGRATIONS[n](c)
            logging.info("Base de datos migrada al esquema %d", n + 1)
        c.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        c.execute("COMMIT")
    except BaseException:
        try:
            c.execute("ROLLBACK")
        except sqlite3.Error:
            pass   # que no tape el error original
        raise
    finally:
        c.close()
    try:   # contiene ubicaciones: solo para el usuario que ejecuta MiEV3
        os.chmod(config.DB_PATH, 0o600)
    except OSError as e:
        logging.warning("No se pudieron ajustar los permisos de %s: %s", config.DB_PATH, e)

def get_state(k, default=None):
    with conn() as c:
        r = c.execute("SELECT v FROM state WHERE k=?", (k,)).fetchone()
        return r["v"] if r else default

def set_state(k, v):
    with conn() as c:
        c.execute("INSERT INTO state(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, str(v)))
