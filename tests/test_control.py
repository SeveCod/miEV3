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
    assert [u for _, u in avisos] == ["INFO", "INFO"]


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


def test_tras_reanudar_sigue_vigilando_hasta_la_lectura_siguiente(env):
    """La lectura guardada es de cuando estaba en pausa (sin cargar); la reanudación cuenta."""
    control, casa, ordenes, _ = env
    correr(control, casa, 0, 1, kw=1.7)
    lectura(ts=t(5), charging=0, charge_power_kw=0, charge_remaining_min=0)   # lectura en pausa
    correr(control, casa, 1, 12, kw=0.3)
    assert ordenes == ["stop", "start"]
    correr(control, casa, 12, 14, kw=1.7)
    assert ordenes == ["stop", "start", "stop"]


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
