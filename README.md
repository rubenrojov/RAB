# RAB — Raspberry Admin Bot v3.0

> Bot de Telegram para administración completa de una Raspberry Pi desde Telegram, sin necesidad de abrir ninguna consola SSH.

---

## Índice

1. [Funcionalidades](#funcionalidades)
2. [Arquitectura y funcionamiento](#arquitectura-y-funcionamiento)
3. [Capturas de pantalla](#capturas-de-pantalla)
4. [Requisitos del sistema](#requisitos-del-sistema)
5. [Instalación paso a paso](#instalación-paso-a-paso)
6. [Configuración del bot](#configuración-del-bot)
7. [Permisos sudo](#permisos-sudo)
8. [Servicio systemd](#servicio-systemd)
9. [Descripción detallada de módulos](#descripción-detallada-de-módulos)
10. [Alertas proactivas](#alertas-proactivas)
11. [Troubleshooting](#troubleshooting)

---

## Funcionalidades

| Módulo | Descripción |
|--------|-------------|
| 📊 **Sistema** | Dashboard en tiempo real, top procesos, salud SD, info de red, montajes |
| 📈 **Métricas** | Histórico de CPU/RAM/disco/temp con sparklines ASCII |
| ⚙️ **Servicios** | Estado WireGuard, logs Fail2Ban/SSH/Docker, cronjobs, servicios systemd |
| 🛡️ **Red/Seg** | Test velocidad, peers WG, jails Fail2Ban, IPs baneadas, desbanear, puertos, Geo-IP |
| 📦 **APT** | Paquetes actualizables, actualizar todo, limpieza, historial |
| 🐳 **Docker** | Listar contenedores, start/stop/restart, logs, stats, imágenes, prune |
| 📂 **Archivos** | Explorador, previsualizar, subir, descargar, borrar, crear carpetas, buscar |
| 🛠️ **Avanzado** | Terminal interactiva, ejecutar scripts .sh, Wake-on-LAN, reiniciar, apagar |
| ⚠️ **Alertas** | Recursos, servicios caídos, contenedores caídos, intentos de login |
| ☀️ **Resumen diario** | Informe automático cada mañana a las 08:00 |

---

## Arquitectura y funcionamiento

### Visión general

```
Telegram App  <-->  Telegram Servers  <-->  RAB Bot (Raspberry Pi)
                                             |
                                             ├── python-telegram-bot (polling)
                                             ├── Jobs periódicos (job_queue)
                                             ├── psutil (métricas del sistema)
                                             ├── subprocess (comandos del sistema)
                                             └── Archivos CSV (métricas e historial)
```

### Flujo de una interacción

1. El usuario pulsa un botón en Telegram → genera un `callback_query`
2. `router_botones()` recibe el `callback_data` y ejecuta la acción correspondiente
3. Para operaciones de sistema se usa `exec_cmd()` que llama a `subprocess.run()`
4. El resultado se formatea y se envía de vuelta al chat con `edit_message_text()`

### Modo de entrada de texto

Algunas acciones requieren que el usuario escriba texto (terminal, desbanear IP, geo-IP, crear carpeta, buscar archivo). El bot usa `context.user_data['mode']` para saber en qué estado se encuentra y `handle_everything()` gestiona el mensaje recibido según ese modo.

### Sistema de caché de paths

Los `callback_data` de Telegram tienen un límite de 64 bytes. Para el explorador de archivos, los paths del sistema pueden ser más largos. La solución es un diccionario `_PATH_CACHE` que mapea un hash MD5 de 12 caracteres al path completo:

```python
_PATH_CACHE: dict = {}

def path_a_key(path: str) -> str:
    key = hashlib.md5(path.encode()).hexdigest()[:12]
    _PATH_CACHE[key] = path
    return key
```

---

## Capturas de pantalla

### Menú principal

```
🏠 Panel de Control RAB v3.0
15/01/2025 08:32

┌─────────────────┬──────────────────┐
│  📊 Sistema     │  ⚙️ Servicios    │
├─────────────────┼──────────────────┤
│  📦 APT/Manten  │  🛡️ Red/Seg     │
├─────────────────┼──────────────────┤
│  🐳 Docker      │  📂 Archivos     │
├─────────────────┼──────────────────┤
│  🛠️ Avanzado    │  📈 Métricas     │
└─────────────────┴──────────────────┘
```

### Dashboard del sistema

```
📊 Dashboard del Sistema

🟢  CPU:   12%
🟢  RAM:   54%  (556MB/1024MB)
🟢  Disco: 38%  (14GB/32GB)
🟢  Temp:  48.2C
📶  IP:     192.168.1.42
⏱️  Uptime: 12d 4h 22m
⚖️  Carga:  0.15 / 0.18 / 0.12

         [⬅️ Volver]
```

### Docker — lista de contenedores

```
🐳 Contenedores:

🟢 portainer            Up 12 days
🟢 nginx-proxy          Up 12 days
🟢 vaultwarden          Up 12 days
🔴 syncthing            Exited (1) 2h ago

┌──────────────┬────┬────┬────┐
│ 📜 portainer │ ⏹  │ ▶️ │ 🔄 │
├──────────────┼────┼────┼────┤
│ 📜 nginx     │ ⏹  │ ▶️ │ 🔄 │
├──────────────┼────┼────┼────┤
│ 📜 vault     │ ⏹  │ ▶️ │ 🔄 │
├──────────────┼────┼────┼────┤
│ 📜 syncthing │ ⏹  │ ▶️ │ 🔄 │
└──────────────┴────┴────┴────┘
         [⬅️ Volver]
```

### Métricas históricas (sparklines)

```
📈 Métricas Historicas

🖥️ CPU   `▁▁▂▁▁▃▂▁▁▂▄▃▂▁▁▂▁▁▃▂▁▁▂▁` 12%
🧠 RAM   `▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄` 54%
💾 Disco `▃▃▃▃▃▃▃▃▃▃▃▃▃▃▃▃▃▃▃▃▃▃▃▃` 38%
🌡️ Temp  `▃▃▃▃▄▃▃▃▃▃▃▃▄▄▃▃▃▃▃▃▃▃▃▃` 48.1C

Ultima: 08:30
```

### Alerta automática de recursos

```
⚠️ ALERTA DE RECURSOS

🔴 CPU al 87% (umbral 80%)
🔴 RAM al 91% (umbral 85%)
```

### Explorador de archivos

```
📍 `/home/$USER`
_1-8 de 23_

[📁 ⬆️ Subir nivel        ]
[📁 docker               ]
[📁 tgbot                ]
[📁 backups              ]
[📄 .bashrc              ]
[📄 .ssh                 ]
[📄 notas.txt            ]
[📄 script.sh            ]

[◀️ Ant]          [Sig ▶️]

[📤 Subir archivo] [📁 Nueva carpeta]
[🔍 Buscar]  [👁️ Ocultos: OFF]
[⬅️ Volver]
```

### Terminal interactiva

```
💻 Terminal Activa
Envia cualquier comando. Escribe `salir` para cerrar.

> df -h

Filesystem      Size  Used Avail Use% Mounted on
/dev/mmcblk0p2   30G   11G   18G  38% /
tmpfs           459M     0  459M   0% /dev/shm
/dev/mmcblk0p1  253M   49M  204M  20% /boot

[🏠 Menu]  [❌ Cerrar terminal]
```

### Resumen diario automático (08:00)

```
☀️ Resumen Diario — 15/01/2025 08:00

🖥️ CPU:      8%
🧠 RAM:      51% (522MB/1024MB)
💾 Disco:    38%
🌡️ Temp:     46.1C
🌐 IP local: 192.168.1.42
⏱️ Uptime:   12d 4h 0m
🐳 Docker:   4 corriendo: portainer, nginx-proxy, vaultwarden, syncthing
```

---

## Requisitos del sistema

### Paquetes del sistema operativo

```bash
sudo apt update && sudo apt install -y \
    python3 \
    python3-pip \
    python3-venv \
    wireguard \
    fail2ban \
    speedtest-cli \
    wakeonlan \
    iproute2 \
    procps
```

> **Nota:** Docker debe estar instalado por separado siguiendo la
> [guía oficial para Raspberry Pi](https://docs.docker.com/engine/install/raspberry-pi-os/).

Añadir el usuario al grupo `docker` para que el bot pueda ejecutar
comandos Docker sin sudo:

```bash
sudo usermod -aG docker $USER
# Es necesario cerrar sesión y volver a entrar para que tenga efecto
```

### Requisitos de Python

- Python 3.9 o superior (Raspbian Trixie incluye 3.11)
- Se usa un **entorno virtual** para aislar las dependencias del resto del sistema

---

## Instalación paso a paso

### 1. Obtener el token del bot

1. Abre Telegram y busca **@BotFather**
2. Envía `/newbot` y sigue las instrucciones
3. Guarda el token que te proporciona (formato: `123456789:ABCdef...`)

Para obtener tu ID de usuario:
1. Busca **@userinfobot** en Telegram
2. Envía `/start` — te responderá con tu `Id` numérico

### 2. Clonar el repositorio

```bash
git clone https://https://github.com/rubenrojov/RAB /home/$USER/tgbot
cd /home/$USER/tgbot
```

### 3. Crear el entorno virtual Python

```bash
# Crear el entorno virtual
python3 -m venv venv

# Activarlo
source venv/bin/activate

# Instalar dependencias
pip install -r requirements.txt

# Desactivarlo (el servicio systemd lo activará automáticamente)
deactivate
```

Verificar que la instalación es correcta:

```bash
source venv/bin/activate
python -c "import telegram; print('OK:', telegram.__version__)"
deactivate
```

---

## Configuración del bot

Edita las primeras líneas de `bot_control.py` con tu editor favorito:

```bash
nano /home/$USER/tgbot/bot_control.py
```

```python
# ============================================================
# CONFIGURACIÓN — edita estas líneas
# ============================================================
TOKEN         = "123456789:ABCdefGHIjklMNOpqrSTUvwxYZ"  # token de @BotFather
MI_USUARIO_ID = 123456789        # tu ID numérico de Telegram
ROOT_DIR      = "/home/$USER"    # raíz del explorador de archivos

# Equipos para Wake-on-LAN (nombre: dirección MAC)
WOL_MACS = {
    "PC-Principal": "AA:BB:CC:DD:EE:FF",
    "NAS":          "11:22:33:44:55:66",
}

# Umbrales para alertas automáticas
UMBRALES = {
    "cpu":  80,   # % de uso de CPU
    "ram":  85,   # % de uso de RAM
    "disk": 90,   # % de uso de disco
    "temp": 70,   # temperatura en °C
}

# Servicios systemd que se monitorizan cada 2 minutos
SERVICIOS_WATCH = ["docker", "wg-quick@wg0", "fail2ban", "ssh"]
```

> ⚠️ **Seguridad:** Se recomienda no subir el token al repositorio.
> Puedes sacarlo a una variable de entorno y leerla con `os.environ.get("RAB_TOKEN")`.

---

## Permisos sudo

El bot necesita ejecutar algunos comandos como root. La forma más segura es
conceder permisos específicos con `visudo`, **sin dar acceso total a sudo**.

```bash
sudo visudo
```

Añade al final del archivo (ajusta las rutas con `which <comando>` si difieren):

```
$USER ALL=(ALL) NOPASSWD: /usr/bin/wg, \
    /usr/sbin/fail2ban-client, \
    /usr/sbin/shutdown, \
    /usr/sbin/reboot, \
    /usr/bin/apt-get, \
    /usr/bin/journalctl, \
    /usr/bin/tail
```

Verificar rutas en tu sistema:

```bash
which wg             # normalmente /usr/bin/wg
which fail2ban-client # normalmente /usr/bin/fail2ban-client
which shutdown       # normalmente /usr/sbin/shutdown
```

---

## Servicio systemd

Esto hace que el bot arranque automáticamente con el sistema y se reinicie
si falla.

### Contenido de `rab-bot.service`

```ini
[Unit]
Description=RAB - Raspberry Admin Bot v3.0
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$USER
WorkingDirectory=/home/$USER/tgbot
ExecStart=/home/$USER/tgbot/venv/bin/python /home/$USER/tgbot/bot_control.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

### Instalar y activar el servicio

```bash
# Copiar el archivo de servicio
sudo cp /home/$USER/tgbot/rab-bot.service /etc/systemd/system/

# Recargar systemd para que detecte el nuevo servicio
sudo systemctl daemon-reload

# Habilitar el servicio (arrancará automáticamente tras cada reinicio)
sudo systemctl enable rab-bot

# Arrancarlo ahora mismo
sudo systemctl start rab-bot

# Verificar que está corriendo
sudo systemctl status rab-bot
```

La salida de `status` debería mostrar `Active: active (running)`.

### Comandos útiles de gestión

```bash
# Ver logs en tiempo real
sudo journalctl -u rab-bot -f

# Ver los últimos 50 logs
sudo journalctl -u rab-bot -n 50

# Reiniciar el bot (necesario tras cambiar bot_control.py)
sudo systemctl restart rab-bot

# Parar el bot
sudo systemctl stop rab-bot

# Deshabilitar el arranque automático
sudo systemctl disable rab-bot
```

---

## Descripción detallada de módulos

### `exec_cmd()` — ejecutor de comandos

```python
async def exec_cmd(comando, shell=False, timeout=45) -> str:
```

Función central del bot. Ejecuta cualquier comando del sistema de forma
asíncrona usando `subprocess.run()`. Gestiona:
- Timeout configurable para evitar que el bot se bloquee
- Captura de `stdout` y `stderr`
- Retorno limpio del resultado o mensaje de error descriptivo

### `get_temperatura()` — lectura de temperatura

Lee la temperatura del SoC de la Raspberry Pi desde el sistema de ficheros
virtual `/sys/class/thermal/thermal_zone0/temp` (en milésimas de grado).
Si falla, intenta con el comando `vcgencmd measure_temp` propio de Raspberry Pi.

### `guardar_metrica()` y `leer_metricas_recientes()` — histórico

Cada 5 minutos `monitor_recursos()` llama a `guardar_metrica()` que añade
una línea al CSV `/home/$USER/rab_metricas.csv` con timestamp, CPU, RAM,
disco y temperatura. `leer_metricas_recientes(n)` devuelve las últimas `n`
mediciones para generar los sparklines del módulo de métricas.

### `sparkline()` — gráficas ASCII

```python
def sparkline(valores: list) -> str:
    chars = ["▁","▂","▃","▄","▅","▆","▇","█"]
```

Convierte una lista de valores numéricos en una cadena de caracteres Unicode
de bloques de altura variable. Normaliza automáticamente al rango mín-máx.

### `salud_sd()` — diagnóstico de la tarjeta SD

Lee las estadísticas de bloques de `/sys/block/mmcblk0/stat` para obtener
el número acumulado de lecturas y escrituras. A mayor número de escrituras,
más desgaste acumula la tarjeta. Complementa con temperatura, uso de disco
y uptime para dar una visión completa del estado del hardware.

### `monitor_recursos()` — monitor periódico (cada 5 min)

Job registrado en `job_queue` de python-telegram-bot. Comprueba CPU, RAM,
disco y temperatura contra los umbrales configurados. Si alguno los supera,
envía un mensaje proactivo al usuario. También llama a `guardar_metrica()`
para mantener el histórico actualizado.

### `monitor_servicios()` — vigilancia de servicios (cada 2 min)

Itera sobre `SERVICIOS_WATCH` y ejecuta `systemctl is-active <servicio>`
para cada uno. Si algún servicio no está `active`, notifica inmediatamente.

### `monitor_docker()` — vigilancia de contenedores (cada 3 min)

Ejecuta `docker ps -a` y comprueba que todos los contenedores conocidos
estén en estado `Up`. Notifica si alguno está `Exited` o en estado de error.

### `monitor_intentos_login()` — vigilancia de auth.log (cada 10 min)

Lee `/var/log/auth.log` incrementalmente (recuerda el offset de la última
lectura en `context.bot_data`) y cuenta líneas con `Failed password` o
`Invalid user`. Si hay 5 o más intentos nuevos, envía alerta con el resumen.

### `router_botones()` — enrutador principal

Función central de la interfaz. Recibe todos los `callback_query` de los
botones inline y los enruta según el valor de `callback_data`. Cada sección
del menú tiene su propio bloque `elif`. Registra cada acción en el log de
actividad mediante `registrar_actividad()`.

### `handle_everything()` — gestor de mensajes de texto

Gestiona los mensajes de texto y documentos enviados por el usuario. El
comportamiento depende de `context.user_data['mode']`:

| Modo | Acción |
|------|--------|
| `terminal` | Ejecuta el texto como comando de shell |
| `upload` | Guarda el documento recibido en `current_path` |
| `unban_ip` | Desbanea la IP recibida en Fail2Ban |
| `geoip` | Consulta la IP en ip-api.com |
| `mkdir` | Crea una carpeta con el nombre recibido |
| `file_search` | Ejecuta `find` con el término recibido |
| `None` | Informa de que hay que usar /start |

---

## Alertas proactivas

El bot funciona de forma proactiva incluso sin que el usuario interactúe.
Los intervalos son configurables en el bloque `job_queue` al final del script.

```python
jq.run_repeating(monitor_recursos,       interval=300, first=60)   # cada 5 min
jq.run_repeating(monitor_servicios,      interval=120, first=30)   # cada 2 min
jq.run_repeating(monitor_docker,         interval=180, first=45)   # cada 3 min
jq.run_repeating(monitor_intentos_login, interval=600, first=120)  # cada 10 min
jq.run_daily(resumen_diario, time=datetime.time(hour=8, minute=0, tzinfo=ZONA_HORARIA))
```

El parámetro `first` indica cuántos segundos esperar tras el arranque antes
de la primera ejecución, para dar tiempo a que el sistema se estabilice.

---

## Troubleshooting

### El bot no responde a /start

**Causa más probable:** Token incorrecto o `MI_USUARIO_ID` mal configurado.

```bash
# Verificar que el bot está corriendo
sudo systemctl status rab-bot

# Ver logs de error
sudo journalctl -u rab-bot -n 50

# Probar el token manualmente
curl https://api.telegram.org/bot<TU_TOKEN>/getMe
```

Si `getMe` devuelve `{"ok":true,...}` el token es correcto. Si devuelve
`{"ok":false}`, el token es inválido — regenera uno con @BotFather.

---

### Error: `ModuleNotFoundError: No module named 'telegram'`

El servicio systemd no está usando el entorno virtual correcto.

```bash
# Verificar que el venv existe
ls /home/$USER/tgbot/venv/bin/python

# Verificar que telegram está instalado en el venv
/home/$USER/tgbot/venv/bin/pip list | grep telegram

# Si no está, instalarlo
/home/$USER/tgbot/venv/bin/pip install -r /home/$USER/tgbot/requirements.txt
```

Asegúrate de que `ExecStart` en el `.service` apunta a
`/home/$USER/tgbot/venv/bin/python` y **no** a `/usr/bin/python3`.

---

### Error: `sudo: wg: command not found` o similar

Las rutas en el bloque `sudoers` no coinciden con las del sistema.

```bash
# Buscar la ruta real de cada comando
which wg
which fail2ban-client
which shutdown
which reboot
which apt-get
which journalctl
which tail
```

Actualiza el bloque en `visudo` con las rutas correctas.

---

### Los comandos sudo devuelven `Error (rc=1): sudo: a password is required`

El usuario no tiene configurado el acceso sin contraseña para esos comandos.

```bash
# Verificar la configuración sudoers
sudo visudo -c

# Probar manualmente
sudo -n wg show
```

Si pide contraseña, el bloque `NOPASSWD` no está bien aplicado.
Revisa que el bloque en `visudo` usa la ruta exacta del binario.

---

### El test de velocidad falla o da timeout

```bash
# Verificar que speedtest-cli está instalado
which speedtest-cli

# Ejecutarlo manualmente para ver el error
speedtest-cli --simple

# Si no está instalado
sudo apt install speedtest-cli
# o con pip en el venv:
source /home/$USER/tgbot/venv/bin/activate
pip install speedtest-cli
deactivate
```

El timeout del test está en 90 segundos. En conexiones muy lentas puede
no ser suficiente — ajusta el valor en la línea:
```python
res = await exec_cmd("speedtest-cli --simple 2>&1", shell=True, timeout=90)
```

---

### Estado de jails de Fail2Ban no muestra nada

```bash
# Verificar que fail2ban está activo
sudo systemctl status fail2ban

# Probar el comando manualmente
sudo fail2ban-client status

# Ver qué jails están configuradas
sudo fail2ban-client status | grep "Jail list"
```

Si el servicio está activo pero no hay jails, revisa
`/etc/fail2ban/jail.local` — puede que no tengas ninguna jail habilitada.

---

### WireGuard muestra error de permisos

```bash
# El comando wg show necesita permisos de root
sudo wg show

# Verificar que está en sudoers
sudo -n wg show
```

---

### El explorador de archivos no navega correctamente

Esto puede ocurrir si el `_PATH_CACHE` se pierde al reiniciar el bot.
La caché es en memoria — si el bot se reinicia, los hashes anteriores
dejan de ser válidos. Solución: vuelve al menú principal con `/start`
y navega de nuevo desde la raíz.

---

### El bot no envía alertas proactivas

```bash
# Verificar que job_queue está activo (requiere el extra [job-queue])
source /home/$USER/tgbot/venv/bin/activate
python -c "from telegram.ext import JobQueue; print('OK')"
deactivate
```

Si falla, reinstala con el extra correcto:

```bash
source /home/$USER/tgbot/venv/bin/activate
pip install "python-telegram-bot[job-queue]==21.6"
deactivate
sudo systemctl restart rab-bot
```

---

### Mensajes de error sobre `MI_USUARIO_ID` siendo `int` vs `str`

Asegúrate de que `MI_USUARIO_ID` es un **número entero**, no una cadena:

```python
MI_USUARIO_ID = 123456789    # correcto
MI_USUARIO_ID = "123456789"  # incorrecto — causará que el bot no responda
```

---

### Alto consumo de CPU por el propio bot

Los jobs de monitorización llaman a `psutil.cpu_percent(interval=2)` que
bloquea 2 segundos midiendo la CPU. En una Raspberry Pi 3B con 1GB RAM
esto es normal. Si quieres reducir la carga, aumenta los intervalos de
los jobs o reduce `interval=2` a `interval=0.5`.

---

## Actualizar

```bash
cd /home/$USER/tgbot
git pull
source venv/bin/activate
pip install -r requirements.txt
deactivate
sudo systemctl restart rab-bot
sudo systemctl status rab-bot
```

---

## Estructura del repositorio

```
tgbot/
├── bot_control.py      # script principal del bot
├── requirements.txt    # dependencias Python del entorno virtual
├── rab-bot.service     # unidad systemd para arranque automático
└── README.md           # esta documentación

# Ficheros generados en tiempo de ejecución (no incluidos en el repo):
/home/$USER/rab_actividad.log   # registro CSV de todas las acciones
/home/$USER/rab_metricas.csv    # histórico de métricas del sistema
```

---

## Seguridad

- El bot ignora silenciosamente cualquier mensaje de un usuario distinto a `MI_USUARIO_ID`
- Las operaciones destructivas (borrar archivos, parar contenedores, reiniciar, apagar) requieren confirmación con un segundo botón antes de ejecutarse
- Los permisos sudo están acotados a comandos específicos, sin acceso total a root
- Se recomienda excluir el token del repositorio usando `.gitignore` y una variable de entorno:

```bash
# En .gitignore
.env

# En .env
RAB_TOKEN=123456789:ABCdef...

# En bot_control.py
import os
TOKEN = os.environ.get("RAB_TOKEN", "")
```

Cargar el `.env` en el servicio systemd añadiendo en `[Service]`:
```ini
EnvironmentFile=/home/$USER/tgbot/.env
```

---

## Licencia

Uso personal. Sin garantías.
