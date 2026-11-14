"""Sesiones de carga: tramos con pausas < CHARGE_MERGE_GAP_MIN son una sola carga
y un solo aviso al terminar; al empezar no se avisa."""
import pytest


@pytest.fixture
def env(tmp_path, monkeypatch):
    import config, db, sessions
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(config, "CHARGE_MERGE_GAP_MIN", 90.0)
    monkeypatch.setattr(config, "BATTERY_KWH", 58.3)
    monkeypatch.setattr(config, "CHARGE_EFFICIENCY", 0.80)
    monkeypatch.setattr(config, "PRICE_EUR_KWH", 0.121)
    monkeypatch.setattr(config, "HOME_LAT", "40.000")
    monkeypatch.setattr(config, "HOME_LON", "-3.700")
    db.init()
    avisos = []
    monkeypatch.setattr(sessions, "notify", lambda text, urgency="TODAY", event_id=None:
                        avisos.append((text, urgency, event_id)) or True)
    return sessions, db, avisos


def snap(ts, soc, charging, plugged=True, lat=40.0001, lon=-3.7002):
    return {"ts": ts, "soc": soc, "charging": charging, "plugged": plugged,
            "range_km": 200, "lat": lat, "lon": lon}


def _secuencia(sessions, tramos):
    for t in tramos:
        sessions.process(snap(*t))


def test_noche_del_21_10_es_una_sola_carga(env):
    """Real: 18:18–20:19 (61→68 %) y 21:19–03:19 (68→90 %), pausa de 60 min."""
    sessions, db, avisos = env
    _secuencia(sessions, [
        ("2026-10-21T18:18:53+00:00", 61, True),
        ("2026-10-21T20:19:02+00:00", 68, False),   # pausa
        ("2026-10-21T21:19:06+00:00", 68, True),    # se reanuda a los 60 min
        ("2026-10-22T03:19:28+00:00", 90, False),
        ("2026-10-22T03:49:30+00:00", 90, False),   # 30 min: aún en pausa
    ])
    assert avisos == []  # nada al empezar ni durante la pausa
    sessions.process(snap("2026-10-22T04:50:00+00:00", 90, False))  # 91 min → se cierra
    with db.conn() as c:
        filas = c.execute("SELECT start_ts,end_ts,soc_start,soc_end FROM charge_sessions").fetchall()
    assert [tuple(f) for f in filas] == [("2026-10-21T18:18:53+00:00", "2026-10-22T03:19:28+00:00", 61.0, 90.0)]
    assert len(avisos) == 1
    texto, urgencia, event_id = avisos[0]
    assert urgencia == "TODAY" and event_id == "miev3:carga:2026-10-21T18:18:53+00:00"
    assert "61% → 90%" in texto and "~21.1 kWh" in texto and "~2.56 €" in texto
    assert "0.121 €/kWh" in texto and "🏠 Casa" in texto and "20:18–05:19" in texto


def test_pausa_larga_son_dos_cargas(env):
    sessions, db, avisos = env
    _secuencia(sessions, [
        ("2026-10-20T08:00:00+00:00", 30, True),
        ("2026-10-20T09:00:00+00:00", 40, False),
        ("2026-10-20T11:00:00+00:00", 40, True),    # 120 min después: otra carga
        ("2026-10-20T12:00:00+00:00", 50, False),
        ("2026-10-20T14:00:00+00:00", 50, False),
    ])
    with db.conn() as c:
        assert c.execute("SELECT COUNT(*) FROM charge_sessions").fetchone()[0] == 2
    assert [a[2] for a in avisos] == ["miev3:carga:2026-10-20T08:00:00+00:00",
                                      "miev3:carga:2026-10-20T11:00:00+00:00"]


def test_carga_fuera_de_casa_da_coordenadas(env):
    sessions, _, avisos = env
    _secuencia(sessions, [
        ("2026-10-18T14:13:27+00:00", 25, True, True, 41.5000, -5.5000),
        ("2026-10-18T22:43:59+00:00", 58, False, True, 41.5000, -5.5000),
        ("2026-10-19T00:30:00+00:00", 58, False, False, 41.5000, -5.5000),
    ])
    assert "📍 41.5000, -5.5000" in avisos[0][0]


def test_estado_desconocido_no_cierra(env):
    sessions, db, avisos = env
    _secuencia(sessions, [
        ("2026-10-20T08:00:00+00:00", 30, True),
        ("2026-10-20T08:30:00+00:00", 35, None),
        ("2026-10-20T12:00:00+00:00", 60, None),
    ])
    assert avisos == [] and db.get_state("charging") == "1"


def test_bateria_baja_today_una_vez(env):
    sessions, _, avisos = env
    _secuencia(sessions, [
        ("2026-10-20T08:00:00+00:00", 18, False, False),
        ("2026-10-20T08:30:00+00:00", 17, False, False),
    ])
    assert len(avisos) == 1 and avisos[0][1] == "TODAY" and "🪫" in avisos[0][0]
