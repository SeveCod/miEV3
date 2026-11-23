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


def snap(ts, soc, charging, plugged=True, lat=40.0001, lon=-3.7002, **extra):
    return {"ts": ts, "soc": soc, "charging": charging, "plugged": plugged,
            "range_km": 200, "lat": lat, "lon": lon, **extra}


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


# --- Motivo de fin de carga -------------------------------------------------------------------

def _carga(sessions, fin_soc, fin_plugged, kw=4.4, faltan=400, limite_ac=80, limite_dc=90,
           cierre_cargando=False):
    """Carga 35 % → fin_soc y cierre 91 min después (o con otra carga que empieza)."""
    lim = {"charge_limit_ac": limite_ac, "charge_limit_dc": limite_dc}
    sessions.process(snap("2026-11-21T18:16:51+00:00", 35, True, charge_power_kw=kw,
                          charge_remaining_min=faltan, **lim))
    sessions.process(snap("2026-11-21T19:04:01+00:00", fin_soc, False, plugged=fin_plugged,
                          charge_power_kw=0.0, charge_remaining_min=0.0, **lim))
    sessions.process(snap("2026-11-21T20:35:00+00:00", fin_soc, cierre_cargando,
                          charge_power_kw=4.4 if cierre_cargando else 0.0, **lim))


def _motivo(db):
    with db.conn() as c:
        return c.execute("SELECT end_reason FROM charge_sessions").fetchone()[0]


def test_real_21_11_parada_enchufado_por_debajo_del_limite(env):
    """Real (sesión 27): 35 → 37,5 % con el coche enchufado, límite 80 %, faltaban 400 min
    (saltó el limitador). Debe decir que se paró enchufado, sin culpar a nadie."""
    sessions, db, avisos = env
    _carga(sessions, 37.5, True)
    assert _motivo(db) == "stopped"
    texto = avisos[0][0]
    assert "⚠️ Se paró enchufado en 38% de 80%, faltaban ~400 min" in texto
    assert "¿corte de luz, el cargador o el horario de carga?" in texto


def test_real_22_11_llega_al_limite(env):
    """Real (sesión 28): acaba en 80 % con límite 80 %."""
    sessions, db, avisos = env
    _carga(sessions, 80, True, faltan=6)
    assert _motivo(db) == "limit"
    assert "🎯 Llegó al límite del coche (80%)" in avisos[0][0]
    assert "Se paró" not in avisos[0][0]


def test_desenchufado_antes_del_limite(env):
    sessions, db, avisos = env
    _carga(sessions, 60, False)
    assert _motivo(db) == "unplugged"
    assert "🔌 Desenchufado en 60% de 80%" in avisos[0][0]


def test_al_limite_y_desenchufado_cuenta_como_limite(env):
    sessions, db, _ = env
    _carga(sessions, 79.5, False)
    assert _motivo(db) == "limit"


def test_carga_rapida_usa_el_limite_de_dc(env):
    sessions, db, _ = env
    _carga(sessions, 90, True, kw=95.0)   # 90 % < 80-1 no; con DC (90 %) sí es el límite
    assert _motivo(db) == "limit"


def test_cierre_porque_empieza_otra_carga_usa_datos_de_la_pausa(env):
    """Si la sesión se cierra cuando ya empieza otra (enchufado y cargando), el motivo sale de
    la lectura en la que paró, no de la actual."""
    sessions, db, avisos = env
    _carga(sessions, 60, False, cierre_cargando=True)
    assert _motivo(db) == "unplugged"
    assert len(avisos) == 1


def test_sin_datos_motivo_desconocido(env):
    sessions, db, avisos = env
    _carga(sessions, 50, None, limite_ac=None, limite_dc=None)
    assert _motivo(db) is None
    for frase in ("🎯", "🔌", "Se paró"):
        assert frase not in avisos[0][0]


def test_motivo_fin_tabla():
    import sessions
    assert sessions.motivo_fin(80, 1, 80) == "limit"
    assert sessions.motivo_fin(50, "1", "80") == "stopped"
    assert sessions.motivo_fin(50, "0", "80") == "unplugged"
    assert sessions.motivo_fin(50, "", "80") is None
    assert sessions.motivo_fin(50, "1", "") is None
    assert sessions.motivo_fin(50, "0", "") == "unplugged"
