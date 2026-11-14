"""API + panel web. Protegido con usuario/contraseña (incluye ubicación del coche)."""
import json, logging, math, re, secrets
from pathlib import Path
from datetime import date, datetime, timedelta, timezone
from typing import Literal
from fastapi import FastAPI, Depends, HTTPException, Header, Query, Request
from fastapi.responses import FileResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
import config, db, settings, stats

# Sin /docs ni /openapi.json: el panel no necesita describir su API a quien no ha entrado
app = FastAPI(title="MiEV3", docs_url=None, redoc_url=None, openapi_url=None)
security = HTTPBasic()
STATIC = Path(__file__).resolve().parent / "static"   # rutas fijas: no dependen del directorio de arranque
VERSION = (STATIC.parent / "VERSION").read_text().strip()

# CSP sin 'unsafe-inline' para scripts (el JS va en /app.js). Los style="" del panel necesitan
# 'unsafe-inline' en estilos, que no ejecuta código. frame-ancestors evita incrustar el panel.
CSP = ("default-src 'self'; script-src 'self' https://cdnjs.cloudflare.com; "
       "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; "
       "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")

@app.middleware("http")
async def security_headers(request: Request, call_next):
    r = await call_next(request)
    r.headers.update({"Content-Security-Policy": CSP, "X-Frame-Options": "DENY",
                      "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer"})
    return r

db.init()
settings.load()

if not config.secret_ok(config.DASH_PASS, config.DASH_PASS_MIN):
    logging.error("DASH_PASS vacía, de ejemplo, con menos de %d caracteres o con ñ/tildes: el panel "
                  "no dejará entrar a nadie. Cámbiala en .env y reinicia.", config.DASH_PASS_MIN)

def _same(a: str, b: str) -> bool:
    """Comparación en tiempo constante que admite caracteres no ASCII."""
    return secrets.compare_digest(a.encode(), b.encode())

def auth(cred: HTTPBasicCredentials = Depends(security)):
    ok = config.secret_ok(config.DASH_PASS, config.DASH_PASS_MIN) and \
         _same(cred.username, config.DASH_USER) and _same(cred.password, config.DASH_PASS)
    if not ok:
        raise HTTPException(401, headers={"WWW-Authenticate": "Basic"})

@app.get("/", dependencies=[Depends(auth)])
def index():
    return FileResponse(STATIC / "index.html")

@app.get("/app.js", dependencies=[Depends(auth)])
def app_js():
    return FileResponse(STATIC / "app.js", media_type="text/javascript")

@app.get("/img/{name}", dependencies=[Depends(auth)])
def img(name: str):
    """Iconos y la foto opcional del coche, también con contraseña (la foto es personal).
    Solo nombres simples de static/img: nada de rutas ni ficheros de otras carpetas."""
    f = STATIC / "img" / name
    if not re.fullmatch(r"[\w.-]+\.(png|webp|jpg|svg)", name) or not f.is_file():
        raise HTTPException(404)
    return FileResponse(f)

@app.get("/api/status", dependencies=[Depends(auth)])
def status():
    last = stats.rows("SELECT * FROM kia_snapshots ORDER BY ts DESC LIMIT 1")
    v2c = stats.rows("SELECT * FROM v2c_readings ORDER BY ts DESC LIMIT 1")
    since = None
    if last and last[0]["charging"]:
        try:
            soc0 = float(db.get_state("session_start_soc", ""))
        except ValueError:
            soc0 = None   # sin % inicial guardado (JSON no admite NaN)
        since = {"ts": db.get_state("session_start_ts"), "soc": soc0}
    return {"kia": last[0] if last else None, "v2c": v2c[0] if v2c else None,
            "price": config.PRICE_EUR_KWH, "charging_since": since,
            "photo": (STATIC / "img" / "car.webp").is_file(), "version": VERSION}

@app.get("/api/snapshots", dependencies=[Depends(auth)])
def snapshots(days: int = Query(7, ge=1, le=3650)):
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    return stats.rows("SELECT ts,soc,charging,range_km,odometer FROM kia_snapshots "
                "WHERE ts >= ? ORDER BY ts", since)

@app.get("/api/sessions", dependencies=[Depends(auth)])
def charge_sessions(limit: int = 50):
    return stats.rows("SELECT * FROM charge_sessions ORDER BY end_ts DESC LIMIT ?", limit)

@app.get("/api/trips", dependencies=[Depends(auth)])
def trips(days: int = Query(30, ge=1, le=3650)):
    return stats.rows("SELECT * FROM trips WHERE day >= strftime('%Y%m%d', 'now', 'localtime', ?) "
                "ORDER BY day DESC, start_time DESC", f"-{days} days")

@app.get("/api/efficiency", dependencies=[Depends(auth)])
def efficiency():
    """Consumo según Kia (daily_stats y acumulado de vida) × factor calibrado con SoC."""
    cal = stats.soc_factor()
    f = cal["soc_factor"]
    last = stats.rows("SELECT * FROM daily_stats WHERE distance_km > 0 AND total_wh > 0 ORDER BY day DESC LIMIT 1")
    # Ventana de 30 días que termina en el último día con datos
    end = last[0]["day"] if last else datetime.now().strftime("%Y%m%d")
    since = (stats.day(end) - timedelta(days=30)).strftime("%Y%m%d")
    d30 = stats.rows("""SELECT SUM(total_wh) wh, SUM(distance_km) km, SUM(engine_wh) engine_wh,
                  SUM(climate_wh) climate_wh, SUM(electronics_wh) electronics_wh,
                  SUM(battery_care_wh) battery_care_wh, SUM(regen_wh) regen_wh
                  FROM daily_stats WHERE day > ?""", since)[0]
    snap = stats.rows("SELECT total_consumed_wh wh, odometer km FROM kia_snapshots "
                "WHERE total_consumed_wh IS NOT NULL ORDER BY ts DESC LIMIT 1")
    return {
        "last_day": {"day": last[0]["day"], **stats.eff(last[0]["total_wh"] * f, last[0]["distance_km"])} if last else None,
        "d30": stats.eff(d30["wh"] * f if d30["wh"] is not None else None, d30["km"]),
        "lifetime": stats.eff(snap[0]["wh"] * f, snap[0]["km"]) if snap else None,
        **cal,
        "breakdown_30d": {k: d30[k] for k in ("engine_wh", "climate_wh", "electronics_wh",
                                              "battery_care_wh", "regen_wh")},
        "wltp_kwh_100": config.WLTP_KWH_100,
        "fuel": stats.fuel(),
    }

@app.get("/api/history", dependencies=[Depends(auth)])
def history(group: Literal["day", "week", "month"] = "month"):
    return stats.aggregate(group)

def _report_range(period, key):
    """Primer y último día (date) del informe. key: "2026-W45" (semana ISO) o "2026-11" (mes)."""
    if period == "week":
        start = datetime.strptime(key + "-1", "%G-W%V-%u").date()
        return start, start + timedelta(days=6)
    if period == "month":
        start = datetime.strptime(key + "-01", "%Y-%m-%d").date()
        nxt = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
        return start, nxt - timedelta(days=1)
    have = stats.rows("SELECT MIN(day) a FROM (SELECT day FROM trips UNION ALL SELECT day FROM daily_stats)")[0]["a"]
    return (stats.day(have).date() if have else date.today()), date.today()

@app.get("/api/report", dependencies=[Depends(auth)])
def report(period: Literal["week", "month", "all"] = "month", key: str = ""):
    """Datos de un informe: totales, filas por día (semana/mes) o por mes (histórico) y viajes."""
    try:
        first, last = _report_range(period, key)
    except (ValueError, OverflowError):
        raise HTTPException(422, "Periodo no válido: usa 2026-W45 para semanas o 2026-11 para meses")
    cal = stats.soc_factor()
    lines, tot, odo = stats.period_totals("month" if period == "all" else "day", first, last, cal)
    a, b = first.strftime("%Y%m%d"), last.strftime("%Y%m%d")
    trips = [] if period == "all" else stats.rows(
        "SELECT * FROM trips WHERE day BETWEEN ? AND ? AND distance_km > 0 ORDER BY day, start_time", a, b)
    return {"first": a, "last": b, "totals": tot, "odometer": odo,
            "rows": lines, "trips": trips, "fuel": stats.fuel(), "calibrated": cal["calibrated"],
            "generated": datetime.now(config.LOCAL_TZ).isoformat(timespec="minutes")}

@app.get("/api/insights", dependencies=[Depends(auth)])
def insights():
    """Récords, proyección anual de km y hábitos (km por día de la semana y hora de salida)."""
    trips = stats.rows("SELECT * FROM trips WHERE distance_km > 0")
    habits = [[0.0] * 24 for _ in range(7)]
    if not trips:
        return {"records": None, "projection": None, "habits": habits}
    per_day = {}
    for t in trips:
        per_day[t["day"]] = per_day.get(t["day"], 0) + t["distance_km"]
        wd = stats.day(t["day"]).weekday()
        habits[wd][int(str(t["start_time"]).zfill(6)[:2])] += t["distance_km"]
    top = max(sorted(per_day), key=lambda d: per_day[d])
    # Día más eficiente con un mínimo de km para que no gane un trayecto corto
    best = stats.rows("SELECT day, total_wh * 1.0 / distance_km / 10 kwh_100, distance_km FROM daily_stats "
                "WHERE distance_km >= 20 AND total_wh > 0 ORDER BY kwh_100, day LIMIT 1")
    if best:
        best = [{**best[0], "kwh_100": best[0]["kwh_100"] * stats.soc_factor()["soc_factor"]}]
    first, last = min(per_day), max(per_day)
    span = (stats.day(last) - stats.day(first)).days + 1
    km = sum(per_day.values())
    return {
        "records": {
            "longest_trip": max(trips, key=lambda t: t["distance_km"]),
            "top_day": {"day": top, "km": per_day[top]},
            "max_speed": max(trips, key=lambda t: t["max_speed"] or 0),
            "best_day": best[0] if best else None,
        },
        "projection": {"since": first, "days": span, "km": km,
                       "km_per_day": km / span, "km_year": km / span * 365},
        "habits": habits,
    }

@app.get("/api/settings", dependencies=[Depends(auth)])
def get_settings():
    return settings.describe()

@app.put("/api/settings", dependencies=[Depends(auth)])
async def put_settings(req: Request):
    """Guarda los ajustes enviados (solo los que cambian). Un PUT con JSON desde otro origen
    necesita preflight CORS, que esta API no concede: no se puede forzar desde otra web."""
    try:
        data = await req.json()
        if not isinstance(data, dict):
            raise ValueError("Se esperaba un objeto JSON")
        settings.save(data)
    except ValueError as e:
        raise HTTPException(422, str(e))
    return settings.describe()

def _ingest_ts(v):
    """Marca del agente en UTC ISO (como db.utcnow()); sin marca, la hora de llegada."""
    if v in (None, ""):
        return db.utcnow()
    t = datetime.fromisoformat(str(v))
    if t.tzinfo is None:
        raise ValueError("ts sin zona horaria")
    return t.astimezone(timezone.utc).isoformat(timespec="seconds")

def _num(v):
    if v is None:
        return None
    n = float(v)
    if not math.isfinite(n):
        raise ValueError("número no finito")
    return n

@app.post("/ingest/v2c")
async def ingest_v2c(req: Request, x_token: str = Header(default="")):
    if not config.secret_ok(config.INGEST_TOKEN, config.INGEST_TOKEN_MIN) or not _same(x_token, config.INGEST_TOKEN):
        raise HTTPException(403)
    # Endpoint sin contraseña del panel: todo lo que llega se valida antes de guardarlo
    try:
        d = await req.json()
        if not isinstance(d, dict):
            raise ValueError("se esperaba un objeto JSON")
        row = (_ingest_ts(d.get("ts")), _num(d.get("ChargePower")), _num(d.get("ChargeEnergy")),
               _num(d.get("HousePower")), _num(d.get("ChargeState")), _num(d.get("Intensity")),
               json.dumps(d)[:10000])
    except (ValueError, TypeError) as e:
        raise HTTPException(422, f"Lectura no válida: {e}")
    with db.conn() as c:
        c.execute("""INSERT OR REPLACE INTO v2c_readings
                     (ts, charge_power_w, charge_energy_kwh, house_power_w, charge_state, intensity, raw)
                     VALUES (?,?,?,?,?,?,?)""", row)
    return {"ok": True}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=config.API_PORT)
