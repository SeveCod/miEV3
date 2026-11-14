"""Colector: coche cada N minutos, viajes diarios y resumen semanal por aviso."""
import logging, time
from datetime import datetime, timedelta, timezone
import config, db, sessions, settings
from collectors import kia
from notify import notify

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

def _insert(table, cols, rows):
    with db.conn() as c:
        c.executemany(f"INSERT OR REPLACE INTO {table} ({','.join(cols)}) "
                      f"VALUES ({','.join(':' + k for k in cols)})", rows)

def save_snapshot(s):
    # Solo columnas conocidas: una clave nueva sin columna avisa en vez de perder el snapshot
    unknown = set(s) - set(db.SNAPSHOT_COLUMNS)
    if unknown:
        logging.warning("Claves de snapshot sin columna (añadir a db.SNAPSHOT_EXTRA): %s", sorted(unknown))
    _insert("kia_snapshots", [k for k in db.SNAPSHOT_COLUMNS if k in s], [s])

def save_daily_stats(rows):
    _insert("daily_stats", db.DAILY_COLUMNS, rows)

def save_trips(day):
    rows = kia.day_trips(day)
    with db.conn() as c:
        c.executemany("""INSERT OR REPLACE INTO trips VALUES
                         (:day,:start_time,:distance_km,:drive_min,:idle_min,:avg_speed,:max_speed)""", rows)
    logging.info("Viajes %s: %d", day, len(rows))

def kia_health(ok, err=None, now=None):
    """Aviso level-triggered de Kia caído: a los KIA_DOWN_ALERT_MIN del primer fallo y después
    cada KIA_DOWN_REPEAT_H mientras siga; al volver, un aviso. Caso real: 23→25/10/2026, 2,7 días a ciegas."""
    now = now or db.utcnow()
    since = db.get_state("kia_fail_since") or ""
    sent = int(db.get_state("kia_fail_alerts") or 0)
    if ok:
        if sent:
            notify(f"✅ {config.CAR_NAME}: vuelve a responder tras {_horas(since, now)} sin datos",
                   "TODAY", event_id=f"miev3:kia-ok:{since}")
        if since:
            db.set_state("kia_fail_since", "")
            db.set_state("kia_fail_alerts", 0)
        return
    if not since:
        db.set_state("kia_fail_since", now)
        since = now
    caido_min = (datetime.fromisoformat(now) - datetime.fromisoformat(since)).total_seconds() / 60
    if caido_min >= config.KIA_DOWN_ALERT_MIN + sent * config.KIA_DOWN_REPEAT_H * 60:
        notify(f"⚠️ {config.CAR_NAME}: sin datos desde hace {_horas(since, now)} · {str(err)[:200]}\n"
               "Si es de login, mirar releases de hyundai_kia_connect_api antes de tocar credenciales.",
               "TODAY", event_id=f"miev3:kia-caido:{since}:{sent}")
        db.set_state("kia_fail_alerts", sent + 1)

def _horas(desde, hasta):
    return f"{(datetime.fromisoformat(hasta) - datetime.fromisoformat(desde)).total_seconds() / 3600:.0f} h"

TRIPS_MAX_TRIES = 3

def trips_tick(now):
    """Viajes del día desde las 23:45: como mucho TRIPS_MAX_TRIES intentos, uno cada 5 min.
    Con el login caído se reintentaba cada minuto (15 logins fallidos por noche, riesgo de bloqueo)."""
    today = now.strftime("%Y%m%d")
    if now.hour != 23 or db.get_state("trips_day") == today:
        return
    day, _, n = (db.get_state("trips_try") or "").partition(":")
    n = int(n) if day == today else 0
    if n >= TRIPS_MAX_TRIES or now.minute < 45 + 5 * n:
        return
    db.set_state("trips_try", f"{today}:{n + 1}")
    try:
        save_trips(today)
        db.set_state("trips_day", today)
    except Exception as e:
        logging.error("Error viajes (intento %d/%d): %s", n + 1, TRIPS_MAX_TRIES, e)

def weekly_summary():
    since = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat(timespec="seconds")
    with db.conn() as c:
        s = c.execute("SELECT COALESCE(SUM(kwh_est),0) k, COALESCE(SUM(cost_eur),0) e, COUNT(*) n "
                      "FROM charge_sessions WHERE end_ts>=?", (since,)).fetchone()
        km = c.execute("SELECT COALESCE(MAX(odometer)-MIN(odometer),0) km FROM kia_snapshots WHERE ts>=?",
                       (since,)).fetchone()["km"]
    per100 = s["e"] / km * 100 if km else 0
    notify(f"📊 Semana · {config.CAR_NAME}\n{km:.0f} km · {s['n']} cargas · ~{s['k']:.0f} kWh · ~{s['e']:.2f} €\n"
           f"≈ {per100:.2f} €/100 km (estimado por SoC)",
           "INFO", event_id=f"miev3:semana:{datetime.now().strftime('%G-%V')}")

def kia_tick():
    """Lee el coche y procesa la lectura. Solo un fallo al LEER cuenta para el aviso de «coche sin
    datos»; si falla guardar o procesar (fallo nuestro), se registra sin culpar al coche."""
    try:
        v = kia.read()
        snap = kia.snapshot(v)
    except Exception as e:
        logging.error("Error Kia: %s", e)
        kia_health(False, e)
        return
    kia_health(True)
    try:
        save_snapshot(snap)
        save_daily_stats(kia.daily_stats(v))
        sessions.process(snap)
        logging.info("Kia SoC %s%% cargando=%s", snap["soc"], snap["charging"])
    except Exception:
        logging.exception("Error procesando la lectura del coche")

def run():
    db.init()
    last_kia = 0
    while True:
        try:
            settings.load()  # ajustes cambiados en el panel, sin reiniciar
        except Exception as e:
            logging.error("Error leyendo ajustes: %s", e)
        now = datetime.now()
        if time.time() - last_kia >= config.KIA_INTERVAL_MIN * 60:
            kia_tick()
            last_kia = time.time()

        trips_tick(now)

        week = now.strftime("%G-%V")
        if now.weekday() == 6 and now.hour == 20 and db.get_state("summary_week") != week:
            weekly_summary()
            db.set_state("summary_week", week)

        time.sleep(60)

if __name__ == "__main__":
    run()
