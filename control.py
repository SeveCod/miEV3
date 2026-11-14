"""Control de carga por consumo de la casa (opcional, experimental): pausa la carga del coche
cuando la casa consume mucho, para que no salte el limitador (ICP) de la potencia contratada.

Necesita Home Assistant con un sensor de la potencia de la casa SIN el cargador (HA_URL,
HA_TOKEN, HA_POWER_ENTITY en .env). Sin eso, el módulo no hace nada. El colector lo llama cada
minuto.

Mínimas llamadas (cada llamada al coche gasta cupo de la API y batería de 12 V):
- Solo con el cargador de casa: dentro del radio de casa y sin pasar de LOAD_CHARGER_MAX_KW.
- Al coche NO se le pregunta nada: se decide con la última lectura guardada, la que el colector
  hace cada KIA_INTERVAL_MIN. Al coche solo le llegan las órdenes de parar o reanudar.
- A Home Assistant solo se le pregunta si esa lectura dice que el coche carga en casa por debajo
  de su límite, o si hay una pausa nuestra en curso. Con el coche lleno o sin cargar, nada.

Reglas:
- Casa ≥ LOAD_HIGH_KW → pausa. Casa < LOAD_LOW_KW durante LOAD_LOW_MIN → reanuda.
- A los LOAD_PAUSE_MAX_MIN de pausa reanuda si la casa ya no está alta o HA no responde; si sigue
  alta, alarga la pausa y avisa una vez. Solo reanuda lo que pausó él.
- Como mucho LOAD_MAX_PAUSES pausas por carga; al llegar al tope avisa una vez y deja cargar.
- Una orden que falla se reintenta en los minutos siguientes; a los MAX_TRIES fallos avisa y
  no insiste en esa carga.
- Al terminar la carga, «Carga terminada» dice cuántas pausas hubo y si se quedó por debajo del límite.
# TODO verificar: start_charge/stop_charge solo se han probado en un Kia EV3 (UE).
"""
import logging
import requests
import config, db, sessions
from collectors import kia
from notify import notify

MAX_TRIES = 3
PAUSED, RESUMED, LOW_SINCE, TRIES = "load_paused_at", "load_resumed_at", "load_low_since", "load_tries"
EXTENDED = "load_extended"   # pausa (su inicio) ya alargada y avisada
# Carga en curso (session_start_ts): pausas hechas, minutos en pausa y si ya no se insiste
CYCLE, COUNT, PAUSED_MIN, GIVEN_UP = "load_cycle", "load_count", "load_paused_min", "load_given_up"


def activo() -> bool:
    """Home Assistant configurado y la casa definida (sin casa no se sabe qué cargador es el nuestro)."""
    return bool(config.HA_URL and config.HA_TOKEN and config.HA_POWER_ENTITY
                and config.HOME_LAT and config.HOME_LON)


def potencia_casa() -> float | None:
    """kW de la casa según HA; None si HA no responde o el sensor no tiene valor."""
    try:
        r = requests.get(f"{config.HA_URL}/api/states/{config.HA_POWER_ENTITY}", timeout=5,
                         headers={"Authorization": f"Bearer {config.HA_TOKEN}"})
        r.raise_for_status()
        return float(r.json()["state"])
    except Exception as e:
        logging.info("Sin potencia de la casa en HA: %s", type(e).__name__)
        return None


def _ultima_lectura() -> dict:
    with db.conn() as c:
        row = c.execute("SELECT * FROM kia_snapshots ORDER BY ts DESC LIMIT 1").fetchone()
    return dict(row) if row else {}


def _carga_vigilable(s: dict, ciclo: str) -> bool:
    """La última lectura dice que el coche carga en el cargador de casa por debajo de su límite (o
    lo acabamos de reanudar y aún no hay lectura nueva), y en esta carga no hemos llegado al tope.
    La API no da un ID del cargador: «el de casa» = dentro del radio de casa y sin pasar de
    LOAD_CHARGER_MAX_KW (un cargador público AC trifásico o DC da más potencia)."""
    if not s or db.get_state(GIVEN_UP) == ciclo:
        return False
    if sessions.en_casa(s.get("lat"), s.get("lon")) is not True or s.get("plugged") == 0:
        return False
    if (s.get("charge_power_kw") or 0) > config.LOAD_CHARGER_MAX_KW:
        return False
    if s.get("soc") is not None and s.get("charge_limit_ac") and s["soc"] >= s["charge_limit_ac"]:
        return False
    if s.get("charging") is None:   # CCS2: la librería puede no saberlo; vale la potencia
        cargando = (s.get("charge_power_kw") or 0) > 0 or (s.get("charge_remaining_min") or 0) > 0
    else:
        cargando = bool(s["charging"])
    return cargando or (db.get_state(RESUMED) or "") > s["ts"]


def _contador(ciclo: str) -> int:
    """Pausas ya hechas en la carga en curso (a cero al cambiar de carga)."""
    if db.get_state(CYCLE) != ciclo:
        for k, v in ((CYCLE, ciclo), (COUNT, 0), (PAUSED_MIN, 0)):
            db.set_state(k, v)
    return int(db.get_state(COUNT) or 0)


def _orden(start: bool) -> str:
    """'ok', 'reintentar' o 'fallo' (MAX_TRIES fallos seguidos, ya avisado)."""
    try:
        kia.charge(start)
        db.set_state(TRIES, 0)
        return "ok"
    except Exception as e:
        n = int(db.get_state(TRIES) or 0) + 1
        logging.error("Control de carga (%s), intento %d/%d: %s",
                      "reanudar" if start else "pausar", n, MAX_TRIES, e)
        db.set_state(TRIES, 0 if n >= MAX_TRIES else n)
        if n < MAX_TRIES:
            return "reintentar"
        notify(f"⚠️ {config.CAR_NAME}: no he podido {'reanudar' if start else 'pausar'} la carga "
               f"({str(e)[:150]}). Revísalo en la app del coche.", "TODAY",
               event_id=f"miev3:load-fail:{db.utcnow()}")
        return "fallo"


def _pausar(now: str, ciclo: str) -> None:
    n = _contador(ciclo)
    if n >= config.LOAD_MAX_PAUSES:
        db.set_state(GIVEN_UP, ciclo)
        notify(f"⚠️ {config.CAR_NAME}: ya van {n} pausas en esta carga; no pauso más. "
               "Si la casa sigue consumiendo mucho, puede saltar el limitador.", "TODAY",
               event_id=f"miev3:load-cap:{ciclo}")
        return
    r = _orden(start=False)
    if r == "ok":
        db.set_state(PAUSED, now)
        db.set_state(COUNT, n + 1)
        notify(f"⏸️ {config.CAR_NAME}: carga en pausa, la casa consume mucho. Se reanuda cuando baje.",
               "INFO", event_id=f"miev3:load-pause:{now}")
    elif r == "fallo":
        db.set_state(GIVEN_UP, ciclo)


def _reanudar(now: str, por_seguridad: bool) -> None:
    desde = db.get_state(PAUSED)
    r = _orden(start=True)
    if r == "reintentar":
        return
    dur = sessions._minutos(desde, now)
    for k, v in ((PAUSED, ""), (LOW_SINCE, ""), (RESUMED, now),
                 (PAUSED_MIN, float(db.get_state(PAUSED_MIN) or 0) + dur)):
        db.set_state(k, v)
    if r == "ok":
        motivo = (f"{config.LOAD_PAUSE_MAX_MIN:.0f} min de pausa como máximo" if por_seguridad
                  else "la casa ya consume poco")
        notify(f"▶️ {config.CAR_NAME}: carga reanudada tras {dur:.0f} min de pausa ({motivo}).",
               "TODAY" if por_seguridad else "INFO", event_id=f"miev3:load-resume:{desde}")


def tick(now: str | None = None) -> None:
    now = now or db.utcnow()
    paused = db.get_state(PAUSED) or ""
    if paused:
        kw = potencia_casa()
        if sessions._minutos(paused, now) >= config.LOAD_PAUSE_MAX_MIN:
            if kw is None or kw < config.LOAD_HIGH_KW:
                _reanudar(now, por_seguridad=True)
                return
            # La casa sigue alta (horno, estufa): reanudar haría saltar el limitador. Se alarga.
            if db.get_state(EXTENDED) != paused:
                db.set_state(EXTENDED, paused)
                notify(f"⚠️ {config.CAR_NAME}: la carga lleva {config.LOAD_PAUSE_MAX_MIN:.0f} min en "
                       f"pausa y la casa sigue en {kw:.1f} kW. Alargo la pausa hasta que baje.",
                       "TODAY", event_id=f"miev3:load-extend:{paused}")
        if kw is None:
            return
        if kw >= config.LOAD_LOW_KW:
            db.set_state(LOW_SINCE, "")
            return
        since = db.get_state(LOW_SINCE) or ""
        if not since:
            db.set_state(LOW_SINCE, now)
        elif sessions._minutos(since, now) >= config.LOAD_LOW_MIN:
            _reanudar(now, por_seguridad=False)
        return
    ciclo = db.get_state("session_start_ts") or ""
    if not _carga_vigilable(_ultima_lectura(), ciclo):
        return                                   # sin carga que proteger: ni HA ni coche
    kw = potencia_casa()
    if kw is not None and kw >= config.LOAD_HIGH_KW:
        _pausar(now, ciclo)


def resumen_carga(ts0: str, soc_final: float) -> str:
    """Línea para el aviso de «Carga terminada» si el control pausó esa carga; si no, ''."""
    if db.get_state(CYCLE) != ts0:
        return ""
    n = int(db.get_state(COUNT) or 0)
    if not n:
        return ""
    texto = f"\n⏸️ {n} pausa{'s' if n > 1 else ''} por consumo de la casa " \
            f"({float(db.get_state(PAUSED_MIN) or 0):.0f} min)"
    limite = _ultima_lectura().get("charge_limit_ac")
    if limite and soc_final < float(limite) - 1:
        texto += f" · ⚠️ se quedó en {soc_final:.0f}% de {float(limite):.0f}%"
    return texto
