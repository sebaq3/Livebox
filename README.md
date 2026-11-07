# Orange Livebox Arcadyan device tracker

Integración personalizada para Home Assistant que crea rastreadores de los
dispositivos conectados a routers Orange Livebox con firmware Arcadyan.

La integración consulta el router en la red local y usa la contraseña de
administración configurada en su interfaz web. Solo se crean entidades
`device_tracker`; no se ofrecen sensores, interruptores, botones ni calendario.

## Instalación con HACS

Hasta que el repositorio se incorpore a la tienda predeterminada de HACS:

1. En HACS, abre **Integraciones** y selecciona **Repositorios personalizados**.
2. Añade la URL de este repositorio de GitHub y selecciona la categoría
   **Integración**.
3. Instala **Orange Livebox device tracker** y reinicia Home Assistant.
4. En **Ajustes → Dispositivos y servicios**, añade la integración **Livebox**.

## Configuración

Introduce:

- **Dirección del router** (por defecto `192.168.1.1`).
- **Contraseña de administración** del router.
- **Puerto** (por defecto `80`).

La integración verifica la conexión y el acceso al listado de clientes antes de
guardar la configuración. En las opciones se puede ajustar el tiempo durante el
cual un dispositivo permanece como conectado después de que el router deje de
marcarlo en línea (por defecto, 300 segundos).

## Desarrollo

```bash
uv sync --group dev
uv run pytest
uv run prek run --all-files
```

## Alcance

La integración está desarrollada para el CGI de clientes de los routers
Arcadyan. No se garantiza compatibilidad con otros modelos de Livebox. La
conexión al router se realiza por HTTP sin cifrar. Úsala solo en una red local
confiable: alguien con acceso a esa red podría interceptar las credenciales o
la sesión del router.

## Agradecimientos

Este proyecto parte de [hass-livebox-component](https://github.com/cyr-ius/hass-livebox-component),
creado por [Antoine Colmard](https://github.com/cyr-ius). Gracias por el trabajo
original y por compartirlo bajo la licencia MIT.
