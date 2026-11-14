# Cambios

## 0.4.3-beta (2026-11-15)

- **Control de carga probado:** en un Kia EV3 de Europa el coche obedece las órdenes de parar y
  reanudar la carga. Deja de marcarse como experimental (otros modelos, sin comprobar).
- **Control de carga, arranque vigilado:** si la casa está alta, el coche en casa por debajo de
  su límite y la última lectura tiene más de 10 min, el colector lee el coche en caché (no lo
  despierta) y pausa si ya carga. Antes, una carga empezada con el termo encendido pasaba hasta
  30 min sin vigilar, y el limitador salta a los 15–20 min. Como mucho una lectura cada 15 min y
  12 al día.

## 0.4.2-beta (2026-11-15)

- **Control de carga:** a la pausa máxima ya no reanuda si la casa sigue alta (horno, estufa):
  alarga la pausa y avisa una vez. Reanuda si la casa bajó o Home Assistant no responde.
- `LOAD_MAX_PAUSES` pasa de 4 a 8 por defecto: una carga nocturna de 6–7 h puede coincidir con
  varios picos.

## 0.4.1-beta (2026-11-15)

- **Control de carga más simple y con menos llamadas:** decide con la última lectura guardada
  (sin pedir lecturas extra al coche: menos cupo de API y menos batería de 12 V) y solo pregunta
  a Home Assistant cuando el coche carga en casa por debajo de su límite o hay una pausa en curso.
  Con la batería en su límite, sin cargar o fuera de casa: ni llamadas ni avisos.
- **Solo el cargador de casa:** hace falta la casa configurada y la carga no puede pasar de
  `LOAD_CHARGER_MAX_KW` (7,4 kW por defecto): nunca actúa en un cargador público.
- Una pausa del control ya no parte la carga en dos. Quitado `LOAD_REQ_MAX_AGE_MIN`.

## 0.4.0-beta (2026-11-15)

- **Control de carga por consumo de la casa (opcional, experimental):** con Home Assistant
  (`HA_URL`, `HA_TOKEN`, `HA_POWER_ENTITY`), pausa la carga cuando la casa consume mucho y la
  reanuda cuando baja, para que no salte el limitador. Solo actúa con el coche en casa,
  enchufado, cargando y por debajo de su límite; tope de pausas por carga con aviso; el aviso de
  «Carga terminada» resume las pausas. Umbrales en ⚙ Ajustes. Guía: `docs/control-carga.md`.
  Parar y reanudar solo se ha probado en un Kia EV3 de Europa.

## 0.3.1-beta (2026-11-14)

Condiciones de la revisión final antes de publicar.

- **Contraseñas con `$`:** el `.env` se lee sin interpolar `${...}`; antes, una contraseña con
  `$` podía cambiar sin avisar.
- **CSP:** solo admite el fichero exacto de Chart.js. La ingesta del Trydan rechaza envíos de
  más de 64 KB y números desbordados. Las cargas por página tienen un límite.
- **Colector:** un fallo en una vuelta (lectura o resumen semanal) ya no lo detiene. Sin canal de
  avisos, el resumen no se reintenta cada minuto.
- **Configuración:** `CAR_REGION`, `CAR_BRAND` y `API_PORT` ilegibles en `.env` ya no impiden
  arrancar.
- **CI:** acciones fijadas por SHA.
- **Documentación:** `SECURITY.md` con aviso privado de vulnerabilidades. El README explica los
  permisos de la instalación manual y de la copia de la BD, y el límite de intentos.

## 0.3.0-beta (2026-11-14)

- **Resumen semanal con las cifras del panel:** km, viajes, cargas, kWh/100 km, coste en luz y
  ahorro frente a tu combustible, calculados igual que los informes (módulo `stats.py`).
- **Cuentas con varios coches:** `CAR_VIN` en `.env` elige cuál se lee; sin él, el log avisa y
  lista los VIN.
- **Pruebas del panel en un navegador real** (Playwright) en cada push.

## 0.2.1-beta (2026-11-14)

- **Requisito corregido:** hace falta Python **3.12** o superior (la librería del coche no instala en 3.11; lo detectó la CI).

## 0.2.0-beta (2026-11-14)

Endurecimiento tras la auditoría de la primera beta.

- **Seguridad:**
  - Las contraseñas y tokens de ejemplo ya no dan acceso. `DASH_PASS` necesita 8 caracteres o más, sin ñ ni tildes.
  - CSP y cabeceras de seguridad; el JavaScript sale a `/app.js` y Chart.js va con SRI.
  - Fuera `/docs`; las imágenes van con contraseña; la BD se crea con permisos 0600.
- **Robustez:**
  - La ingesta del Trydan valida lo que recibe.
  - Los datos incompletos del coche ya no tumban el panel, que además pinta cada sección por separado.
  - Un `.env` mal escrito no impide arrancar.
  - Una carga ya no se guarda dos veces.
- **Base de datos:** esquema versionado (`PRAGMA user_version`) con migraciones numeradas y arranque simultáneo seguro.
- **Accesibilidad:** contraste ≥ 4,5:1 en los textos de color, foco visible, textos alternativos en gráficas y mapa, y bienvenida para usuarios nuevos.
- **Instalación:** `TZ` se lee del `.env`; dependencias con versión máxima; licencia MIT; tests en GitHub Actions.

## 0.1.0-beta (2026-11-14)

Primera beta pública: panel con el color del coche, comparación con gasóleo o gasolina, informes en PDF, guía de conexión e instalador guiado.
