"""Control de carga por consumo de la casa (control.py): decide con la última lectura guardada
y solo pregunta a Home Assistant cuando hay una carga que proteger.
Caso real (EV3, 2026-11): coche a 5,7 kW + termo 1,7 kW → el limitador de 6 kW saltó tres veces."""
import pytest

CASA = (40.0, -3.0)            # punto ficticio
T0 = "2026-11-14T14:23:00+00:00"


def t(minutos: int) -> str:
    h, m = divmod(23 + minutos, 60)
    return f"2026-11-14T{14 + h:02d}:{m:02d}:00+00:00"


@pytest.fixture
def env(tmp_path, monkeypatch):
    import config, db, control
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    for k, v in (("HOME_LAT", str(CASA[0])), ("HOME_LON", str(CASA[1])), ("LOAD_PAUSE_MAX_MIN", 90.0),
                 ("LOAD_MAX_PAUSES", 4), ("LOAD_HIGH_KW", 1.0), ("LOAD_LOW_KW", 0.6),
                 ("LOAD_LOW_MIN", 10.0), ("LOAD_CHARGER_MAX_KW", 7.4),
                 ("HA_URL", "http://ha"), ("HA_TOKEN", "x"), ("HA_POWER_ENTITY", "sensor.casa")):
        monkeypatch.setattr(config, k, v)
    db.init()
    db.set_state("session_start_ts", "2026-11-14T14:00:00+00:00")
    casa = {"kw": 0.2, "lecturas": 0}
    ordenes, avisos = [], []

    def potencia():
        casa["lecturas"] += 1
        return casa["kw"]

    def charge(start):
        if casa.get("fail"):
            casa["fail"] -= 1
            raise RuntimeError("Kia 500")
        ordenes.append("start" if start else "stop")

    monkeypatch.setattr(control, "potencia_casa", potencia)
    monkeypatch.setattr(control.kia, "charge", charge)
    monkeypatch.setattr(control.kia, "read", lambda: pytest.fail("el control no debe leer el coche"))
    monkeypatch.setattr(control, "notify", lambda text, urgency="TODAY", event_id=None:
                        avisos.append((text, urgency)) or True)
    lectura(ts="2026-11-14T14:10:00+00:00")
    return control, casa, ordenes, avisos


def lectura(**kw):
    """Guarda una lectura del coche: por defecto, cargando en casa a 5,7 kW, al 50 % de 80 %."""
    import db
    s = {"ts": "2026-11-14T14:10:00+00:00", "soc": 50, "charging": 1, "plugged": 1, "lat": CASA[0],
         "lon": CASA[1], "charge_power_kw": 5.7, "charge_remaining_min": 200, "charge_limit_ac": 80, **kw}
    with db.conn() as c:
        c.execute(f"INSERT OR REPLACE INTO kia_snapshots ({','.join(s)}) VALUES ({','.join('?' * len(s))})",
                  list(s.values()))


def correr(control, casa, desde, hasta, kw):
    casa["kw"] = kw
    for m in range(desde, hasta):
        control.tick(t(m))


def test_pausa_y_reanuda_una_vez(env):
    control, casa, ordenes, avisos = env
    correr(control, casa, 0, 5, kw=1.7)
    assert ordenes == ["stop"]                         # solo una vez aunque siga alta
    correr(control, casa, 5, 16, kw=0.3)              # 10 min por debajo de 0,6
    assert ordenes == ["stop", "start"]
    assert avisos == []


@pytest.mark.parametrize("cambio", [
    {"soc": 80},                                       # en su límite: caso real del 14/11
    {"charging": 0, "charge_power_kw": 0, "charge_remaining_min": 0},   # enchufado sin cargar
    {"plugged": 0, "charging": 0},
    {"lat": 40.4},                                     # fuera de casa (~45 km)
    {"lat": None, "lon": None},                        # ubicación desconocida
    {"charge_power_kw": 10.7},                         # cargador público AC trifásico
    {"charge_power_kw": 45.7},                         # cargador rápido DC
])
def test_sin_carga_en_casa_no_pregunta_a_ha_ni_al_coche(env, cambio):
    control, casa, ordenes, avisos = env
    lectura(ts="2026-11-14T14:20:00+00:00", **cambio)
    correr(control, casa, 0, 120, kw=1.8)
    assert ordenes == [] and avisos == [] and casa["lecturas"] == 0


def test_sin_casa_configurada_esta_inactivo(env, monkeypatch):
    control, *_ = env
    import config
    monkeypatch.setattr(config, "HOME_LAT", "")
    assert not control.activo()


def test_cargando_desconocido_usa_la_potencia(env):
    control, casa, ordenes, _ = env
    lectura(ts="2026-11-14T14:20:00+00:00", charging=None)
    correr(control, casa, 0, 2, kw=1.7)
    assert ordenes == ["stop"]


def test_tras_reanudar_no_supone_que_carga_lo_confirma_leyendo(env):
    """La lectura guardada es de la pausa (sin cargar). Tras reanudar no se da por hecho que carga
    (pausar un coche que no carga es el fallo real del 14/11): con la casa alta y esa lectura
    vieja, una lectura extra lo confirma y entonces pausa."""
    control, casa, ordenes, _ = env
    correr(control, casa, 0, 1, kw=1.7)
    lectura(ts=t(5), charging=0, charge_power_kw=0, charge_remaining_min=0)   # lectura en pausa
    correr(control, casa, 1, 12, kw=0.3)
    assert ordenes == ["stop", "start"]
    casa["kw"] = 1.7
    refrescar, llamadas = _refresco({"ts": t(16)})               # ya carga otra vez
    control.tick(t(14), refrescar=refrescar)                     # lectura de hace 9 min: no lee
    assert ordenes == ["stop", "start"] and llamadas == []
    control.tick(t(16), refrescar=refrescar)                     # 11 min: lee, carga → pausa
    assert ordenes == ["stop", "start", "stop"] and len(llamadas) == 1


def test_reanuda_por_seguridad_sin_ha(env):
    control, casa, ordenes, avisos = env
    correr(control, casa, 0, 1, kw=1.7)
    casa["kw"] = None
    correr(control, casa, 1, 91, kw=None)
    assert ordenes == ["stop", "start"] and avisos[-1][1] == "TODAY"


def test_reintenta_y_no_insiste_tras_tres_fallos(env):
    control, casa, ordenes, avisos = env
    casa["fail"] = 3
    correr(control, casa, 0, 10, kw=1.7)
    assert ordenes == [] and avisos[-1][1] == "TODAY" and "pausar" in avisos[-1][0]
    assert len(avisos) == 1                            # no insiste en esta carga


def test_tope_de_pausas_por_carga_avisa_una_vez(env):
    control, casa, ordenes, avisos = env
    for i in range(6):
        correr(control, casa, 20 * i, 20 * i + 1, kw=1.7)
        correr(control, casa, 20 * i + 1, 20 * i + 13, kw=0.3)
    assert ordenes.count("stop") == 4
    assert len([a for a in avisos if "no pauso más" in a[0]]) == 1
    import db
    db.set_state("session_start_ts", "2026-11-15T01:00:00+00:00")   # carga nueva
    correr(control, casa, 200, 201, kw=1.7)
    assert ordenes.count("stop") == 5


def test_umbrales_con_lecturas_reales(env):
    """Lecturas reales de la casa: el termo (1,67 kW) pausa; 0,8 kW (entre umbrales) no
    reanuda; 10 min por debajo de 0,6 kW sí."""
    control, casa, ordenes, _ = env
    for m, kw in ((0, 0.21), (1, 1.67), (6, 1.75), (16, 0.35), (21, 0.8), (26, 0.26),
                  (31, 0.26), (35, 0.23), (36, 0.19)):
        casa["kw"] = kw
        control.tick(t(m))
        if m == 31:
            assert ordenes == ["stop"]
    assert ordenes == ["stop", "start"]


def test_la_pausa_no_parte_la_carga_en_dos(env, monkeypatch):
    import config, db, sessions
    control, casa, ordenes, _ = env
    monkeypatch.setattr(config, "CHARGE_MERGE_GAP_MIN", 60.0)
    avisos = []
    monkeypatch.setattr(sessions, "notify", lambda text, urgency="TODAY", event_id=None:
                        avisos.append(text) or True)
    db.set_state("charging", "1")
    db.set_state("session_start_soc", 50)
    correr(control, casa, 0, 1, kw=1.7)                # pausa en t(0)
    snap = {"soc": 55, "charging": 0, "plugged": 1, "range_km": 300, "lat": CASA[0], "lon": CASA[1]}
    for m in (5, 35, 65, 85):                          # lecturas en pausa (> 60 min)
        sessions.process({**snap, "ts": t(m)})
    correr(control, casa, 85, 91, kw=None)             # reanuda por seguridad a los 90 min
    sessions.process({**snap, "ts": t(95), "charging": 1, "soc": 56})
    sessions.process({**snap, "ts": t(200), "charging": 0, "soc": 70})
    sessions.process({**snap, "ts": t(300), "charging": 0, "soc": 70})
    assert len(avisos) == 1 and "50% → 70%" in avisos[0]
    assert "1 pausa por consumo de la casa (90 min)" in avisos[0]


def test_pausa_maxima_con_la_casa_aun_alta_alarga_y_avisa_una_vez(env):
    """Horno o estufa: a los 90 min la casa sigue alta; reanudar haría saltar el limitador."""
    control, casa, ordenes, avisos = env
    correr(control, casa, 0, 1, kw=1.7)
    correr(control, casa, 1, 120, kw=1.7)
    assert ordenes == ["stop"]
    alargadas = [a for a in avisos if "Alargo la pausa" in a[0]]
    assert len(alargadas) == 1 and alargadas[0][1] == "TODAY"
    correr(control, casa, 120, 131, kw=0.3)            # baja: reanuda por la vía normal
    assert ordenes == ["stop", "start"]


def test_pausa_maxima_reanuda_si_la_casa_ya_esta_entre_umbrales(env):
    control, casa, ordenes, _ = env
    correr(control, casa, 0, 1, kw=1.7)
    correr(control, casa, 1, 91, kw=0.8)               # ni alta ni baja: a los 90 min reanuda
    assert ordenes == ["stop", "start"]


def test_tope_por_defecto_es_8_pausas():
    import importlib, config
    assert importlib.reload(config).LOAD_MAX_PAUSES == 8


def _refresco(nueva: dict | None):
    """Lectura extra simulada: cuenta las llamadas y guarda `nueva` como lectura del coche."""
    llamadas = []

    def refrescar():
        llamadas.append(1)
        if nueva is not None:
            lectura(**nueva)
    return refrescar, llamadas


PARADO = {"charging": 0, "plugged": 0, "charge_power_kw": 0, "charge_remaining_min": 0}


def test_carga_empezada_con_la_casa_alta_se_detecta_con_una_lectura_extra(env):
    """Coche enchufado después de la última lectura con el termo ya encendido (punto ciego de
    hasta KIA_INTERVAL_MIN; el limitador salta a los 15–20 min)."""
    control, casa, ordenes, _ = env
    lectura(ts="2026-11-14T14:10:00+00:00", **PARADO)        # 13 min antes de t(0)
    refrescar, llamadas = _refresco({"ts": t(0)})              # ahora sí carga
    casa["kw"] = 1.7
    control.tick(t(0), refrescar=refrescar)
    assert llamadas == [1] and ordenes == ["stop"]


def test_lectura_extra_como_mucho_cada_15_min(env):
    control, casa, ordenes, _ = env
    lectura(ts="2026-11-14T14:10:00+00:00", **PARADO)
    refrescar, llamadas = _refresco(None)                      # sigue sin cargar
    casa["kw"] = 1.7
    for m in range(0, 31):
        control.tick(t(m), refrescar=refrescar)
    assert len(llamadas) == 3 and ordenes == []                # t(0), t(15), t(30)


@pytest.mark.parametrize("cambio,minutos", [
    ({"lat": 40.4}, 13),                                       # fuera de casa
    ({"soc": 80}, 13),                                         # en su límite
    ({}, 5),                                                   # lectura reciente (< 10 min)
])
def test_sin_lectura_extra_si_no_puede_estar_cargando_en_casa(env, cambio, minutos):
    control, casa, ordenes, _ = env
    h, m = divmod(23 - minutos, 60)
    lectura(ts=f"2026-11-14T{14 + h:02d}:{m:02d}:00+00:00", **{**PARADO, **cambio})
    refrescar, llamadas = _refresco({"ts": t(0)})
    casa["kw"] = 1.7
    control.tick(t(0), refrescar=refrescar)
    assert llamadas == [] and ordenes == []


def test_sin_lectura_extra_con_la_casa_baja(env):
    control, casa, ordenes, _ = env
    lectura(ts="2026-11-14T14:10:00+00:00", **PARADO)
    refrescar, llamadas = _refresco({"ts": t(0)})
    for m in range(0, 30):
        casa["kw"] = 0.4
        control.tick(t(m), refrescar=refrescar)
    assert llamadas == [] and ordenes == []


def test_lectura_extra_como_mucho_12_al_dia(env):
    control, casa, ordenes, _ = env
    lectura(ts="2026-11-14T14:10:00+00:00", **PARADO)
    refrescar, llamadas = _refresco(None)
    casa["kw"] = 1.7
    for m in range(0, 15 * 20, 15):                            # 20 ocasiones el mismo día
        control.tick(t(m), refrescar=refrescar)
    assert len(llamadas) == 12


def test_reanudada_y_desenchufada_la_carga_se_cierra(env, monkeypatch):
    """Si tras reanudar el coche ya no vuelve a cargar, la carga se cierra al pasar el hueco
    (antes quedaba abierta y la siguiente carga, días después, se unía a ella)."""
    import config, db, sessions
    control, casa, ordenes, _ = env
    monkeypatch.setattr(config, "CHARGE_MERGE_GAP_MIN", 60.0)
    avisos = []
    monkeypatch.setattr(sessions, "notify", lambda text, urgency="TODAY", event_id=None:
                        avisos.append(text) or True)
    db.set_state("charging", "1")
    db.set_state("session_start_soc", 50)
    correr(control, casa, 0, 1, kw=1.7)                  # pausa en t(0)
    snap = {"soc": 55, "charging": 0, "plugged": 1, "range_km": 300, "lat": CASA[0], "lon": CASA[1]}
    sessions.process({**snap, "ts": t(5)})
    correr(control, casa, 1, 12, kw=0.3)                 # reanuda en t(11)
    sessions.process({**snap, "ts": t(40), "plugged": 0})
    assert avisos == []                                  # aún dentro del hueco desde t(11)
    sessions.process({**snap, "ts": t(75), "plugged": 0})
    assert len(avisos) == 1 and "50% → 55%" in avisos[0]


def test_sin_sesion_abierta_el_tope_no_bloquea_el_control(env):
    import db
    control, casa, ordenes, _ = env
    db.set_state("session_start_ts", "")
    db.set_state(control.GIVEN_UP, "")
    correr(control, casa, 0, 1, kw=1.7)
    assert ordenes == ["stop"]
