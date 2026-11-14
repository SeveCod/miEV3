# MiEV3

> **Beta.** Funciona a diario con un Kia EV3, pero es una versión para recoger mejoras: si algo
> falla o echas algo en falta, cuéntalo.

Panel, informes y avisos para tu Kia EV3 (y otros eléctricos de Kia, Hyundai o Genesis con app
conectada). Lee cada 30 minutos el estado que publica la nube del fabricante. A partir de él
detecta las cargas y calcula cuánto te cuestan, guarda tus viajes y te compara con un coche de
gasóleo o de gasolina. Avisa por Telegram y lo enseña todo en un panel web protegido con
contraseña, con el color de tu coche.

Funciona en cualquier máquina encendida 24/7 con Python 3.11 o superior: un VPS, una Raspberry Pi
o un mini PC. No hace falta tocar el coche ni instalar nada en él.

> **Probado solo con un Kia EV3 en Europa.** Otros modelos y regiones de la misma librería
> deberían leer el estado básico, pero los viajes y el consumo diario dependen de lo que publique
> cada coche. Si lo pruebas con otro, cuéntalo.

## Qué hace

| Función | Cómo |
|---|---|
| Batería, autonomía, km, cierre, salud y 12 V | Lectura en caché de la nube del fabricante (no despierta el coche) |
| Cargas: kWh y coste | Por la subida del % de batería × capacidad ÷ eficiencia × precio |
| Consumo real (kWh/100 km) | Contador del coche calibrado con el % de batería |
| Ahorro frente a combustión | Tu coste por km frente al de un coche de gasóleo o gasolina que tú defines |
| Informes en PDF | Semanal, mensual o histórico: km, viajes, consumo, costes, ahorro y cargas |
| Personalización | Nombre, color de la carrocería (el panel toma su color), precios y consumos |
| Viajes del día | Distancia, tiempo, velocidades (sin ruta dibujada) |
| Avisos | Carga terminada, batería baja sin enchufar, coche sin datos y resumen semanal |
| Cargador V2C Trydan (opcional) | Agente en casa que envía sus lecturas al panel |

Conectar con el coche y peculiaridades de la API: [docs/conectar-api.md](docs/conectar-api.md).
Cómo se calcula cada cifra: [docs/como-funciona.md](docs/como-funciona.md).
Cada ajuste explicado: [docs/ajustes.md](docs/ajustes.md).

## Instalación

```bash
git clone <este-repo> miev3 && cd miev3
python3 -m venv venv && venv/bin/pip install -r requirements.txt
cp .env.example .env && nano .env        # cuenta del coche, contraseña del panel, Telegram
venv/bin/python -c "from collectors import kia; print(kia.snapshot())"   # ¿lee el coche?
```

Si la última línea imprime tu % de batería, arranca los dos procesos: el colector, que lee el coche
y avisa, y el panel. Si da error, sigue la [guía de conexión](docs/conectar-api.md).

```bash
npm install -g pm2                        # si no lo tienes
pm2 start ecosystem.config.js && pm2 save
```

En `ecosystem.config.js`, cambia `TZ` si no vives en la zona horaria de Madrid.

### Abrir el panel

La API escucha solo en `127.0.0.1:8090`. Tienes dos maneras de abrirla:

- **Desde tu ordenador:** `ssh -L 8090:localhost:8090 tu-servidor` y abre http://localhost:8090.
- **Desde el móvil:** ponla detrás de nginx o Caddy con HTTPS. Deja `/ingest/v2c` fuera de la
  contraseña si usas el agente del Trydan, porque ese endpoint se protege con su propio token.

Usuario y contraseña: `DASH_USER` y `DASH_PASS` del `.env`. **El panel muestra dónde está aparcado
el coche: nunca lo publiques sin HTTPS.**

### Primer uso

1. Entra en el panel y pulsa **⚙ Ajustes**: nombre y color del coche, batería, precio de la luz,
   combustible con el que te comparas (gasóleo o gasolina), su precio y su consumo… Los cambios
   se aplican al momento, sin reiniciar.
2. La primera lectura llega a los pocos segundos de arrancar el colector. Los viajes del día se
   descargan a las 23:45.
3. El consumo sale **provisional** hasta que haya unos 300 km sin cargar (ver
   [cómo funciona](docs/como-funciona.md#consumo-real)).

### Informes en PDF

Pulsa **📄 Informes**, elige **Semana**, **Mes** o **Histórico** y el periodo, y luego
**Descargar PDF**. Se abre la ventana de impresión del navegador: elige «Guardar como PDF». En
el móvil, «Compartir → Imprimir» o «Guardar como PDF», según el navegador. Cada informe trae:

- km, viajes, horas al volante, consumo medio y energía consumida;
- coste estimado en luz, lo que habría costado en gasóleo o gasolina, y el ahorro;
- cargas del periodo (número, kWh y €) y el odómetro al principio y al final;
- una gráfica y una tabla por día (semana y mes) o por mes (histórico), y la lista de viajes.

### Probarlo sin coche

```bash
DB_PATH=demo.db venv/bin/python scripts/seed_demo.py           # 60 días inventados
DB_PATH=demo.db DASH_PASS=demo venv/bin/python api.py          # http://127.0.0.1:8090 (admin / demo)
```

## Avisos

- **Telegram** (lo más fácil): crea un bot con @BotFather y pon `TELEGRAM_BOT_TOKEN`. Escríbele
  algo y saca tu `TELEGRAM_CHAT_ID` de `https://api.telegram.org/bot<TOKEN>/getUpdates`.
- **Webhook propio:** si no hay Telegram y `NOTIFY_URL` tiene valor, se hace un POST JSON con
  `topic`, `urgency` (`TODAY` o `INFO`), `text`, `event_id` (sirve para deduplicar) y la cabecera
  `X-Notify-Secret`.
- Sin ninguno de los dos, los avisos solo quedan en el log (`pm2 logs miev3-collector`).

## ⚠️ Login en Europa y límites de la API

Resumen; el detalle está en la [guía de conexión](docs/conectar-api.md).

- El fabricante cambia a menudo el inicio de sesión europeo. Si el login falla y tus credenciales
  están bien, **actualiza primero** `hyundai_kia_connect_api` (`requirements.txt`): casi siempre
  ya está arreglado ahí.
- Solo se lee el **estado en caché** y nunca se fuerza el refresco del coche: forzarlo gasta la
  batería de 12 V y el cupo diario de llamadas. Por eso el intervalo mínimo es de 30 minutos.
- Si el coche deja de responder, recibes un aviso a las 2 h, otro cada 12 h y uno cuando vuelve.

## Cargador V2C Trydan (opcional)

El servidor normalmente no ve el cargador de casa. Para conectarlos:

1. Da una IP fija al Trydan en el router.
2. En un equipo de casa que esté siempre encendido, crea un `.env` con `TRYDAN_IP`,
   `VPS_INGEST_URL=https://tu-dominio/ingest/v2c` e `INGEST_TOKEN` (el mismo que en el servidor).
3. Ejecuta `python v2c_agent.py` (o arráncalo con pm2).

**Sin verificar:** el endpoint `/RealTimeData` y los nombres de sus campos dependen del firmware.
Compruébalo antes con `curl http://IP_DEL_TRYDAN/RealTimeData`. La tarjeta del Trydan solo aparece
en el panel cuando llegan datos.

## Privacidad

- Todo se queda en tu máquina, en `miev3.db` (SQLite), ubicaciones incluidas. No se envía nada
  a terceros salvo los avisos al canal que configures.
- `.env` y `*.db` están en `.gitignore`.
- Para la foto del coche en la cabecera, deja tu imagen en `static/img/car.webp`. Sin ella, el
  hueco no aparece.

## Desarrollo

```bash
venv/bin/pip install pytest httpx
venv/bin/pytest            # usa coches simulados y bases de datos temporales, sin red
```

Estructura: `main.py` (colector), `api.py` (API y panel), `settings.py` (ajustes editables),
`sessions.py` (cargas y avisos de batería), `collectors/kia.py` (lectura del coche),
`notify.py` (avisos), `db.py` (esquema) y `static/index.html` (panel, sin compilación).
