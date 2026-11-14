"""Detecta sesiones de carga a partir del SoC del coche (funciona sin datos del V2C).
Si llegan datos del V2C se guardan aparte; kwh_meter queda para cuadrarlos.

Una pausa del cargador menor que CHARGE_MERGE_GAP_MIN (perfiles, potencia
limitada) no parte la carga: al dejar de cargar la sesión queda en pausa y solo
se cierra —una fila y un único aviso— cuando pasa ese tiempo sin reanudarse.
Antes cada tramo era una sesión con su «Carga iniciada» y su «Carga terminada»
(el 21/10/2026, tres sesiones para una noche). Al empezar no se avisa."""
import logging, math
from datetime import datetime
import db, config
from notify import notify


def _minutos(desde: str, hasta: str) -> float:
    return (datetime.fromisoformat(hasta) - datetime.fromisoformat(desde)).total_seconds() / 60


def _distancia_m(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6_371_000 * math.asin(math.sqrt(a))


def en_casa(lat, lon) -> bool | None:
    """True/False según HOME_RADIUS_M; None si falta la ubicación o el punto «Casa» (o es ilegible)."""
    if lat in (None, "") or lon in (None, "") or not (config.HOME_LAT and config.HOME_LON):
        return None
    try:
        return _distancia_m(float(lat), float(lon), float(config.HOME_LAT),
                            float(config.HOME_LON)) <= config.HOME_RADIUS_M
    except ValueError:
        return None


def punto(lat, lon) -> str:
    """«🏠 Casa» si cae dentro de HOME_RADIUS_M de HOME_LAT/HOME_LON; si no, las coordenadas."""
    if lat in (None, "") or lon in (None, ""):
        return "📍 ubicación desconocida"
    try:
        lat, lon = float(lat), float(lon)
    except ValueError:
        return "📍 ubicación desconocida"
    return "🏠 Casa" if en_casa(lat, lon) else f"📍 {lat:.4f}, {lon:.4f}"


def _hora(ts: str) -> str:
    return datetime.fromisoformat(ts).astimezone(config.LOCAL_TZ).strftime("%H:%M")


SESSION_KEYS = ("session_start_ts", "session_start_soc", "session_pause_ts",
                "session_end_soc", "session_lat", "session_lon")


def _cerrar_sesion():
    """Guarda la sesión (inicio → última pausa) y manda el único aviso. La fila y la limpieza
    del estado van en la misma transacción: si algo falla, no se guarda nada y no se repite."""
    ts0, ts1 = db.get_state("session_start_ts"), db.get_state("session_pause_ts")
    soc0, soc1 = float(db.get_state("session_start_soc")), float(db.get_state("session_end_soc"))
    kwh = max(soc1 - soc0, 0) / 100 * config.BATTERY_KWH / config.CHARGE_EFFICIENCY
    cost = kwh * config.PRICE_EUR_KWH
    lugar = punto(db.get_state("session_lat"), db.get_state("session_lon"))
    texto = (f"✅ Carga terminada · {soc0:.0f}% → {soc1:.0f}% · ~{kwh:.1f} kWh · ~{cost:.2f} € "
             f"({config.PRICE_EUR_KWH:.3f} €/kWh)\n{lugar} · {_hora(ts0)}–{_hora(ts1)}")
    import control   # aquí: control importa sessions
    try:   # el control de carga es opcional: un fallo suyo no impide guardar la carga
        texto += control.resumen_carga(ts0, soc1)
    except Exception:
        logging.exception("Resumen del control de carga")
    with db.conn() as c:
        c.execute("""INSERT INTO charge_sessions(start_ts,end_ts,soc_start,soc_end,kwh_est,cost_eur,source)
                     VALUES(?,?,?,?,?,?, 'kia_soc')""", (ts0, ts1, soc0, soc1, kwh, cost))
        c.executemany("UPDATE state SET v='' WHERE k=?", [(k,) for k in SESSION_KEYS])
    notify(texto, "TODAY", event_id=f"miev3:carga:{ts0}")


def process(snap: dict):
    prev_charging = db.get_state("charging", "0") == "1"
    # Estado de carga desconocido: se mantiene el anterior para no cerrar una sesión en falso
    now_charging = prev_charging if snap["charging"] is None else bool(snap["charging"])
    soc = snap["soc"]
    if soc is None:
        return
    ts = snap["ts"]
    pausa = db.get_state("session_pause_ts")

    if now_charging and not prev_charging:
        # Una pausa del control de carga (control.py) tampoco parte la carga
        if pausa and (_minutos(pausa, ts) < config.CHARGE_MERGE_GAP_MIN
                      or (db.get_state("load_resumed_at") or "") > pausa):
            db.set_state("session_pause_ts", "")  # se reanuda la misma carga
        else:
            if pausa:
                _cerrar_sesion()  # la pausa ya superaba el umbral: cierra la anterior
            db.set_state("session_start_ts", ts)
            db.set_state("session_start_soc", soc)
            db.set_state("session_lat", "" if snap.get("lat") is None else snap["lat"])
            db.set_state("session_lon", "" if snap.get("lon") is None else snap["lon"])

    elif prev_charging and not now_charging and db.get_state("session_start_ts"):
        db.set_state("session_pause_ts", ts)
        db.set_state("session_end_soc", soc)

    elif not now_charging and pausa and _minutos(pausa, ts) >= config.CHARGE_MERGE_GAP_MIN \
            and not db.get_state("load_paused_at") and (db.get_state("load_resumed_at") or "") <= pausa:
        _cerrar_sesion()

    if soc <= config.LOW_SOC_ALERT and not snap["plugged"] and db.get_state("low_alert") != "1":
        notify(f"🪫 Batería al {soc:.0f}% y sin enchufar · autonomía {snap['range_km']} km",
               "TODAY", event_id=f"miev3:bateria:{ts[:10]}")
        db.set_state("low_alert", "1")
    elif soc > config.LOW_SOC_ALERT + 5:
        db.set_state("low_alert", "0")

    db.set_state("charging", "1" if now_charging else "0")
