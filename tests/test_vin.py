"""Cuentas con varios coches: CAR_VIN elige cuál se lee."""
from types import SimpleNamespace as V
import pytest


def vm(*cars):
    return V(vehicles={str(i): c for i, c in enumerate(cars)})


EV3, EV6 = V(name="EV3", VIN="KNAAA111"), V(name="EV6", VIN="KNBBB222")


def test_sin_vin_lee_el_primero_y_avisa(monkeypatch, caplog):
    import config
    from collectors import kia
    monkeypatch.setattr(config, "CAR_VIN", "")
    assert kia._vehicle(vm(EV3, EV6)) is EV3
    assert "KNBBB222" in caplog.text


def test_con_vin_lee_ese(monkeypatch):
    import config
    from collectors import kia
    monkeypatch.setattr(config, "CAR_VIN", "knbbb222")
    assert kia._vehicle(vm(EV3, EV6)) is EV6


@pytest.mark.parametrize("vin,cars,msg", [("NOEXISTE", (EV3, EV6), "KNAAA111"), ("", (), "ningún coche")])
def test_errores_claros(monkeypatch, vin, cars, msg):
    import config
    from collectors import kia
    monkeypatch.setattr(config, "CAR_VIN", vin)
    with pytest.raises(RuntimeError, match=msg):
        kia._vehicle(vm(*cars))
