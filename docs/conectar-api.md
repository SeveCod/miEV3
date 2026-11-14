# Conectar MiEV3 con tu coche

MiEV3 lee el coche a través de la nube del fabricante, la misma que usa la app **Kia Connect**.
Kia no publica una API oficial para particulares, así que la conexión se hace con la librería de
código abierto [hyundai_kia_connect_api](https://github.com/Hyundai-Kia-Connect/hyundai_kia_connect_api).
Es la librería en la que se apoya la integración de Home Assistant. No hace falta tocar el coche
ni instalarle nada.

## 1. Antes de empezar

- El coche tiene que estar **activado en la app Kia Connect** y verse en ella: batería, posición…
  Si la app no lo ve, MiEV3 tampoco.
- Usa la cuenta **principal** del coche, la que lo dio de alta. Las cuentas invitadas pueden no
  tener acceso a todos los datos (*sin verificar*).
- Apunta el **PIN** de la app y ponlo en `KIA_PIN`. La librería lo recibe al conectar, aunque
  MiEV3 no manda órdenes al coche (*sin verificar* si es obligatorio para solo leer).

## 2. Rellenar el `.env`

```ini
KIA_USER=tu_email@ejemplo.com      # el de la app Kia Connect
KIA_PASSWORD=tu_contraseña         # o un refresh token (ver punto 4)
KIA_PIN=0000
CAR_REGION=1                       # 1 Europa, 2 Canadá, 3 EE. UU., 4 China, 5 Australia…
CAR_BRAND=1                        # 1 Kia, 2 Hyundai, 3 Genesis
```

Solo se ha probado **Kia en Europa** (región 1, marca 1) con un EV3. Otras combinaciones usan la
misma librería y deberían leer al menos la batería, pero no están comprobadas.

## 3. Probar la conexión

```bash
venv/bin/python -c "from collectors import kia; print(kia.snapshot())"
```

Si imprime tu % de batería (`'soc': 80.0, …`), la conexión funciona: arranca el colector y el
panel (`pm2 start ecosystem.config.js`). Si da error, mira la tabla de problemas del final.

## 4. Si el login falla con la contraseña correcta

En Europa el fabricante cambia a menudo el inicio de sesión, y cada cambio rompe la librería
hasta que sale una versión nueva. Pasó dos veces en octubre de 2026:

| Fecha | Síntoma | Arreglo |
|---|---|---|
| 14/10/2026 | HTTP 400, código 9035, «Unusual activity» | Versión 4.35.3 de la librería |
| 22/10/2026 | «returned to login page… Check username and password», aunque estaban bien | Versión 4.41.1 (nuevo User-Agent) |

Qué hacer, en este orden:

1. **No cambies la contraseña** ni pruebes muchas veces seguidas: los intentos fallidos podrían
   bloquear la cuenta un tiempo (*sin verificar*, pero no merece la pena arriesgarse).
2. Mira las [issues](https://github.com/Hyundai-Kia-Connect/hyundai_kia_connect_api/issues) y
   [releases](https://github.com/Hyundai-Kia-Connect/hyundai_kia_connect_api/releases) de la
   librería. Casi siempre otro usuario ya lo ha reportado y hay una versión nueva.
3. Sube la versión en `requirements.txt` y reinstala:
   `venv/bin/pip install -r requirements.txt && pm2 restart miev3-api miev3-collector`.
4. Algunas cuentas europeas piden un **refresh token** en lugar de la contraseña, o una
   verificación por código (OTP). Cómo sacarlo lo explica el README de la librería: ponlo en
   `KIA_PASSWORD`. MiEV3 no guarda la sesión, así que cada reinicio vuelve a iniciar sesión.

## 5. Lo que hay que saber de la API

- **Los datos no son en directo.** MiEV3 lee el último estado que el coche subió a la nube.
  Por lo observado en un EV3, el coche lo sube al apagarse y durante la carga, así que el dato
  puede tener minutos u horas. El panel enseña siempre la hora del dato («Dato del coche: …»).
- **Nunca se fuerza al coche a actualizar.** Despertarlo gasta la batería de 12 V y el cupo
  diario de llamadas de la cuenta (el límite exacto no está publicado). Por eso el mínimo es una
  lectura cada 30 minutos, y el ajuste no deja bajarlo.
- **«Cargando» en coches nuevos (CCS2, como el EV3):** la librería lo deduce del tiempo restante
  de carga y a veces no lo sabe. Entonces MiEV3 mantiene el estado anterior para no cerrar una
  carga en falso.
- **El consumo del coche se queda corto.** El contador de energía que publica Kia marcó un 23 %
  menos de lo que bajó la batería en un EV3. MiEV3 lo calibra con el % de batería a partir de
  unos 300 km (ver [cómo funciona](como-funciona.md#consumo-real)).
- **Viajes sin ruta.** En Europa la nube da cada viaje (hora, km, minutos y velocidades), pero
  no el recorrido. MiEV3 descarga los del día a las 23:45.
- **Energía recuperada:** aparece en los datos, pero no cuadra con el balance de batería. Está
  marcada «pendiente de aclarar».
- **Ubicación:** la posición del coche se guarda en tu base de datos y se ve en el panel. No
  publiques el panel sin HTTPS y contraseña.

## 6. Problemas frecuentes

| Síntoma | Causa probable | Qué hacer |
|---|---|---|
| `AuthenticationError` con la contraseña bien | Cambio en el login del fabricante | Punto 4: actualizar la librería |
| Pide OTP o código | Cuenta con verificación adicional | Refresh token (README de la librería) |
| `StopIteration` o ningún vehículo | El coche no está en esa cuenta, o la región o la marca son incorrectas | Revisa la app, `CAR_REGION` y `CAR_BRAND` |
| El panel dice «Aún no hay lecturas» | El colector no está arrancado o falla | `pm2 logs miev3-collector` |
| Aviso «⚠️ sin datos del coche» | Login roto o nube caída | Punto 4; si la app del móvil tampoco va, es la nube |
| Los viajes de hoy no salen | Se descargan a las 23:45 | Espera a esa hora |
| Batería con hora antigua | El coche no ha subido estado (aparcado y dormido) | Normal: se actualiza al usarlo o cargarlo |
