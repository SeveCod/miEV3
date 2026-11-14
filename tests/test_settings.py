"""Ajustes del panel: validación, superposición a config, efecto en costes y en otro proceso."""
import pytest


@pytest.fixture
def client(tmp_path, monkeypatch):
    import config, db, settings
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(config, "DASH_USER", "admin")
    monkeypatch.setattr(config, "DASH_PASS", "test-pass")
    # Restaura al final todo lo que los ajustes puedan tocar en config
    for f in settings.FIELDS:
        monkeypatch.setattr(config, f.key, getattr(config, f.key))
    db.init()
    with db.conn() as c:
        c.execute("INSERT INTO daily_stats VALUES ('20261010',15000,0,0,0,0,0,100)")
    import api
    from fastapi.testclient import TestClient
    return TestClient(api.app, headers={"Authorization": "Basic YWRtaW46dGVzdC1wYXNz"})  # admin:test-pass


def test_lista_todos_los_ajustes(client):
    import settings
    got = {s["key"]: s for s in client.get("/api/settings").json()}
    assert set(got) == set(settings.BY_KEY)
    assert got["BATTERY_KWH"]["unit"] == "kWh" and got["BATTERY_KWH"]["group"] == "Coche"


def test_guardar_cambia_config_y_costes(client):
    import config
    r = client.put("/api/settings", json={"CAR_NAME": "  Rayo  ", "PRICE_EUR_KWH": "0,20",
                                          "CHARGE_EFFICIENCY": 1, "FUEL_L_100": 6,
                                          "FUEL_PRICE_EUR_L": 1.5, "KIA_INTERVAL_MIN": "45"})
    assert r.status_code == 200
    assert config.CAR_NAME == "Rayo" and config.PRICE_EUR_KWH == 0.2 and config.KIA_INTERVAL_MIN == 45
    e = client.get("/api/efficiency").json()
    # 15 kWh/100 × 0,20 €/kWh = 3 €/100 km; combustión 6 L × 1,5 € = 9 €/100 km → ahorro 6 € en 100 km
    assert e["d30"]["eur_100"] == pytest.approx(3.0)
    assert e["fuel"]["eur_100"] == pytest.approx(9.0)
    assert e["d30"]["saving_eur"] == pytest.approx(6.0)


@pytest.mark.parametrize("data", [
    {"CHARGE_EFFICIENCY": 0},        # dividiría por cero en el coste
    {"BATTERY_KWH": -1},
    {"PRICE_EUR_KWH": "gratis"},
    {"KIA_INTERVAL_MIN": 5},         # agotaría el cupo de la API
    {"CAR_NAME": ""},
    {"HOME_LAT": 91},
    {"PRICE_EUR_KWH": "nan"},
    {"FUEL_TYPE": "gasoil"},         # solo las opciones de la lista
    {"CAR_COLOR": "#ff0000"},
    {"DASH_PASS": "y"},              # los secretos no se editan desde la web
])
def test_valores_no_validos_422_y_no_guarda_nada(client, data):
    import config
    before = config.PRICE_EUR_KWH
    r = client.put("/api/settings", json={"PRICE_EUR_KWH": 0.3, **data} if "PRICE_EUR_KWH" not in data else data)
    assert r.status_code == 422
    assert config.PRICE_EUR_KWH == before  # todo o nada


def test_casa_vacia_es_valida(client):
    import config
    assert client.put("/api/settings", json={"HOME_LAT": "", "HOME_LON": ""}).status_code == 200
    assert config.HOME_LAT == "" and config.HOME_LON == ""


def test_el_colector_ve_el_cambio_al_recargar(client, monkeypatch):
    """Otro proceso (el colector) parte de .env y recoge el ajuste con settings.load()."""
    import config, settings, sessions
    client.put("/api/settings", json={"PRICE_EUR_KWH": 0.25, "CHARGE_EFFICIENCY": 0.5, "BATTERY_KWH": 50})
    monkeypatch.setattr(config, "PRICE_EUR_KWH", 0.1)  # como si el proceso acabara de arrancar
    settings.load()
    assert config.PRICE_EUR_KWH == 0.25
    # Y una carga cerrada usa el precio nuevo: 20 % de 50 kWh / 0,5 = 20 kWh × 0,25 = 5 €
    import db
    monkeypatch.setattr(sessions, "notify", lambda *a, **k: True)
    for k, v in {"session_start_ts": "2026-10-10T20:00:00+00:00", "session_pause_ts": "2026-10-10T22:00:00+00:00",
                 "session_start_soc": 50, "session_end_soc": 70}.items():
        db.set_state(k, v)
    sessions._cerrar_sesion()
    with db.conn() as c:
        kwh, cost = c.execute("SELECT kwh_est, cost_eur FROM charge_sessions").fetchone()
    assert kwh == pytest.approx(20) and cost == pytest.approx(5)


def test_sin_credenciales_401(client):
    from fastapi.testclient import TestClient
    import api
    assert TestClient(api.app).put("/api/settings", json={"CAR_NAME": "x"}).status_code == 401


def test_color_y_combustible(client):
    import config
    r = client.put("/api/settings", json={"CAR_COLOR": "terracota", "FUEL_TYPE": "gasolina"})
    assert r.status_code == 200
    got = {f["key"]: f for f in r.json()}
    assert got["CAR_COLOR"]["value"] == "terracota" and config.FUEL_TYPE == "gasolina"
    assert got["CAR_COLOR"]["options"]["terracota"]["accent"].startswith("#")
    assert set(got["FUEL_TYPE"]["options"]) == {"diesel", "gasolina"}


def test_valores_malos_en_env_se_corrigen(client, monkeypatch):
    import config, settings
    monkeypatch.setattr(config, "FUEL_TYPE", "gasoil")
    monkeypatch.setattr(config, "KIA_INTERVAL_MIN", 5)        # agotaría el cupo de la API
    monkeypatch.setattr(config, "CHARGE_EFFICIENCY", 0.0)     # dividiría por cero
    settings.load()
    assert config.FUEL_TYPE == "diesel"
    assert config.KIA_INTERVAL_MIN == 30 and config.CHARGE_EFFICIENCY == 0.5
    assert client.get("/api/efficiency").status_code == 200


def test_colores_legibles():
    """Texto blanco sobre el color de acento (modo claro) y acento sobre el fondo oscuro: ≥ 4,5:1."""
    import settings
    def lum(h):
        c = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
        return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
    ratio = lambda a, b: (max(lum(a), lum(b)) + .05) / (min(lum(a), lum(b)) + .05)
    for k, c in settings.CAR_COLORS.items():
        assert ratio(c["accent"], "#FFFFFF") >= 4.5, k
        assert ratio(c["accent_dark"], "#0F1A1D") >= 4.5, k
