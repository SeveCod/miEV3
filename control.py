"""Control de carga por consumo de la casa (opcional): pausa la carga del coche
cuando la casa consume mucho, para que no salte el limitador (ICP) de la potencia contratada.

Necesita Home Assistant con un sensor de la potencia de la casa SIN el cargador (HA_URL,
HA_TOKEN, HA_POWER_ENTITY en .env). Sin eso, el módulo no hace nada. El colector lo llama cada
minuto.

Mínimas llamadas (cada llamada al coche gasta cupo de la API y batería de 12 V):
- Solo con el cargador de casa: dentro del radio de casa y sin pasar de LOAD_CHARGER_MAX_KW.
- Se decide con la última lectura guardada, la que el colector hace cada KIA_INTERVAL_MIN.
- A Home Assistant solo se le pregunta si esa lectura dice que el coche carga en casa por debajo
  de su límite, si hay una pausa nuestra en curso o, con el coche en casa por debajo del límite,
  si la lectura tiene más de STALE_MIN (una carga puede haber empezado después).
- Única lectura extra del coche (en caché, no lo despierta): la casa está alta, el coche en casa
  por debajo de su límite y la lectura guardada tiene más de STALE_MIN. Como mucho una cada
  REFRESH_EVERY_MIN y REFRESH_MAX_DAY al día. Sin ella, una carga empezada con el termo ya encendido pasaba hasta
  KIA_INTERVAL_MIN sin vigilar, y el limitador salta a los 15–20 min (caso real del 14/11).

Reglas:
- Casa ≥ LOAD_HIGH_KW → pausa. Casa < LOAD_LOW_KW durante LOAD_LOW_MIN → reanuda.
- A los LOAD_PAUSE_MAX_MIN de pausa reanuda si la casa ya no está alta o HA no responde; si sigue
  alta, alarga la pausa y avisa una vez. Solo reanuda lo que pausó él.
- Como mucho LOAD_MAX_PAUSES pausas por carga; al llegar al tope avisa una vez y deja cargar.
- Una orden que falla se reintenta en los minutos siguientes; a los MAX_TRIES fallos avisa y
  no insiste en esa carga.
- Al terminar la carga, «Carga terminada» dice cuántas pausas hubo y si se quedó por debajo del límite.
Parar y reanudar: probado en un Kia EV3 (UE), el coche obedece (2026-11-15). Otros modelos: TODO verificar.
"""
import logging
from datetime import datetime
import requests
import config, db, sessions
from collectors import kia
from notify import notify

MAX_TRIES = 3
STALE_MIN = 10            # lectura guardada más vieja que esto: puede haber empezado una carga
REFRESH_EVERY_MIN = 15    # como mucho una lectura extra del coche cada tanto…
REFRESH_MAX_DAY = 12      # …y como mucho estas al día (día local)
REFRESHED, REFRESH_DAY = "load_refreshed_at", "load_refresh_day"
PAUSED, LOW_SINCE, TRIES = "load_paused_at", "load_low_since", "load_tries"
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


def _en_casa_bajo_limite(s: dict) -> bool:
    """Lectura del coche en casa y por debajo de su límite de carga."""
    if not s or sessions.en_casa(s.get("lat"), s.get("lon")) is not True:
        return False
    return not (s.get("soc") is not None and s.get("charge_limit_ac") and s["soc"] >= s["charge_limit_ac"])


def _abandonada(ciclo: str) -> bool:
    """En esta carga ya no se insiste (tope de pausas u orden fallida)."""
    return bool(ciclo) and db.get_state(GIVEN_UP) == ciclo


def _carga_vigilable(s: dict) -> bool:
    """La última lectura dice que el coche carga en el cargador de casa por debajo de su límite.
    La API no da un ID del cargador: «el de casa» = dentro del radio de casa y sin pasar de
    LOAD_CHARGER_MAX_KW (un cargador público AC trifásico o DC da más potencia)."""
    if not _en_casa_bajo_limite(s) or s.get("plugged") == 0:
        return False
    if (s.get("charge_power_kw") or 0) > config.LOAD_CHARGER_MAX_KW:
        return False
    if s.get("charging") is None:   # CCS2: la librería puede no saberlo; vale la potencia
        return (s.get("charge_power_kw") or 0) > 0 or (s.get("charge_remaining_min") or 0) > 0
    return bool(s["charging"])


def _cupo_de_lectura(now: str) -> str:
    """Valor de REFRESH_DAY tras gastar una lectura extra, o '' si no queda cupo (como mucho una
    cada REFRESH_EVERY_MIN y REFRESH_MAX_DAY por día local). Solo lee el estado."""
    ultima = db.get_state(REFRESHED) or ""
    if ultima and sessions._minutos(ultima, now) < REFRESH_EVERY_MIN:
        return ""
    dia = datetime.fromisoformat(now).astimezone(config.LOCAL_TZ).date().isoformat()
    d, _, n = (db.get_state(REFRESH_DAY) or "").partition("#")
    n = int(n) if d == dia else 0
    return f"{dia}#{n + 1}" if n < REFRESH_MAX_DAY else ""


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
        if n < MAX_TRIES:
            db.set_state(TRIES, n)
            return "reintentar"
        db.set_state(TRIES, 0)
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
    for k, v in ((PAUSED, ""), (LOW_SINCE, ""), (PAUSED_MIN, float(db.get_state(PAUSED_MIN) or 0) + dur)):
        db.set_state(k, v)
    # La pausa del cargador cuenta desde la reanudación: así la carga no se parte en dos y, si el
    # coche ya no vuelve a cargar, se cierra sola pasado CHARGE_MERGE_GAP_MIN
    if db.get_state("session_pause_ts"):
        db.set_state("session_pause_ts", now)
    if r == "ok":
        motivo = (f"{config.LOAD_PAUSE_MAX_MIN:.0f} min de pausa como máximo" if por_seguridad
                  else "la casa ya consume poco")
        notify(f"▶️ {config.CAR_NAME}: carga reanudada tras {dur:.0f} min de pausa ({motivo}).",
               "TODAY" if por_seguridad else "INFO", event_id=f"miev3:load-resume:{desde}")


def en_pausa() -> bool:
    """Hay una pausa del control en curso (sessions no cierra la carga mientras dure)."""
    return bool(db.get_state(PAUSED))


def tick(now: str | None = None, refrescar=None) -> None:
    """refrescar: función que lee el coche (en caché) y guarda la lectura; el colector pasa la suya."""
    now = now or db.utcnow()
    paused = db.get_state(PAUSED) or ""
    if paused:
        _tick_en_pausa(now, paused)
        return
    ciclo = db.get_state("session_start_ts") or ""
    s = _ultima_lectura()
    if not _en_casa_bajo_limite(s) or _abandonada(ciclo):
        return                                   # sin carga que proteger: ni HA ni coche
    if _carga_vigilable(s):
        kw = potencia_casa()
        if kw is not None and kw >= config.LOAD_HIGH_KW:
            _pausar(now, ciclo)
        return
    # La lectura dice que no carga, pero si es vieja puede haber empezado una carga después
    if refrescar is None or sessions._minutos(s["ts"], now) <= STALE_MIN:
        return
    cupo = _cupo_de_lectura(now)
    if not cupo:
        return                                   # sin cupo, HA no cambiaría nada
    kw = potencia_casa()
    if kw is None or kw < config.LOAD_HIGH_KW:
        return
    db.set_state(REFRESH_DAY, cupo)
    db.set_state(REFRESHED, now)
    refrescar()
    if _carga_vigilable(_ultima_lectura()):
        _pausar(now, db.get_state("session_start_ts") or "")


def _tick_en_pausa(now: str, paused: str) -> None:
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
    since = db.get_state(LOW_SINCE) or ""
    if kw >= config.LOAD_LOW_KW:
        if since:
            db.set_state(LOW_SINCE, "")
    elif not since:
        db.set_state(LOW_SINCE, now)
    elif sessions._minutos(since, now) >= config.LOAD_LOW_MIN:
        _reanudar(now, por_seguridad=False)


def resumen_carga(ts0: str, soc_final: float) -> str:
    """Línea para el aviso de «Carga terminada» si el control pausó esa carga; si no, ''."""
    if db.get_state(CYCLE) != ts0:
        return ""
    n = int(db.get_state(COUNT) or 0)
    if not n:
        return ""
    texto = f"\n⏸️ {n} pausa{'s' if n > 1 else ''} por consumo de la casa " \
            f"({float(db.get_state(PAUSED_MIN) or 0):.0f} min)"
    limite = float(_ultima_lectura().get("charge_limit_ac") or 0)
    if limite and soc_final < limite - 1:
        texto += f" · ⚠️ se quedó en {soc_final:.0f}% de {limite:.0f}%"
    return texto
