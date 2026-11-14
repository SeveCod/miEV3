"""Hallazgos de la auditoría del 2026-11-14: cada test reproduce un fallo real antes del arreglo."""
import json
import pytest

AUTH = {"Authorization": "Basic YWRtaW46dGVzdC1wYXNz"}  # admin:test-pass
TOKEN = "t" * 24


@pytest.fixture
def client(tmp_path, monkeypatch):
    import config, db
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(config, "DASH_USER", "admin")
    monkeypatch.setattr(config, "DASH_PASS", "test-pass")
    monkeypatch.setattr(config, "INGEST_TOKEN", TOKEN)
    db.init()
    import api
    from fastapi.testclient import TestClient
    return TestClient(api.app, headers=AUTH, raise_server_exceptions=False)


# --- Credenciales de ejemplo y endpoint de ingesta (sin contraseña del panel)

@pytest.mark.parametrize("pw", ["cambia_esto", "corta", "", "contraseña-larga"])
def test_contrasena_de_ejemplo_o_corta_no_entra(client, monkeypatch, pw):
    import base64, config
    monkeypatch.setattr(config, "DASH_PASS", pw)
    hdr = {"Authorization": "Basic " + base64.b64encode(f"admin:{pw}".encode()).decode()}
    assert client.get("/api/status", headers=hdr).status_code == 401


def test_contrasena_no_ascii_se_rechaza_al_validar():
    """HTTP Basic de FastAPI solo decodifica ASCII: una DASH_PASS con ñ dejaría fuera al usuario
    sin explicación; se rechaza al arrancar con un mensaje en el log."""
    import config
    assert not config.secret_ok("contraseña-larga", 8) and config.secret_ok("password-larga", 8)


@pytest.mark.parametrize("tok", ["pon_un_token_largo_aleatorio", "corto"])
def test_token_de_ejemplo_o_corto_no_vale(client, monkeypatch, tok):
    import config
    monkeypatch.setattr(config, "INGEST_TOKEN", tok)
    assert client.post("/ingest/v2c", headers={"X-Token": tok}, json={}).status_code == 403


def test_token_no_ascii_403(client):
    r = client.post("/ingest/v2c", headers={"X-Token": "tökén".encode()}, json={})
    assert r.status_code == 403


@pytest.mark.parametrize("body", [{"ts": "basura"}, {"ts": "2026-11-14T10:00:00"}, [1, 2],
                                  {"ChargePower": "mucho"}, {"ChargePower": "nan"}])
def test_ingesta_rechaza_lo_ilegible_y_la_app_sigue_arrancando(client, body):
    import db
    r = client.post("/ingest/v2c", headers={"X-Token": TOKEN}, content=json.dumps(body))
    assert r.status_code == 422
    db.init()   # antes: ValueError al migrar un ts ilegible → ni la API ni el colector arrancaban


def test_ingesta_valida_guarda_en_utc(client):
    import db
    r = client.post("/ingest/v2c", headers={"X-Token": TOKEN},
                    json={"ts": "2026-11-14T10:00:00+01:00", "ChargePower": 7200})
    assert r.status_code == 200
    with db.conn() as c:
        assert c.execute("SELECT ts, charge_power_w FROM v2c_readings").fetchone()[:] == \
            ("2026-11-14T09:00:00+00:00", 7200.0)


def test_init_tolera_marcas_ilegibles_ya_guardadas(client):
    import db
    with db.conn() as c:
        c.execute("INSERT INTO v2c_readings (ts) VALUES ('basura')")
    db.init()


# --- Datos incompletos de Kia: NULL o 0 no deben tumbar el panel

def test_datos_nulos_o_cero_no_dan_500(client):
    import db
    with db.conn() as c:
        c.execute("INSERT INTO kia_snapshots (ts,soc,charging,odometer,total_consumed_wh) "
                  "VALUES ('2026-10-01T10:00:00+00:00',80,0,1000,100000)")
        c.execute("INSERT INTO kia_snapshots (ts,soc,charging,odometer,total_consumed_wh) "
                  "VALUES ('2026-10-02T10:00:00+00:00',NULL,0,1100,115000)")
        c.execute("INSERT INTO daily_stats VALUES ('20261001',NULL,0,0,0,0,0,40)")
        c.execute("INSERT INTO daily_stats VALUES ('20261002',0,0,0,0,0,0,30)")
        c.execute("INSERT INTO trips VALUES ('20261001','080000',40,30,1,60,90)")
    for u in ["/api/status", "/api/efficiency", "/api/history?group=month", "/api/history?group=day",
              "/api/report?period=all", "/api/report?period=month&key=2026-10", "/api/insights"]:
        assert client.get(u).status_code == 200, u
    assert client.get("/api/efficiency").json()["last_day"] is None   # ningún día con consumo


@pytest.mark.parametrize("u", ["/api/report?period=month&key=9999-12", "/api/snapshots?days=999999999",
                               "/api/trips?days=0"])
def test_parametros_desbordados_422(client, u):
    assert client.get(u).status_code == 422


def test_status_sin_soc_inicial(client):
    import db
    with db.conn() as c:
        c.execute("INSERT INTO kia_snapshots (ts,soc,charging) VALUES ('2026-10-01T10:00:00+00:00',50,1)")
    db.set_state("session_start_ts", "2026-10-01T09:00:00+00:00")
    db.set_state("session_start_soc", "")
    assert client.get("/api/status").json()["charging_since"]["soc"] is None


# --- .env mal escrito: arranca igual, con aviso y valor razonable

@pytest.mark.parametrize("raw,expected", [("0,15", 0.15), ("abc", 0.15), ("nan", 0.15), ("inf", 0.15),
                                          (" 0.2 ", 0.2)])
def test_numeros_de_env(monkeypatch, raw, expected):
    import config
    monkeypatch.setenv("X_TEST", raw)
    assert config._f("X_TEST", "0.15") == pytest.approx(expected)


def test_entero_con_decimales_en_env(monkeypatch):
    import config
    monkeypatch.setenv("X_TEST", "30.0")
    assert config._f("X_TEST", "30", int) == 30


def test_casa_ilegible_en_env_se_ignora(client, monkeypatch):
    import config, settings
    monkeypatch.setattr(config, "HOME_LAT", "abc")
    monkeypatch.setattr(config, "CAR_NAME", "")
    settings.load()
    assert config.HOME_LAT == "" and config.CAR_NAME == "Mi EV3"


# --- Cierre de carga: una sola fila aunque falle el aviso o la casa esté mal

def _carga_pendiente():
    import db
    for k, v in {"session_start_ts": "2026-10-10T20:00:00+00:00", "session_pause_ts": "2026-10-10T22:00:00+00:00",
                 "session_start_soc": 50, "session_end_soc": 70, "session_lat": "40.0", "session_lon": "-3.0"}.items():
        db.set_state(k, v)


def _filas():
    import db
    with db.conn() as c:
        return c.execute("SELECT COUNT(*) FROM charge_sessions").fetchone()[0]


def test_casa_mal_configurada_no_duplica_cargas(client, monkeypatch):
    import config, sessions
    monkeypatch.setattr(config, "HOME_LAT", "abc"); monkeypatch.setattr(config, "HOME_LON", "-3")
    avisos = []
    monkeypatch.setattr(sessions, "notify", lambda t, *a, **k: avisos.append(t))
    _carga_pendiente()
    sessions._cerrar_sesion()
    assert _filas() == 1 and "40.0000, -3.0000" in avisos[0]


def test_aviso_fallido_no_duplica_la_carga(client, monkeypatch):
    import db, sessions
    def boom(*a, **k):
        raise RuntimeError("Telegram caído")
    monkeypatch.setattr(sessions, "notify", boom)
    _carga_pendiente()
    with pytest.raises(RuntimeError):
        sessions._cerrar_sesion()
    assert _filas() == 1 and db.get_state("session_start_ts") == ""
    # La vuelta siguiente del colector ya no encuentra nada que cerrar
    sessions.process({"ts": "2026-10-11T00:00:00+00:00", "soc": 70, "charging": 0, "plugged": 0, "range_km": 300})
    assert _filas() == 1


# --- Colector: un fallo nuestro al procesar no es «coche sin datos»

def test_fallo_al_procesar_no_cuenta_como_coche_caido(client, monkeypatch):
    import main
    salud = []
    monkeypatch.setattr(main, "kia_health", lambda ok, err=None: salud.append(ok))
    monkeypatch.setattr(main.kia, "read", lambda: object())
    monkeypatch.setattr(main.kia, "snapshot", lambda v: {"ts": "2026-10-11T00:00:00+00:00", "soc": 70})
    monkeypatch.setattr(main, "save_snapshot", lambda s: (_ for _ in ()).throw(RuntimeError("BD bloqueada")))
    main.kia_tick()
    assert salud == [True]
    monkeypatch.setattr(main.kia, "read", lambda: (_ for _ in ()).throw(ConnectionError("Kia caído")))
    main.kia_tick()
    assert salud == [True, False]
