"""API + panel web. Protegido con usuario/contraseña (incluye ubicación del coche)."""
import json, secrets
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Literal
from fastapi import FastAPI, Depends, HTTPException, Header, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPBasic, HTTPBasicCredentials
import config, db, settings

app = FastAPI(title="EV Brain")
security = HTTPBasic()
# Solo static/img: el resto de static/ (catálogo PDF, originales) no se publica
app.mount("/img", StaticFiles(directory="static/img"), name="img")
db.init()
settings.load()

def auth(cred: HTTPBasicCredentials = Depends(security)):
    ok = secrets.compare_digest(cred.username, config.DASH_USER) and \
         config.DASH_PASS and secrets.compare_digest(cred.password, config.DASH_PASS)
    if not ok:
        raise HTTPException(401, headers={"WWW-Authenticate": "Basic"})

def rows(sql, *a):
    with db.conn() as c:
        return [dict(r) for r in c.execute(sql, a).fetchall()]

@app.get("/", dependencies=[Depends(auth)])
def index():
    return FileResponse("static/index.html")

@app.get("/api/status", dependencies=[Depends(auth)])
def status():
    last = rows("SELECT * FROM kia_snapshots ORDER BY ts DESC LIMIT 1")
    v2c = rows("SELECT * FROM v2c_readings ORDER BY ts DESC LIMIT 1")
    since = None
    if last and last[0]["charging"]:
        since = {"ts": db.get_state("session_start_ts"), "soc": float(db.get_state("session_start_soc", "nan"))}
    return {"kia": last[0] if last else None, "v2c": v2c[0] if v2c else None,
            "price": config.PRICE_EUR_KWH, "charging_since": since,
            "photo": Path("static/img/car.webp").is_file()}

@app.get("/api/snapshots", dependencies=[Depends(auth)])
def snapshots(days: int = 7):
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    return rows("SELECT ts,soc,charging,range_km,odometer FROM kia_snapshots "
                "WHERE ts >= ? ORDER BY ts", since)

@app.get("/api/sessions", dependencies=[Depends(auth)])
def charge_sessions(limit: int = 50):
    return rows("SELECT * FROM charge_sessions ORDER BY end_ts DESC LIMIT ?", limit)

@app.get("/api/trips", dependencies=[Depends(auth)])
def trips(days: int = 30):
    return rows("SELECT * FROM trips WHERE day >= strftime('%Y%m%d', 'now', 'localtime', ?) "
                "ORDER BY day DESC, start_time DESC", f"-{days} days")

def _day(yyyymmdd):
    return datetime.strptime(yyyymmdd, "%Y%m%d")

def _kwh100(wh, km):
    return wh / km / 10 if wh is not None and km else None

def _grid_eur(kwh_batt):
    """€ de red: la energía consumida más las pérdidas de carga, al precio de casa."""
    return kwh_batt / config.CHARGE_EFFICIENCY * config.PRICE_EUR_KWH

SOC_MIN_KM, SOC_MIN_SEGMENTS = 300, 2

def _soc_factor():
    """Batería gastada (ΔSoC × BATTERY_KWH) / contador de Kia, en tramos sin carga de toda la historia.
    El contador se queda corto (en un Kia EV3 se midió ×1,30); un factor global vale por día solo aproximado."""
    bat = kia = km = 0.0
    segs, run = 0, []
    snaps = rows("SELECT soc, charging, odometer, total_consumed_wh tc FROM kia_snapshots ORDER BY ts")
    for r in snaps + [None]:
        if r is not None and r["charging"] != 1 and r["tc"] is not None and r["odometer"] is not None:
            run.append(r)
            continue
        if len(run) > 1:
            a, b = run[0], run[-1]
            dsoc, dkm, dtc = a["soc"] - b["soc"], b["odometer"] - a["odometer"], (b["tc"] - a["tc"]) / 1000
            if dsoc >= 10 and dkm >= 30 and dtc > 0:
                bat += dsoc / 100 * config.BATTERY_KWH; kia += dtc; km += dkm; segs += 1
        run = []
    ok = km >= SOC_MIN_KM and segs >= SOC_MIN_SEGMENTS
    return {"soc_factor": bat / kia if ok else 1.0, "soc_basis_km": km, "soc_segments": segs, "calibrated": ok}

def _diesel_eur_100():
    return config.DIESEL_L_100 * config.DIESEL_PRICE_EUR_L

def _eff(wh, km):
    k = _kwh100(wh, km)
    if k is None:
        return {"kwh_100": None, "l_100": None, "eur_100": None, "range_full_km": None, "km": km,
                "saving_eur": None}
    eur = _grid_eur(k)
    # Ahorro frente al diésel de referencia en esos mismos km
    return {"kwh_100": k, "l_100": k / config.DIESEL_KWH_PER_L, "eur_100": eur,
            "range_full_km": config.BATTERY_KWH / k * 100, "km": km,
            "saving_eur": (_diesel_eur_100() - eur) * km / 100}

@app.get("/api/efficiency", dependencies=[Depends(auth)])
def efficiency():
    """Consumo según Kia (daily_stats y acumulado de vida) × factor calibrado con SoC."""
    cal = _soc_factor()
    f = cal["soc_factor"]
    last = rows("SELECT * FROM daily_stats WHERE distance_km > 0 ORDER BY day DESC LIMIT 1")
    # Ventana de 30 días que termina en el último día con datos
    end = last[0]["day"] if last else datetime.now().strftime("%Y%m%d")
    since = (_day(end) - timedelta(days=30)).strftime("%Y%m%d")
    d30 = rows("""SELECT SUM(total_wh) wh, SUM(distance_km) km, SUM(engine_wh) engine_wh,
                  SUM(climate_wh) climate_wh, SUM(electronics_wh) electronics_wh,
                  SUM(battery_care_wh) battery_care_wh, SUM(regen_wh) regen_wh
                  FROM daily_stats WHERE day > ?""", since)[0]
    snap = rows("SELECT total_consumed_wh wh, odometer km FROM kia_snapshots "
                "WHERE total_consumed_wh IS NOT NULL ORDER BY ts DESC LIMIT 1")
    return {
        "last_day": {"day": last[0]["day"], **_eff(last[0]["total_wh"] * f, last[0]["distance_km"])} if last else None,
        "d30": _eff(d30["wh"] * f if d30["wh"] is not None else None, d30["km"]),
        "lifetime": _eff(snap[0]["wh"] * f, snap[0]["km"]) if snap else None,
        **cal,
        "breakdown_30d": {k: d30[k] for k in ("engine_wh", "climate_wh", "electronics_wh",
                                              "battery_care_wh", "regen_wh")},
        "wltp_kwh_100": config.WLTP_KWH_100,
        "diesel_kwh_per_l": config.DIESEL_KWH_PER_L,
        "diesel": {"l_100": config.DIESEL_L_100, "price_eur_l": config.DIESEL_PRICE_EUR_L,
                   "eur_100": _diesel_eur_100()},
    }

_GROUPS = {"day": "%Y-%m-%d", "week": "%G-W%V", "month": "%Y-%m"}

@app.get("/api/history", dependencies=[Depends(auth)])
def history(group: Literal["day", "week", "month"] = "month"):
    """km y tiempo de los viajes, kWh/100 de daily_stats y cargas, agrupados por periodo."""
    fmt = _GROUPS[group]
    key = lambda dt: dt.strftime(fmt)
    out = {}
    def slot(p):
        return out.setdefault(p, {"period": p, "km": 0.0, "trips": 0, "drive_min": 0,
                                  "wh": 0.0, "wh_km": 0.0, "charged_kwh": 0.0, "cost_eur": 0.0})
    for t in rows("SELECT day, distance_km, drive_min FROM trips"):
        s = slot(key(_day(t["day"])))
        s["km"] += t["distance_km"] or 0
        s["drive_min"] += t["drive_min"] or 0
        s["trips"] += 1 if (t["distance_km"] or 0) > 0 else 0
    for d in rows("SELECT day, total_wh, distance_km FROM daily_stats"):
        s = slot(key(_day(d["day"])))
        s["wh"] += d["total_wh"] or 0
        s["wh_km"] += d["distance_km"] or 0
    for c in rows("SELECT end_ts, COALESCE(kwh_meter, kwh_est) kwh, cost_eur FROM charge_sessions"):
        s = slot(key(datetime.fromisoformat(c["end_ts"]).astimezone(config.LOCAL_TZ)))
        s["charged_kwh"] += c["kwh"] or 0
        s["cost_eur"] += c["cost_eur"] or 0
    res, f = [], _soc_factor()["soc_factor"]
    for p in sorted(out):
        s = out[p]
        wh = s.pop("wh") * f
        e = _eff(wh, s.pop("wh_km"))
        s.update(cost_est_eur=_grid_eur(wh / 1000) if wh else None, kwh_100=e["kwh_100"], l_100=e["l_100"])
        res.append(s)
    return res

@app.get("/api/insights", dependencies=[Depends(auth)])
def insights():
    """Récords, proyección anual de km y hábitos (km por día de la semana y hora de salida)."""
    trips = rows("SELECT * FROM trips WHERE distance_km > 0")
    habits = [[0.0] * 24 for _ in range(7)]
    if not trips:
        return {"records": None, "projection": None, "habits": habits}
    per_day = {}
    for t in trips:
        per_day[t["day"]] = per_day.get(t["day"], 0) + t["distance_km"]
        wd = _day(t["day"]).weekday()
        habits[wd][int(str(t["start_time"]).zfill(6)[:2])] += t["distance_km"]
    top = max(sorted(per_day), key=lambda d: per_day[d])
    # Día más eficiente con un mínimo de km para que no gane un trayecto corto
    best = rows("SELECT day, total_wh * 1.0 / distance_km / 10 kwh_100, distance_km FROM daily_stats "
                "WHERE distance_km >= 20 ORDER BY kwh_100, day LIMIT 1")
    if best:
        best = [{**best[0], "kwh_100": best[0]["kwh_100"] * _soc_factor()["soc_factor"]}]
    first, last = min(per_day), max(per_day)
    span = (_day(last) - _day(first)).days + 1
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

@app.post("/ingest/v2c")
async def ingest_v2c(req: Request, x_token: str = Header(default="")):
    if not config.INGEST_TOKEN or not secrets.compare_digest(x_token, config.INGEST_TOKEN):
        raise HTTPException(403)
    d = await req.json()
    with db.conn() as c:
        c.execute("INSERT OR REPLACE INTO v2c_readings VALUES (?,?,?,?,?,?,?)", (
            d.get("ts") or db.utcnow(),
            d.get("ChargePower"), d.get("ChargeEnergy"), d.get("HousePower"),
            d.get("ChargeState"), d.get("Intensity"), json.dumps(d)))
    return {"ok": True}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=config.API_PORT)
