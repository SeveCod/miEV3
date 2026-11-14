"""API + panel web. Protegido con usuario/contraseña (incluye ubicación del coche)."""
import json, logging, math, secrets
from pathlib import Path
from datetime import date, datetime, timedelta, timezone
from typing import Literal
from fastapi import FastAPI, Depends, HTTPException, Header, Query, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPBasic, HTTPBasicCredentials
import config, db, settings

app = FastAPI(title="MiEV3")
security = HTTPBasic()
# Solo static/img: el resto de static/ (catálogo PDF, originales) no se publica
app.mount("/img", StaticFiles(directory="static/img"), name="img")
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
        try:
            soc0 = float(db.get_state("session_start_soc", ""))
        except ValueError:
            soc0 = None   # sin % inicial guardado (JSON no admite NaN)
        since = {"ts": db.get_state("session_start_ts"), "soc": soc0}
    return {"kia": last[0] if last else None, "v2c": v2c[0] if v2c else None,
            "price": config.PRICE_EUR_KWH, "charging_since": since,
            "photo": Path("static/img/car.webp").is_file()}

@app.get("/api/snapshots", dependencies=[Depends(auth)])
def snapshots(days: int = Query(7, ge=1, le=3650)):
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    return rows("SELECT ts,soc,charging,range_km,odometer FROM kia_snapshots "
                "WHERE ts >= ? ORDER BY ts", since)

@app.get("/api/sessions", dependencies=[Depends(auth)])
def charge_sessions(limit: int = 50):
    return rows("SELECT * FROM charge_sessions ORDER BY end_ts DESC LIMIT ?", limit)

@app.get("/api/trips", dependencies=[Depends(auth)])
def trips(days: int = Query(30, ge=1, le=3650)):
    return rows("SELECT * FROM trips WHERE day >= strftime('%Y%m%d', 'now', 'localtime', ?) "
                "ORDER BY day DESC, start_time DESC", f"-{days} days")

def _day(yyyymmdd):
    return datetime.strptime(yyyymmdd, "%Y%m%d")

def _kwh100(wh, km):
    return wh / km / 10 if wh and km else None   # 0 Wh con km recorridos es un dato incompleto

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
        if r is not None and r["charging"] != 1 and None not in (r["soc"], r["tc"], r["odometer"]):
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

def _fuel():
    """Coche de combustión de referencia: tipo, consumo, precio y su coste cada 100 km."""
    f = settings.FUELS[config.FUEL_TYPE]
    return {"type": config.FUEL_TYPE, "label": f["label"], "short": f["short"], "kwh_per_l": f["kwh_per_l"],
            "l_100": config.FUEL_L_100, "price_eur_l": config.FUEL_PRICE_EUR_L,
            "eur_100": config.FUEL_L_100 * config.FUEL_PRICE_EUR_L}

def _eff(wh, km):
    k = _kwh100(wh, km)
    if k is None:
        return {"kwh_100": None, "l_100": None, "eur_100": None, "range_full_km": None, "km": km,
                "cost_eur": None, "fuel_eur": None, "saving_eur": None}
    eur, fuel = _grid_eur(k), _fuel()
    # Coste en luz y lo que costaría el coche de combustión de referencia en esos mismos km
    cost, fuel_eur = eur * km / 100, fuel["eur_100"] * km / 100
    return {"kwh_100": k, "l_100": k / fuel["kwh_per_l"], "eur_100": eur,
            "range_full_km": config.BATTERY_KWH / k * 100, "km": km,
            "cost_eur": cost, "fuel_eur": fuel_eur, "saving_eur": fuel_eur - cost}

@app.get("/api/efficiency", dependencies=[Depends(auth)])
def efficiency():
    """Consumo según Kia (daily_stats y acumulado de vida) × factor calibrado con SoC."""
    cal = _soc_factor()
    f = cal["soc_factor"]
    last = rows("SELECT * FROM daily_stats WHERE distance_km > 0 AND total_wh > 0 ORDER BY day DESC LIMIT 1")
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
        "fuel": _fuel(),
    }

_GROUPS = {"day": "%Y-%m-%d", "week": "%G-W%V", "month": "%Y-%m"}

def _utc_span(first, last):
    """Inicio y fin (exclusivo) en UTC ISO de los días locales first..last (date), para filtrar
    marcas de tiempo; el fin sale del día local siguiente, así el cambio de hora no lo desplaza."""
    at = lambda d: datetime(d.year, d.month, d.day, tzinfo=config.LOCAL_TZ).astimezone(timezone.utc) \
        .isoformat(timespec="seconds")
    return at(first), at(last + timedelta(days=1))

def _aggregate(group, first=date.min, last=date.max, factor=None):
    """km y tiempo de los viajes, consumo de daily_stats y cargas por periodo, entre dos días
    locales (date, ambos incluidos). Las cargas cuentan en el día en que terminan."""
    fmt = _GROUPS[group]
    if first > date.min:
        a, b, (lo, hi) = first.strftime("%Y%m%d"), last.strftime("%Y%m%d"), _utc_span(first, last)
    else:   # sin límites: ninguna fecha cae fuera de "" … "~"
        a, b, lo, hi = "", "~", "", "~"
    key = lambda dt: dt.strftime(fmt)
    out = {}
    def slot(p):
        return out.setdefault(p, {"period": p, "km": 0.0, "trips": 0, "drive_min": 0,
                                  "wh": 0.0, "wh_km": 0.0, "charged_kwh": 0.0, "cost_eur": 0.0, "charges": 0})
    for t in rows("SELECT day, distance_km, drive_min FROM trips WHERE day BETWEEN ? AND ?", a, b):
        s = slot(key(_day(t["day"])))
        s["km"] += t["distance_km"] or 0
        s["drive_min"] += t["drive_min"] or 0
        s["trips"] += 1 if (t["distance_km"] or 0) > 0 else 0
    for d in rows("SELECT day, total_wh, distance_km FROM daily_stats WHERE day BETWEEN ? AND ?", a, b):
        s = slot(key(_day(d["day"])))
        s["wh"] += d["total_wh"] or 0
        s["wh_km"] += d["distance_km"] or 0
    for c in rows("SELECT end_ts, COALESCE(kwh_meter, kwh_est) kwh, cost_eur FROM charge_sessions "
                  "WHERE end_ts >= ? AND end_ts < ?", lo, hi):
        s = slot(key(datetime.fromisoformat(c["end_ts"]).astimezone(config.LOCAL_TZ)))
        s["charged_kwh"] += c["kwh"] or 0
        s["cost_eur"] += c["cost_eur"] or 0
        s["charges"] += 1
    res, f = [], _soc_factor()["soc_factor"] if factor is None else factor
    for p in sorted(out):
        s = out[p]
        wh = s.pop("wh") * f
        e = _eff(wh, wh_km := s.pop("wh_km"))
        s.update(kwh=wh / 1000, kwh_km=wh_km, cost_est_eur=e["cost_eur"],
                 kwh_100=e["kwh_100"], l_100=e["l_100"], saving_eur=e["saving_eur"])
        res.append(s)
    return res

@app.get("/api/history", dependencies=[Depends(auth)])
def history(group: Literal["day", "week", "month"] = "month"):
    return _aggregate(group)

def _report_range(period, key):
    """Primer y último día (date) del informe. key: "2026-W45" (semana ISO) o "2026-11" (mes)."""
    if period == "week":
        start = datetime.strptime(key + "-1", "%G-W%V-%u").date()
        return start, start + timedelta(days=6)
    if period == "month":
        start = datetime.strptime(key + "-01", "%Y-%m-%d").date()
        nxt = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
        return start, nxt - timedelta(days=1)
    have = rows("SELECT MIN(day) a FROM (SELECT day FROM trips UNION ALL SELECT day FROM daily_stats)")[0]["a"]
    return (_day(have).date() if have else date.today()), date.today()

@app.get("/api/report", dependencies=[Depends(auth)])
def report(period: Literal["week", "month", "all"] = "month", key: str = ""):
    """Datos de un informe: totales, filas por día (semana/mes) o por mes (histórico) y viajes."""
    try:
        first, last = _report_range(period, key)
    except (ValueError, OverflowError):
        raise HTTPException(422, "Periodo no válido: usa 2026-W45 para semanas o 2026-11 para meses")
    cal = _soc_factor()
    lines = _aggregate("month" if period == "all" else "day", first, last, cal["soc_factor"])
    tot = {k: sum(r[k] for r in lines) for k in ("km", "trips", "drive_min", "kwh", "kwh_km",
                                                 "charged_kwh", "cost_eur", "charges")}
    # Coste, combustión y ahorro sobre los km con consumo conocido (los de daily_stats)
    e = _eff(tot["kwh"] * 1000 if tot["kwh"] else None, tot["kwh_km"])
    tot.update(kwh_100=e["kwh_100"], cost_est_eur=e["cost_eur"], fuel_eur=e["fuel_eur"],
               saving_eur=e["saving_eur"])
    # Odómetro: primera y última lectura dentro del periodo (en hora local del coche)
    odo = rows("SELECT MIN(odometer) a, MAX(odometer) b FROM kia_snapshots WHERE odometer IS NOT NULL "
               "AND ts >= ? AND ts < ?", *_utc_span(first, last))[0]
    a, b = first.strftime("%Y%m%d"), last.strftime("%Y%m%d")
    trips = [] if period == "all" else rows(
        "SELECT * FROM trips WHERE day BETWEEN ? AND ? AND distance_km > 0 ORDER BY day, start_time", a, b)
    return {"first": a, "last": b, "totals": tot, "odometer": {"start": odo["a"], "end": odo["b"]},
            "rows": lines, "trips": trips, "fuel": _fuel(), "calibrated": cal["calibrated"],
            "generated": datetime.now(config.LOCAL_TZ).isoformat(timespec="minutes")}

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
                "WHERE distance_km >= 20 AND total_wh > 0 ORDER BY kwh_100, day LIMIT 1")
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
