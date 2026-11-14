"""Cálculos de consumo, costes y agregados por periodo, sin HTTP. Los usan la API (panel e
informes) y el colector (resumen semanal), para que todos den las mismas cifras."""
from datetime import date, datetime, timedelta, timezone
import config, db, settings


def rows(sql, *a):
    with db.conn() as c:
        return [dict(r) for r in c.execute(sql, a).fetchall()]

def day(yyyymmdd):
    return datetime.strptime(yyyymmdd, "%Y%m%d")

def kwh100(wh, km):
    return wh / km / 10 if wh and km else None   # 0 Wh con km recorridos es un dato incompleto

def grid_eur(kwh_batt):
    """€ de red: la energía consumida más las pérdidas de carga, al precio de casa."""
    return kwh_batt / config.CHARGE_EFFICIENCY * config.PRICE_EUR_KWH

SOC_MIN_KM, SOC_MIN_SEGMENTS = 300, 2

def soc_factor():
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

def fuel():
    """Coche de combustión de referencia: tipo, consumo, precio y su coste cada 100 km."""
    f = settings.FUELS[config.FUEL_TYPE]
    return {"type": config.FUEL_TYPE, "label": f["label"], "short": f["short"], "kwh_per_l": f["kwh_per_l"],
            "l_100": config.FUEL_L_100, "price_eur_l": config.FUEL_PRICE_EUR_L,
            "eur_100": config.FUEL_L_100 * config.FUEL_PRICE_EUR_L}

def eff(wh, km):
    k = kwh100(wh, km)
    if k is None:
        return {"kwh_100": None, "l_100": None, "eur_100": None, "range_full_km": None, "km": km,
                "cost_eur": None, "fuel_eur": None, "saving_eur": None}
    eur, ref = grid_eur(k), fuel()
    # Coste en luz y lo que costaría el coche de combustión de referencia en esos mismos km
    cost, fuel_eur = eur * km / 100, ref["eur_100"] * km / 100
    return {"kwh_100": k, "l_100": k / ref["kwh_per_l"], "eur_100": eur,
            "range_full_km": config.BATTERY_KWH / k * 100, "km": km,
            "cost_eur": cost, "fuel_eur": fuel_eur, "saving_eur": fuel_eur - cost}

GROUPS = {"day": "%Y-%m-%d", "week": "%G-W%V", "month": "%Y-%m"}

def utc_span(first, last):
    """Inicio y fin (exclusivo) en UTC ISO de los días locales first..last (date), para filtrar
    marcas de tiempo; el fin sale del día local siguiente, así el cambio de hora no lo desplaza."""
    at = lambda d: datetime(d.year, d.month, d.day, tzinfo=config.LOCAL_TZ).astimezone(timezone.utc) \
        .isoformat(timespec="seconds")
    return at(first), at(last + timedelta(days=1))

def aggregate(group, first=date.min, last=date.max, factor=None):
    """km y tiempo de los viajes, consumo de daily_stats y cargas por periodo, entre dos días
    locales (date, ambos incluidos). Las cargas cuentan en el día en que terminan."""
    fmt = GROUPS[group]
    if first > date.min:
        a, b, (lo, hi) = first.strftime("%Y%m%d"), last.strftime("%Y%m%d"), utc_span(first, last)
    else:   # sin límites: ninguna fecha cae fuera de "" … "~"
        a, b, lo, hi = "", "~", "", "~"
    key = lambda dt: dt.strftime(fmt)
    out = {}
    def slot(p):
        return out.setdefault(p, {"period": p, "km": 0.0, "trips": 0, "drive_min": 0,
                                  "wh": 0.0, "wh_km": 0.0, "charged_kwh": 0.0, "cost_eur": 0.0, "charges": 0})
    for t in rows("SELECT day, distance_km, drive_min FROM trips WHERE day BETWEEN ? AND ?", a, b):
        s = slot(key(day(t["day"])))
        s["km"] += t["distance_km"] or 0
        s["drive_min"] += t["drive_min"] or 0
        s["trips"] += 1 if (t["distance_km"] or 0) > 0 else 0
    for d in rows("SELECT day, total_wh, distance_km FROM daily_stats WHERE day BETWEEN ? AND ?", a, b):
        s = slot(key(day(d["day"])))
        s["wh"] += d["total_wh"] or 0
        s["wh_km"] += d["distance_km"] or 0
    for c in rows("SELECT end_ts, COALESCE(kwh_meter, kwh_est) kwh, cost_eur FROM charge_sessions "
                  "WHERE end_ts >= ? AND end_ts < ?", lo, hi):
        s = slot(key(datetime.fromisoformat(c["end_ts"]).astimezone(config.LOCAL_TZ)))
        s["charged_kwh"] += c["kwh"] or 0
        s["cost_eur"] += c["cost_eur"] or 0
        s["charges"] += 1
    res, f = [], soc_factor()["soc_factor"] if factor is None else factor
    for p in sorted(out):
        s = out[p]
        wh = s.pop("wh") * f
        e = eff(wh, wh_km := s.pop("wh_km"))
        s.update(kwh=wh / 1000, kwh_km=wh_km, cost_est_eur=e["cost_eur"],
                 kwh_100=e["kwh_100"], l_100=e["l_100"], saving_eur=e["saving_eur"])
        res.append(s)
    return res

def period_totals(group, first, last, cal=None):
    """Filas por periodo, totales y odómetro entre dos días locales. Lo comparten el informe y el
    resumen semanal, para que el aviso y el panel den las mismas cifras."""
    cal = cal or soc_factor()
    lines = aggregate(group, first, last, cal["soc_factor"])
    tot = {k: sum(r[k] for r in lines) for k in ("km", "trips", "drive_min", "kwh", "kwh_km",
                                                 "charged_kwh", "cost_eur", "charges")}
    # Coste, combustión y ahorro sobre los km con consumo conocido (los de daily_stats)
    e = eff(tot["kwh"] * 1000 if tot["kwh"] else None, tot["kwh_km"])
    tot.update(kwh_100=e["kwh_100"], cost_est_eur=e["cost_eur"], fuel_eur=e["fuel_eur"],
               saving_eur=e["saving_eur"])
    # Odómetro: primera y última lectura dentro del periodo (en hora local del coche)
    odo = rows("SELECT MIN(odometer) a, MAX(odometer) b FROM kia_snapshots WHERE odometer IS NOT NULL "
               "AND ts >= ? AND ts < ?", *utc_span(first, last))[0]
    return lines, tot, {"start": odo["a"], "end": odo["b"]}

