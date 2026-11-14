"""Control de carga por consumo de la casa (control.py) con un coche simulado.
Caso real (EV3, 2026-11): coche a 5,7 kW + termo 1,7 kW → el limitador de 6 kW saltó tres veces."""
from types import SimpleNamespace
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
    monkeypatch.setattr(config, "HOME_LAT", str(CASA[0]))
    monkeypatch.setattr(config, "HOME_LON", str(CASA[1]))
    monkeypatch.setattr(config, "LOAD_PAUSE_MAX_MIN", 90.0)
    monkeypatch.setattr(config, "LOAD_REQ_MAX_AGE_MIN", 10.0)
    monkeypatch.setattr(config, "LOAD_MAX_PAUSES", 4)
    db.init()
    car = SimpleNamespace(location_latitude=CASA[0], location_longitude=CASA[1],
                          ev_battery_is_plugged_in=True, ev_battery_is_charging=True,
                          ev_battery_percentage=50, ev_charge_limits_ac=80,
                          ev_charging_power=5.7, ev_estimated_current_charge_duration=200, fail=0)
    ordenes, avisos = [], []

    def charge(start):
        if car.fail:
            car.fail -= 1
            raise RuntimeError("Kia 500")
        ordenes.append("start" if start else "stop")

    monkeypatch.setattr(control.kia, "read", lambda: car)
    monkeypatch.setattr(control.kia, "charge", charge)
    monkeypatch.setattr(control, "notify", lambda text, urgency="TODAY", event_id=None:
                        avisos.append((text, urgency)) or True)
    return control, car, ordenes, avisos


def test_pausa_y_reanuda_una_vez(env):
    control, car, ordenes, avisos = env
    control.request("pause", T0)
    control.tick(t(1))
    control.tick(t(2))                      # la misma petición no se repite
    assert ordenes == ["stop"]
    control.request("resume", t(15))
    control.tick(t(16))
    assert ordenes == ["stop", "start"]
    assert [u for _, u in avisos] == ["INFO", "INFO"] and "15 min" in avisos[1][0]


def test_no_pausa_fuera_de_casa_ni_desenchufado(env):
    control, car, ordenes, _ = env
    car.location_latitude = 40.4            # ~45 km
    control.request("pause", T0); control.tick(t(1))
    car.location_latitude, car.ev_battery_is_plugged_in = CASA[0], False
    control.request("pause", t(2)); control.tick(t(3))
    assert ordenes == []


def test_no_pausa_enchufado_sin_cargar(env):
    """Caso real: coche enchufado al 80 % sin cargar; se pausó y la reanudación «de seguridad»
    habría arrancado una carga que no tocaba."""
    control, car, ordenes, avisos = env
    car.ev_battery_is_charging, car.ev_charging_power = False, 0.0
    car.ev_estimated_current_charge_duration = 0
    control.request("pause", T0); control.tick(t(1))
    control.tick(t(95))
    assert ordenes == [] and avisos == []


def test_no_pausa_si_ya_llego_al_limite(env):
    control, car, ordenes, avisos = env
    car.ev_battery_percentage = 80
    control.request("pause", T0); control.tick(t(1))
    assert ordenes == [] and avisos == []


def test_cargando_desconocido_usa_la_potencia(env):
    """En CCS2 la librería puede dejar «cargando» en None: vale la potencia de carga."""
    control, car, ordenes, _ = env
    car.ev_battery_is_charging = None
    control.request("pause", T0); control.tick(t(1))
    assert ordenes == ["stop"]
    control.request("resume", t(5)); control.tick(t(6))
    car.ev_charging_power, car.ev_estimated_current_charge_duration = 0, 0
    control.request("pause", t(30)); control.tick(t(31))
    assert ordenes == ["stop", "start"]


def test_ubicacion_desconocida_si_pausa(env):
    control, car, ordenes, _ = env
    car.location_latitude = car.ev_battery_is_plugged_in = None
    control.request("pause", T0); control.tick(t(1))
    assert ordenes == ["stop"]


def test_no_reanuda_una_carga_que_no_pauso(env):
    control, _, ordenes, _ = env
    control.request("resume", T0); control.tick(t(1))
    assert ordenes == []


def test_peticion_caducada_se_descarta(env):
    control, _, ordenes, _ = env
    control.request("pause", T0)
    control.tick(t(11))                     # el colector estuvo parado 11 min
    assert ordenes == []


def test_reanuda_por_seguridad(env):
    control, _, ordenes, avisos = env
    control.request("pause", T0); control.tick(t(1))
    control.tick(t(90))
    assert ordenes == ["stop"]
    control.tick(t(91))                     # 90 min desde la pausa (t(1))
    assert ordenes == ["stop", "start"] and avisos[-1][1] == "TODAY"
    control.tick(t(92))
    assert ordenes == ["stop", "start"]


def test_reintenta_y_avisa_al_agotar(env):
    control, car, ordenes, avisos = env
    car.fail = 1
    control.request("pause", T0)
    control.tick(t(1)); control.tick(t(2))  # falla una vez y a la segunda pausa
    assert ordenes == ["stop"] and avisos[-1][1] == "INFO"
    car.fail = 3
    control.request("resume", t(10))
    for m in (11, 12, 13, 14):
        control.tick(t(m))
    assert ordenes == ["stop"]              # tres fallos, no se insiste más
    assert avisos[-1][1] == "TODAY" and "reanudar" in avisos[-1][0]
    import db
    assert db.get_state(control.PAUSED) == ""


def test_tope_de_pausas_por_carga_avisa_una_vez(env):
    control, _, ordenes, avisos = env
    import db
    db.set_state("session_start_ts", "2026-11-14T14:00:00+00:00")
    for i in range(6):                      # pausa a los 1+20i min, reanuda a los 10+20i
        control.request("pause", t(1 + 20 * i)); control.tick(t(2 + 20 * i))
        control.request("resume", t(10 + 20 * i)); control.tick(t(11 + 20 * i))
    assert ordenes.count("stop") == 4
    topes = [a for a in avisos if "no pauso más" in a[0]]
    assert len(topes) == 1 and topes[0][1] == "TODAY"
    db.set_state("session_start_ts", "2026-11-15T01:00:00+00:00")   # carga nueva: contador a cero
    control.request("pause", t(200)); control.tick(t(201))
    assert ordenes.count("stop") == 5


def test_umbrales_con_lecturas_reales(env):
    """Lecturas reales: el termo (1,67 kW) pausa; al bajar a 0,2 kW 10 min se reanuda.
    Una lectura entre umbrales (0,8) no decide nada."""
    control, _, ordenes, _ = env
    for m, kw in ((0, 0.21), (1, 1.67), (6, 1.75), (11, 1.65), (16, 0.35), (21, 0.8),
                  (26, 0.26), (31, 0.26), (35, 0.23), (36, 0.19)):
        control.tick(t(m), kw=kw, leer_ha=False)
        if m == 1:
            assert ordenes == ["stop"]
        if m == 31:
            assert ordenes == ["stop"]
    assert ordenes == ["stop", "start"]


def test_casa_alta_con_coche_fuera_no_lee_el_coche_cada_minuto(env, monkeypatch):
    control, car, ordenes, _ = env
    lecturas = []
    monkeypatch.setattr(control.kia, "read", lambda: lecturas.append(1) or car)
    car.location_latitude = 40.4
    for m in range(0, 30):
        control.tick(t(m), kw=1.8, leer_ha=False)
    assert ordenes == [] and len(lecturas) == 2   # t(0) y t(15)


def test_ha_sin_datos_no_decide(env):
    control, _, ordenes, _ = env
    for m in range(5):
        control.tick(t(m), kw=None, leer_ha=False)
    assert ordenes == []


def test_sin_home_assistant_esta_inactivo(env, monkeypatch):
    control, *_ = env
    import config
    monkeypatch.setattr(config, "HA_URL", "")
    assert not control.activo() and control.potencia_casa() is None


def test_aviso_de_carga_terminada_cuenta_pausas_y_limite(env, monkeypatch):
    control, car, _, _ = env
    import db, sessions
    avisos = []
    monkeypatch.setattr(sessions, "notify", lambda text, urgency="TODAY", event_id=None:
                        avisos.append(text) or True)
    ts0 = "2026-11-14T14:00:00+00:00"
    db.set_state("session_start_ts", ts0)
    with db.conn() as c:
        c.execute("INSERT INTO kia_snapshots(ts, soc, charge_limit_ac) VALUES(?,?,?)", (ts0, 50, 80))
    control.request("pause", T0); control.tick(t(1))
    control.request("resume", t(20)); control.tick(t(21))
    for k, v in (("session_start_soc", 50), ("session_pause_ts", t(200)), ("session_end_soc", 72)):
        db.set_state(k, v)
    sessions._cerrar_sesion()
    assert "1 pausa por consumo de la casa (20 min)" in avisos[0]
    assert "se quedó en 72% de 80%" in avisos[0]
