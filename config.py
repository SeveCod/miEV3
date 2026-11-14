"""Configuración: valores de .env (o por defecto). Los ajustes de negocio pueden cambiarse
después desde el panel (⚙ Ajustes): settings.load() los superpone a estos atributos."""
import os
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

load_dotenv()

def _f(k, d): return float(os.getenv(k, d))

# --- Cuenta del coche (solo .env: son secretos o hacen falta antes del login) ---
KIA_USER = os.getenv("KIA_USER", "")
KIA_PASSWORD = os.getenv("KIA_PASSWORD", "")
KIA_PIN = os.getenv("KIA_PIN", "")
# hyundai_kia_connect_api: región 1 = Europa, 2 = Canadá, 3 = EE. UU.…; marca 1 = Kia, 2 = Hyundai, 3 = Genesis.
# Solo se ha probado con Kia en Europa (región 1, marca 1).
CAR_REGION = int(os.getenv("CAR_REGION", "1"))
CAR_BRAND = int(os.getenv("CAR_BRAND", "1"))

# --- Ajustes editables desde el panel (ver settings.FIELDS) ---
CAR_NAME = os.getenv("CAR_NAME", "Mi EV3")
KIA_INTERVAL_MIN = int(os.getenv("KIA_INTERVAL_MIN", "30"))
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

# --- Panel web ---
DASH_USER = os.getenv("DASH_USER", "admin")
DASH_PASS = os.getenv("DASH_PASS", "")
API_PORT = int(os.getenv("API_PORT", "8090"))
INGEST_TOKEN = os.getenv("INGEST_TOKEN", "")

DB_PATH = os.getenv("DB_PATH", "miev3.db")
# Las marcas de tiempo se guardan en UTC; esta zona solo sirve para agrupar por día/mes local
LOCAL_TZ = ZoneInfo(os.getenv("TZ", "Europe/Madrid"))
