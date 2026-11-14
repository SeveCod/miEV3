# Seguridad

MiEV3 guarda datos sensibles: la ubicación del coche y las credenciales de tu cuenta de Kia
Connect, que permiten abrirlo. Si encuentras un fallo de seguridad, **no abras una incidencia
pública**.

## Cómo avisar

Usa la notificación privada de GitHub: pestaña **Security → Report a vulnerability** de
[SeveCod/miEV3](https://github.com/SeveCod/miEV3/security). Incluye:

- la versión (`cat VERSION`);
- los pasos para reproducirlo;
- qué permite hacer: leer datos, entrar sin contraseña, tumbar la app…

Es un proyecto personal en beta, mantenido en el tiempo libre: respondo en cuanto puedo,
normalmente en pocos días. El arreglo sale en una versión nueva y el aviso se publica cuando ya
hay versión corregida.

## Versiones con soporte

Solo la última versión publicada. Para actualizar, mira «Actualizar» en el README.

## Lo que ya hace MiEV3

- Panel con contraseña obligatoria: mínimo 8 caracteres, y las de ejemplo se rechazan.
- CSP sin scripts en línea y Chart.js con SRI.
- `.env` y base de datos solo legibles por tu usuario.
- La API escucha solo en `127.0.0.1`.
- Lo que no hace: limitar los intentos de contraseña. Si lo abres a internet, hazlo con HTTPS y,
  si quieres, limita los intentos en el proxy.
