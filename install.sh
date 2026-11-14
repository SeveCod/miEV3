#!/usr/bin/env bash
# Instalador de MiEV3: entorno de Python, dependencias, .env con tus datos y prueba de conexión.
# Uso: ./install.sh   (se puede repetir: no pisa un .env que ya exista)
set -euo pipefail
umask 077   # lo que se cree aquí (.env, BD), solo legible por tu usuario
cd "$(dirname "$0")"

say()  { printf '\n\033[1m%s\033[0m\n' "$*"; }
fail() { printf '\n\033[31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

# 1. Python 3.11 o superior
PY=${PYTHON:-python3}
command -v "$PY" >/dev/null || fail "No encuentro python3. Instálalo (3.11 o superior) y repite."
"$PY" -c 'import sys; sys.exit(sys.version_info < (3, 11))' \
  || fail "Hace falta Python 3.11 o superior; tienes $("$PY" -V 2>&1)."

# 2. Entorno virtual y dependencias
say "1/3 · Instalando dependencias en ./venv…"
[ -x venv/bin/python ] || "$PY" -m venv venv
venv/bin/pip install --quiet --disable-pip-version-check -r requirements.txt
echo "✓ Dependencias instaladas"

# 3. .env: se crea desde .env.example con lo imprescindible; el resto se ajusta en el panel
say "2/3 · Configuración (.env)"
if [ -f .env ]; then
  echo "✓ Ya tienes un .env; no lo toco. Para cambiarlo: edítalo o bórralo y repite."
else
  echo "Datos de tu cuenta de la app Kia Connect. Si dejas el correo vacío, lo rellenas luego en .env."
  read -r  -p "  Correo de Kia Connect: " kia_user || true
  read -rs -p "  Contraseña de Kia Connect: " kia_pass || true; echo
  read -r  -p "  PIN de la app (4 cifras): " kia_pin || true
  read -rs -p "  Contraseña para el panel web (vacío = genero una): " dash_pass || true; echo
  # Python escribe el fichero: así las contraseñas con símbolos no se rompen
  KIA_USER_IN="${kia_user:-}" KIA_PASS_IN="${kia_pass:-}" KIA_PIN_IN="${kia_pin:-}" DASH_PASS_IN="${dash_pass:-}" \
  venv/bin/python -I - <<'PY'
import os, re, secrets
from pathlib import Path

def q(v):  # valor entre comillas dobles, con \ y " escapados (formato de python-dotenv)
    return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'

dash = os.environ["DASH_PASS_IN"]
if dash and (len(dash) < 8 or not dash.isascii()):
    print("  ⚠️ La contraseña del panel necesita 8 caracteres o más, sin ñ ni tildes: genero una.")
    dash = ""
generated = not dash
dash = dash or secrets.token_urlsafe(12)
values = {"KIA_USER": os.environ["KIA_USER_IN"], "KIA_PASSWORD": os.environ["KIA_PASS_IN"],
          "KIA_PIN": os.environ["KIA_PIN_IN"], "DASH_PASS": dash,
          "INGEST_TOKEN": secrets.token_urlsafe(24)}
text = Path(".env.example").read_text()
for k, v in values.items():
    if v:
        text = re.sub(rf"^{k}=.*$", lambda m: f"{k}={q(v)}", text, count=1, flags=re.M)
Path(".env").write_text(text)
os.chmod(".env", 0o600)
print("✓ .env creado (solo lo puede leer tu usuario)")
if generated:
    print(f"  Contraseña del panel generada: {dash}   (usuario: admin; está en .env como DASH_PASS)")
PY
fi

# 4. Prueba de conexión con el coche (lee el estado en caché; no despierta el coche)
say "3/3 · Prueba de conexión"
if grep -q '^KIA_USER=tu_email@ejemplo.com' .env; then
  echo "· Sin cuenta en .env: pon KIA_USER y KIA_PASSWORD y prueba con:"
  echo "  venv/bin/python -c \"from collectors import kia; print(kia.snapshot())\""
else
  read -r -p "¿Pruebo ahora la conexión con el coche? [S/n] " go || true
  case "${go:-s}" in
    [nN]*) echo "· Sin probar." ;;
    *) if venv/bin/python -c "from collectors import kia; s = kia.snapshot(); print(f'✓ Conectado: batería al {s[\"soc\"]} %, {s[\"odometer\"]} km')"; then :
       else echo "✗ No conecta. Mira docs/conectar-api.md (sección «Si el login falla»)."; fi ;;
  esac
fi

say "Listo. Para arrancar:"
cat <<'TXT'
  Con pm2 (se reinicia solo):   pm2 start ecosystem.config.js && pm2 save
  Sin pm2, en dos terminales:   venv/bin/python main.py      y      venv/bin/python api.py
  Panel:                        http://127.0.0.1:8090   (ver README para abrirlo desde el móvil)
  ¿Sin coche todavía?           DB_PATH=demo.db venv/bin/python scripts/seed_demo.py
TXT
