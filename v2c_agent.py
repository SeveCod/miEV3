"""Agente para la RED DE CASA (Raspberry, Mac o similar en la misma red que el Trydan).
Lee la API local del Trydan y la envía al VPS. El VPS no puede ver el cargador directamente.
⚠️ Verificar en tu firmware: endpoint /RealTimeData y nombres de campos."""
import os, time, logging, requests
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()
TRYDAN_IP = os.getenv("TRYDAN_IP", "192.168.1.50")
VPS_URL = os.getenv("VPS_INGEST_URL", "https://tu-vps/ingest/v2c")
TOKEN = os.getenv("INGEST_TOKEN", "")
EVERY_S = int(os.getenv("V2C_INTERVAL_S", "60"))

logging.basicConfig(level=logging.INFO)
while True:
    try:
        d = requests.get(f"http://{TRYDAN_IP}/RealTimeData", timeout=5).json()
        d["ts"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        requests.post(VPS_URL, json=d, headers={"X-Token": TOKEN}, timeout=10)
    except Exception as e:
        logging.warning("V2C: %s", e)
    time.sleep(EVERY_S)
