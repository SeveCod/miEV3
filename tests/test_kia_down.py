"""Aviso de Kia caído, level-triggered: a las KIA_DOWN_ALERT_MIN sin leer el coche,
repetido cada KIA_DOWN_REPEAT_H mientras siga caído, y un aviso al volver.
Caso real: login UE rechazado del 23/10 06:04 al 25/10 22:54 (UTC) sin que nadie se enterase."""
import pytest

ERR = "Authentication failed — returned to login page. Check username and password."


@pytest.fixture
def env(tmp_path, monkeypatch):
    import config, db, main
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(config, "KIA_DOWN_ALERT_MIN", 120.0)
    monkeypatch.setattr(config, "KIA_DOWN_REPEAT_H", 12.0)
    db.init()
    avisos = []
    monkeypatch.setattr(main, "notify", lambda text, urgency="TODAY", event_id=None:
                        avisos.append((text, urgency, event_id)) or True)
    return main, avisos


def _cada_30_min(main, desde_h, hasta_h, ok, dia="2026-10-23"):
    """Lecturas cada 30 min entre dos horas (fraccionarias) de un día UTC."""
    m = int(desde_h * 60)
    while m < hasta_h * 60:
        main.kia_health(ok, None if ok else ERR, now=f"{dia}T{m // 60:02d}:{m % 60:02d}:00+00:00")
        m += 30


def test_fallo_breve_no_avisa(env):
    main, avisos = env
    _cada_30_min(main, 6.5, 8.5, ok=False)      # 06:30–08:00: 90 min caído
    _cada_30_min(main, 8.5, 9, ok=True)
    assert avisos == []


def test_avisa_a_las_2_horas_una_sola_vez(env):
    main, avisos = env
    _cada_30_min(main, 6.5, 12, ok=False)       # 06:30–11:30
    assert len(avisos) == 1
    texto, urgencia, eid = avisos[0]
    assert "2 h" in texto and "returned to login page" in texto and urgencia == "TODAY"
    assert eid.startswith("evbrain:kia-caido:")


def test_repite_mientras_siga_caido_y_avisa_al_volver(env):
    """Del 23/10 06:30 al 25/10 22:30: aviso a las 2 h y después cada 12 h; al volver, uno de vuelta."""
    main, avisos = env
    _cada_30_min(main, 6.5, 24, ok=False, dia="2026-10-23")
    _cada_30_min(main, 0, 24, ok=False, dia="2026-10-24")
    _cada_30_min(main, 0, 23, ok=False, dia="2026-10-25")
    caidos = [a for a in avisos if "kia-caido" in a[2]]
    # 2 h, 14 h, 26 h, 38 h, 50 h, 62 h después del primer fallo (06:30 del 23) → 6
    assert len(caidos) == 6
    assert len({a[2] for a in caidos}) == 6     # event_id distintos: el receptor no los deduplica
    main.kia_health(True, now="2026-10-25T23:00:00+00:00")
    assert "kia-ok" in avisos[-1][2] and "vuelve" in avisos[-1][0].lower()
    main.kia_health(True, now="2026-10-25T23:30:00+00:00")
    assert len(avisos) == 7                     # el aviso de vuelta sale una sola vez


def test_tras_volver_un_fallo_nuevo_empieza_de_cero(env):
    main, avisos = env
    _cada_30_min(main, 0, 3, ok=False)          # avisa a las 02:00
    main.kia_health(True, now="2026-10-23T03:00:00+00:00")
    _cada_30_min(main, 4, 5.5, ok=False)        # 90 min: no avisa
    assert [a[2].split(":")[1] for a in avisos] == ["kia-caido", "kia-ok"]
