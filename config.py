"""Configuración: valores de .env (o por defecto). Los ajustes de negocio pueden cambiarse
después desde el panel (⚙ Ajustes): settings.load() los superpone a estos atributos."""
import logging, math, os, time
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

load_dotenv(interpolate=False)   # una contraseña con ${...} se lee tal cual
# TZ del .env manda en todo el proceso (horarios del colector y días locales); así no hay que
# repetirla en ecosystem.config.js
if os.getenv("TZ") and hasattr(time, "tzset"):
    time.tzset()

def _f(k, d, cast=float):
    """Número de .env. Acepta coma decimal; si es ilegible o no finito, avisa y usa el defecto
    (los rangos los comprueba después settings.load())."""
    raw = os.getenv(k, d)
    try:
        n = float(str(raw).strip().replace(",", "."))
        if not math.isfinite(n):
            raise ValueError
        return cast(n)
    except ValueError:
        logging.warning("%s=%r no es un número válido en .env; se usa %s", k, raw, d)
        return cast(float(d))

# Valores de ejemplo publicados en .env.example: nunca valen como contraseña ni como token
PLACEHOLDER_SECRETS = {"cambia_esto", "pon_un_token_largo_aleatorio"}
DASH_PASS_MIN, INGEST_TOKEN_MIN = 8, 16

def secret_ok(value: str, min_len: int) -> bool:
    """Válido si es largo, no es el de ejemplo y es ASCII (HTTP Basic no admite ñ ni tildes)."""
    return len(value) >= min_len and value.isascii() and value not in PLACEHOLDER_SECRETS

# --- Cuenta del coche (solo .env: son secretos o hacen falta antes del login) ---
KIA_USER = os.getenv("KIA_USER", "")
KIA_PASSWORD = os.getenv("KIA_PASSWORD", "")
KIA_PIN = os.getenv("KIA_PIN", "")
# hyundai_kia_connect_api: región 1 = Europa, 2 = Canadá, 3 = EE. UU.…; marca 1 = Kia, 2 = Hyundai, 3 = Genesis.
# Solo se ha probado con Kia en Europa (región 1, marca 1).
CAR_REGION = _f("CAR_REGION", "1", int)
CAR_BRAND = _f("CAR_BRAND", "1", int)
# Cuenta con varios coches: VIN del que se lee (vacío = el primero, con aviso si hay más)
CAR_VIN = os.getenv("CAR_VIN", "").strip()

# --- Ajustes editables desde el panel (ver settings.FIELDS) ---
CAR_NAME = os.getenv("CAR_NAME", "Mi EV3")
KIA_INTERVAL_MIN = _f("KIA_INTERVAL_MIN", "30", int)
# Aviso de coche sin datos: tras estos minutos sin leerlo, y repetido cada KIA_DOWN_REPEAT_H
KIA_DOWN_ALERT_MIN = _f("KIA_DOWN_ALERT_MIN", "120")
KIA_DOWN_REPEAT_H = _f("KIA_DOWN_REPEAT_H", "12")

BATTERY_KWH = _f("BATTERY_KWH", "58.3")
CHARGE_EFFICIENCY = _f("CHARGE_EFFICIENCY", "0.90")
PRICE_EUR_KWH = _f("PRICE_EUR_KWH", "0.15")
LOW_SOC_ALERT = _f("LOW_SOC_ALERT", "20")
# Consumo homologado del coche (kWh/100 km): referencia de los relojes del panel
WLTP_KWH_100 = _f("WLTP_KWH_100", "15.0")
# Comparación con un coche de combustión: tipo de combustible, su consumo y el precio del litro
FUEL_TYPE = os.getenv("FUEL_TYPE", "diesel")          # "diesel" | "gasolina" (settings.FUELS)
FUEL_L_100 = _f("FUEL_L_100", "5.5")
FUEL_PRICE_EUR_L = _f("FUEL_PRICE_EUR_L", "1.45")
# Color de la carrocería: tiñe el panel (settings.CAR_COLORS); "neutro" = sin color de coche
CAR_COLOR = os.getenv("CAR_COLOR", "neutro")
# Tramos de carga separados por menos de esto son la misma sesión (pausas del cargador)
CHARGE_MERGE_GAP_MIN = _f("CHARGE_MERGE_GAP_MIN", "90")
# Punto de carga «Casa»: centro y radio. Sin HOME_LAT/HOME_LON se muestran coordenadas.
HOME_LAT = os.getenv("HOME_LAT", "")
HOME_LON = os.getenv("HOME_LON", "")
HOME_RADIUS_M = _f("HOME_RADIUS_M", "500")

# --- Avisos: Telegram directo o, si no, un webhook (POST JSON) ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
NOTIFY_URL = os.getenv("NOTIFY_URL", "")
NOTIFY_SECRET = os.getenv("NOTIFY_SECRET", "")
NOTIFY_DRY_RUN = os.getenv("NOTIFY_DRY_RUN", "") == "1"

# --- Control de carga por consumo de la casa (opcional, experimental; ver docs/control-carga.md) ---
# Home Assistant con un sensor de potencia de la casa SIN el cargador. Vacío = desactivado.
HA_URL = os.getenv("HA_URL", "").strip().rstrip("/")
HA_TOKEN = os.getenv("HA_TOKEN", "")
HA_POWER_ENTITY = os.getenv("HA_POWER_ENTITY", "").strip()
LOAD_HIGH_KW = _f("LOAD_HIGH_KW", "1.0")          # casa ≥ esto → pausa la carga
LOAD_LOW_KW = _f("LOAD_LOW_KW", "0.6")            # casa < esto durante LOAD_LOW_MIN → reanuda
LOAD_LOW_MIN = _f("LOAD_LOW_MIN", "10")
LOAD_PAUSE_MAX_MIN = _f("LOAD_PAUSE_MAX_MIN", "90")  # pausa máxima: luego reanuda aunque la casa siga alta
LOAD_MAX_PAUSES = _f("LOAD_MAX_PAUSES", "4", int)    # pausas como mucho por carga
LOAD_CHARGER_MAX_KW = _f("LOAD_CHARGER_MAX_KW", "7.4")  # potencia del cargador de casa: más = otro cargador

# --- Panel web ---
DASH_USER = os.getenv("DASH_USER", "admin")
DASH_PASS = os.getenv("DASH_PASS", "")
API_PORT = _f("API_PORT", "8090", int)
INGEST_TOKEN = os.getenv("INGEST_TOKEN", "")

DB_PATH = os.getenv("DB_PATH", "miev3.db")
# Las marcas de tiempo se guardan en UTC; esta zona solo sirve para agrupar por día/mes local
LOCAL_TZ = ZoneInfo(os.getenv("TZ", "Europe/Madrid"))
