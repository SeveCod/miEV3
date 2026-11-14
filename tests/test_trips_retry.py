"""Viajes del día: como mucho TRIPS_MAX_TRIES intentos a partir de las 23:45, separados 5 min.
Caso real: con el login caído se reintentaba cada minuto (15 logins fallidos por noche)."""
from datetime import datetime

import pytest


@pytest.fixture
def env(tmp_path, monkeypatch):
    import config, db, main
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    db.init()
    llamadas = []
    return main, db, llamadas, monkeypatch


def _noche(main, dia="2026-10-24"):
    for m in range(40, 60):
        main.trips_tick(datetime.fromisoformat(f"{dia}T23:{m:02d}:00"))


def test_login_caido_tres_intentos_espaciados(env):
    main, db, llamadas, mp = env
    def falla(day):
        llamadas.append(day)
        raise RuntimeError("Authentication failed — returned to login page.")
    mp.setattr(main, "save_trips", falla)
    _noche(main)
    assert len(llamadas) == 3


def test_intenta_en_los_minutos_45_50_55(env):
    main, db, llamadas, mp = env
    minutos = []
    def falla(day):
        raise RuntimeError("x")
    mp.setattr(main, "save_trips", falla)
    for m in range(40, 60):
        now = datetime.fromisoformat(f"2026-10-24T23:{m:02d}:00")
        antes = db.get_state("trips_try")
        main.trips_tick(now)
        if db.get_state("trips_try") != antes:
            minutos.append(m)
    assert minutos == [45, 50, 55]


def test_exito_a_la_primera_no_repite(env):
    main, db, llamadas, mp = env
    mp.setattr(main, "save_trips", lambda day: llamadas.append(day))
    _noche(main)
    assert llamadas == ["20261024"] and db.get_state("trips_day") == "20261024"


def test_el_dia_siguiente_vuelve_a_tener_intentos(env):
    main, db, llamadas, mp = env
    def falla(day):
        llamadas.append(day)
        raise RuntimeError("x")
    mp.setattr(main, "save_trips", falla)
    _noche(main, "2026-10-24")
    _noche(main, "2026-10-25")
    assert llamadas == ["20261024"] * 3 + ["20261025"] * 3
