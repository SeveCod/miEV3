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
- Pausar y reanudar por la casa no avisa (es ruido): lo cuenta «Carga terminada», con cuántas
  pausas hubo y si se quedó por debajo del límite. Solo avisa la reanudación por tiempo máximo.
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
# Comprobación de la pausa: la API acepta el stop aunque el coche siga cargando (caso real del
# 21/11: 4,3 kW 30 min después del stop y saltó el limitador). VERIFIED = pausa ya comprobada (o
# abandonada); RETRY = "<pausa>|<hora del reintento>". PUBLISHED = último kW publicado en HA.
VERIFY_MIN = 5            # minutos tras el stop (o el reintento) antes de mirar si obedeció
VERIFIED, RETRY, PUBLISHED = "load_verified", "load_retry", "load_published_kw"


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
        logging.info("Carga en pausa: la casa consume mucho")
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
        logging.info("Carga reanudada tras %.0f min de pausa", dur)
        if por_seguridad:
            notify(f"▶️ {config.CAR_NAME}: carga reanudada tras {dur:.0f} min de pausa (tiempo máximo).",
                   "TODAY", event_id=f"miev3:load-resume:{desde}")


def en_pausa() -> bool:
    """Hay una pausa del control en curso (sessions no cierra la carga mientras dure)."""
    return bool(db.get_state(PAUSED))


def _publicar_ha(kw: float) -> bool:
    """Escribe kW en el input_number HA_CAR_POWER_ENTITY. True si HA lo aceptó."""
    try:
        r = requests.post(f"{config.HA_URL}/api/services/input_number/set_value", timeout=5,
                          headers={"Authorization": f"Bearer {config.HA_TOKEN}"},
                          json={"entity_id": config.HA_CAR_POWER_ENTITY, "value": kw})
        r.raise_for_status()
        return True
    except Exception as e:
        logging.info("No he podido publicar la potencia del coche en HA: %s", type(e).__name__)
        return False


def _publicar_coche(s: dict) -> None:
    """Publica en HA la potencia de carga en casa según la última lectura (0 si no carga). Solo al
    cambiar; si HA no responde, se reintenta en la vuelta siguiente."""
    if not config.HA_CAR_POWER_ENTITY:
        return
    kw = round(float(s.get("charge_power_kw") or 0), 1) if _carga_vigilable(s) else 0.0
    if db.get_state(PUBLISHED) != str(kw) and _publicar_ha(kw):
        db.set_state(PUBLISHED, str(kw))


def _verificar_pausa(now: str, paused: str, refrescar) -> None:
    """A los VERIFY_MIN de la pausa mira si el coche dejó de cargar (lectura extra con su cupo si no
    hay una posterior). Si sigue: reintenta el stop una vez y avisa; si tampoco, avisa y lo deja."""
    if db.get_state(VERIFIED) == paused:
        return
    pausa, _, reintento = (db.get_state(RETRY) or "").partition("|")
    desde = reintento if pausa == paused and reintento else paused
    if sessions._minutos(desde, now) < VERIFY_MIN:
        return
    s = _ultima_lectura()
    if not s or sessions._minutos(desde, s["ts"]) <= 0:
        cupo = _cupo_de_lectura(now) if refrescar is not None else ""
        if not cupo:
            return                               # se mira con la próxima lectura programada
        db.set_state(REFRESH_DAY, cupo)
        db.set_state(REFRESHED, now)
        refrescar()
        s = _ultima_lectura()
        if sessions._minutos(desde, s["ts"]) <= 0:
            return
    if not _carga_vigilable(s):
        db.set_state(VERIFIED, paused)
        return
    kw = s.get("charge_power_kw") or 0
    if desde == paused:
        _orden(start=False)
        db.set_state(RETRY, f"{paused}|{now}")
        logging.warning("La pausa no se cumplió (%.1f kW): reintento", kw)
        notify(f"⚠️ {config.CAR_NAME}: pausé la carga por el consumo de la casa, pero sigue cargando "
               f"a {kw:.1f} kW. Lo reintento; si no para, puede saltar el limitador.", "CRITICAL",
               event_id=f"miev3:load-retry:{paused}")
        return
    db.set_state(VERIFIED, paused)
    logging.warning("La pausa tampoco se cumplió tras el reintento (%.1f kW)", kw)
    notify(f"⚠️ {config.CAR_NAME}: el coche no obedece la pausa y sigue cargando a {kw:.1f} kW. "
           "Páralo desde la app o el cargador: puede saltar el limitador.", "CRITICAL",
           event_id=f"miev3:load-disobey:{paused}")


def tick(now: str | None = None, refrescar=None) -> None:
    """refrescar: función que lee el coche (en caché) y guarda la lectura; el colector pasa la suya."""
    now = now or db.utcnow()
    _publicar_coche(_ultima_lectura())
    paused = db.get_state(PAUSED) or ""
    if paused:
        _verificar_pausa(now, paused, refrescar)
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
