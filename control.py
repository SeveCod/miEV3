"""Control de carga por consumo de la casa (opcional, experimental): pausa la carga del coche
cuando la casa consume mucho, para que no salte el limitador (ICP) de la potencia contratada.

Necesita Home Assistant con un sensor de la potencia de la casa SIN el cargador (HA_URL,
HA_TOKEN, HA_POWER_ENTITY en .env). Sin eso, el módulo no hace nada. El colector lo llama cada
minuto; las órdenes al coche salen de la misma sesión de la API que las lecturas.

Reglas:
- Casa ≥ LOAD_HIGH_KW → pide pausa (como mucho una cada PAUSE_REQ_EVERY_MIN mientras siga alta).
- Casa < LOAD_LOW_KW durante LOAD_LOW_MIN → pide reanudar. HA sin datos → no decide nada.
- Cada petición se ejecuta una vez; una más vieja que LOAD_REQ_MAX_AGE_MIN se descarta.
- Solo pausa si el coche está en casa, enchufado y CARGANDO (estado en caché, no lo despierta):
  pausar un coche que no carga haría que la reanudación arrancase una carga que no tocaba.
- Como mucho LOAD_MAX_PAUSES pausas por carga; al llegar al tope avisa y deja cargar.
- Solo reanuda una carga que pausó él, y a los LOAD_PAUSE_MAX_MIN aunque HA no lo pida.
- Una orden que falla se reintenta en los ticks siguientes, MAX_TRIES veces en total, y avisa.
- Al terminar la carga, el aviso de «Carga terminada» dice cuántas pausas hubo y si se quedó
  por debajo del límite de carga del coche (resumen_carga).
# TODO verificar: start_charge/stop_charge solo se han probado en un Kia EV3 (UE).
"""
import logging
from datetime import datetime
import requests
import config, db, sessions
from collectors import kia
from notify import notify

MAX_TRIES = 3
PAUSE_REQ_EVERY_MIN = 15
REQ, DONE, PAUSED, TRIES = "load_req", "load_req_done", "load_paused_at", "load_tries"
LOW_SINCE, LAST_PAUSE_REQ = "load_low_since", "load_last_pause_req"
# Pausas de la carga en curso: clave de la carga, número de pausas, minutos en pausa, tope avisado
CYCLE, COUNT, PAUSED_MIN, CAP_SENT = "load_cycle", "load_count", "load_paused_min", "load_cap_sent"


def activo() -> bool:
    return bool(config.HA_URL and config.HA_TOKEN and config.HA_POWER_ENTITY)


def potencia_casa() -> float | None:
    """kW de la casa según HA; None si HA no responde o el sensor no tiene valor."""
    if not activo():
        return None
    try:
        r = requests.get(f"{config.HA_URL}/api/states/{config.HA_POWER_ENTITY}", timeout=5,
                         headers={"Authorization": f"Bearer {config.HA_TOKEN}"})
        r.raise_for_status()
        return float(r.json()["state"])
    except Exception as e:
        logging.debug("Sin potencia de la casa en HA: %s", type(e).__name__)
        return None


def evaluar(now: str, kw: float | None) -> None:
    """Convierte la potencia de la casa en peticiones de pausa/reanudación."""
    if kw is None:
        return
    paused = db.get_state(PAUSED) or ""
    since = db.get_state(LOW_SINCE) or ""
    if kw >= config.LOAD_HIGH_KW:
        if since:
            db.set_state(LOW_SINCE, "")
        last = db.get_state(LAST_PAUSE_REQ) or ""
        if not paused and (not last or sessions._minutos(last, now) >= PAUSE_REQ_EVERY_MIN):
            db.set_state(LAST_PAUSE_REQ, now)
            request("pause", now)
    elif kw < config.LOAD_LOW_KW:
        if not since:
            db.set_state(LOW_SINCE, now)
            since = now
        if paused and sessions._minutos(since, now) >= config.LOAD_LOW_MIN \
                and not (db.get_state(REQ) or "").startswith("resume|"):
            request("resume", now)
    elif since:
        db.set_state(LOW_SINCE, "")


def request(action: str, now: str | None = None) -> str:
    """Guarda la petición; el colector la ejecuta en su siguiente tick."""
    req = f"{action}|{now or db.utcnow()}"
    db.set_state(REQ, req)
    return req


def _cargando(s: dict) -> bool:
    """Carga en curso según la caché. Si la librería no sabe (None), vale la potencia o el
    tiempo restante de carga."""
    if s.get("charging") is not None:
        return bool(s["charging"])
    return bool((s.get("charge_power_kw") or 0) > 0 or (s.get("charge_remaining_min") or 0) > 0)


def _motivo_para_no_pausar() -> str:
    s = kia.snapshot(kia.read())
    if sessions.en_casa(s.get("lat"), s.get("lon")) is False:
        return "el coche no está en casa"
    if s.get("plugged") == 0:
        return "el coche no está enchufado"
    soc, limite = s.get("soc"), s.get("charge_limit_ac")
    if soc is not None and limite and soc >= limite:
        return f"el coche ya está en su límite ({soc:.0f} % de {limite} %)"
    if not _cargando(s):
        return "el coche no está cargando"
    return ""


def _ciclo(now: str) -> str:
    """Clave de la carga en curso: su inicio si el colector la vio empezar; si no, el día local."""
    return db.get_state("session_start_ts") or \
        datetime.fromisoformat(now).astimezone(config.LOCAL_TZ).date().isoformat()


def _contador(now: str) -> int:
    """Pausas ya hechas en la carga en curso (pone a cero el contador al cambiar de carga)."""
    ciclo = _ciclo(now)
    if db.get_state(CYCLE) != ciclo:
        db.set_state(CYCLE, ciclo)
        db.set_state(COUNT, 0)
        db.set_state(PAUSED_MIN, 0)
        db.set_state(CAP_SENT, "")
    return int(db.get_state(COUNT) or 0)


def _orden(clave: str, start: bool) -> str:
    """'ok', 'reintentar' o 'fallo' (agotados los intentos, ya avisado)."""
    try:
        kia.charge(start)
        db.set_state(TRIES, "")
        return "ok"
    except Exception as e:
        k, _, n = (db.get_state(TRIES) or "").partition("#")
        n = int(n) + 1 if k == clave else 1
        logging.error("Control de carga %s, intento %d/%d: %s", clave, n, MAX_TRIES, e)
        if n < MAX_TRIES:
            db.set_state(TRIES, f"{clave}#{n}")
            return "reintentar"
        db.set_state(TRIES, "")
        notify(f"⚠️ {config.CAR_NAME}: no he podido {'reanudar' if start else 'pausar'} la carga "
               f"({str(e)[:150]}). Revísalo en la app del coche.", "TODAY",
               event_id=f"miev3:load-fail:{clave}")
        return "fallo"


def _pausar(now: str) -> bool:
    """True = petición terminada."""
    n = _contador(now)
    if n >= config.LOAD_MAX_PAUSES:
        if not db.get_state(CAP_SENT):
            db.set_state(CAP_SENT, now)
            notify(f"⚠️ {config.CAR_NAME}: ya van {n} pausas en esta carga; no pauso más. "
                   "Si la casa sigue consumiendo mucho, puede saltar el limitador.", "TODAY",
                   event_id=f"miev3:load-cap:{db.get_state(CYCLE)}")
        logging.info("Control de carga: no pauso, tope de %d pausas", n)
        return True
    motivo = _motivo_para_no_pausar()
    if motivo:
        logging.info("Control de carga: no pauso, %s", motivo)
        return True
    r = _orden(f"pause@{now}", start=False)
    if r == "ok":
        db.set_state(PAUSED, now)
        db.set_state(COUNT, n + 1)
        notify(f"⏸️ {config.CAR_NAME}: carga en pausa, la casa consume mucho. Se reanuda cuando baje.",
               "INFO", event_id=f"miev3:load-pause:{now}")
    return r != "reintentar"


def _reanudar(now: str, por_seguridad: bool) -> bool:
    desde = db.get_state(PAUSED)
    r = _orden(f"resume@{desde}", start=True)
    if r == "reintentar":
        return False
    dur = sessions._minutos(desde, now)
    db.set_state(PAUSED, "")
    db.set_state(PAUSED_MIN, float(db.get_state(PAUSED_MIN) or 0) + dur)
    if r == "ok":
        motivo = (f"seguridad: {config.LOAD_PAUSE_MAX_MIN:.0f} min sin señal para reanudar"
                  if por_seguridad else "la casa ya consume poco")
        notify(f"▶️ {config.CAR_NAME}: carga reanudada tras {dur:.0f} min de pausa ({motivo}).",
               "TODAY" if por_seguridad else "INFO", event_id=f"miev3:load-resume:{desde}")
    return True


def resumen_carga(ts0: str, soc_final: float) -> str:
    """Línea para el aviso de «Carga terminada» si el control pausó esa carga; si no, ''."""
    if not ts0 or db.get_state(CYCLE) != ts0:
        return ""
    n = int(db.get_state(COUNT) or 0)
    if not n:
        return ""
    texto = f"\n⏸️ {n} pausa{'s' if n > 1 else ''} por consumo de la casa " \
            f"({float(db.get_state(PAUSED_MIN) or 0):.0f} min)"
    with db.conn() as c:
        row = c.execute("SELECT charge_limit_ac FROM kia_snapshots WHERE charge_limit_ac IS NOT NULL "
                        "ORDER BY ts DESC LIMIT 1").fetchone()
    limite = float(row[0]) if row and row[0] else None
    if limite and soc_final < limite - 1:
        texto += f" · ⚠️ se quedó en {soc_final:.0f}% de {limite:.0f}%"
    return texto


def tick(now: str | None = None, kw: float | None = None, leer_ha: bool = True) -> None:
    now = now or db.utcnow()
    evaluar(now, potencia_casa() if leer_ha else kw)
    paused = db.get_state(PAUSED) or ""
    if paused and sessions._minutos(paused, now) >= config.LOAD_PAUSE_MAX_MIN:
        _reanudar(now, por_seguridad=True)
        return
    req = db.get_state(REQ) or ""
    if not req or req == db.get_state(DONE):
        return
    action, _, ts = req.partition("|")
    if sessions._minutos(ts, now) > config.LOAD_REQ_MAX_AGE_MIN:
        logging.info("Control de carga: petición caducada %s", req)
        terminado = True
    elif action == "pause":
        terminado = bool(paused) or _pausar(now)
    else:  # resume
        terminado = not paused or _reanudar(now, por_seguridad=False)
    if terminado:
        db.set_state(DONE, req)
