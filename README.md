# RAB — Raspberry Admin Bot v4.0

> Bot de Telegram para administración completa de una Raspberry Pi desde el móvil, sin necesidad de abrir ninguna consola SSH. Protegido con PIN de sesión.

---

## Índice

1. [Novedades v4.0](#novedades-v40)
2. [Funcionalidades completas](#funcionalidades-completas)
3. [Arquitectura y funcionamiento](#arquitectura-y-funcionamiento)
4. [Capturas de pantalla](#capturas-de-pantalla)
5. [Requisitos del sistema](#requisitos-del-sistema)
6. [Instalación paso a paso](#instalación-paso-a-paso)
7. [Configuración del bot](#configuración-del-bot)
8. [Permisos sudo](#permisos-sudo)
9. [Servicio systemd](#servicio-systemd)
10. [Descripción detallada de módulos](#descripción-detallada-de-módulos)
11. [Alertas proactivas](#alertas-proactivas)
12. [Troubleshooting](#troubleshooting)

---

## Novedades v4.0

### 🔐 Seguridad — PIN de sesión

El bot ahora requiere un PIN numérico al iniciar. Sin PIN validado, ningún botón ni comando funciona aunque el ID de Telegram sea el correcto. Esto protege el bot en caso de que el token quede expuesto.

- Sesión activa durante 4 horas (configurable con `PIN_TTL_MIN`)
- Bloqueo automático tras 3 intentos fallidos durante 15 minutos
- Alerta inmediata al propietario si alguien accede con un ID desconocido
- Alerta si se agotan los intentos de PIN

### 🗂️ Menú reorganizado por frecuencia de uso

```
┌─────────────────┬──────────────────┐
│  📊 Dashboard   │  🐳 Docker       │  ← más frecuente arriba
├─────────────────┼──────────────────┤
│  📂 Archivos    │  💻 Terminal     │  ← terminal directa, sin submenú
├─────────────────┼──────────────────┤
│  🛡️ Red/Seg    │  📦 APT          │
├─────────────────┼──────────────────┤
│  ⚙️ Sistema    │  🛠️ Avanzado     │
└─────────────────┴──────────────────┘
```

Cambios respecto a v3.0:
- **Dashboard** sube a primera posición y absorbe Métricas
- **Terminal** accesible directamente desde el menú principal
- **Servicios** desaparece como menú propio — logs redistribuidos en Red/Seg
- **Métricas** fusionadas dentro de Dashboard

### 📊 Dashboard ampliado

- Uso de red en tiempo real (KB/s por interfaz)
- Historial de reinicios con motivo del boot anterior
- Procesos zombies con opción de kill por PID
- Estado de VPN visible directamente en el dashboard

### 🛡️ Red/Seg expandido

- VPN on/off — encender/apagar WireGuard con confirmación, botón dinámico según estado actual
- Ping + Traceroute — introducir IP o dominio y obtener ambos resultados
- Escaneo LAN — `nmap -sn 192.168.0.0/24` para ver dispositivos activos
- Bloque de Logs completo: SSH logins OK, SSH fallidos, VPN, Fail2Ban, actividad del bot, errores del sistema, reinicios

### 🛠️ Avanzado ampliado

- **Comandos favoritos** — lista configurable ejecutable con un toque
- **Notas rápidas** — guardar, ver y borrar notas con timestamp
- **Silenciar alertas** — 1h, 4h o indefinido, con reactivación manual
- **Umbrales editables** — subir/bajar CPU/RAM/disco/temperatura ±5 desde el bot sin editar el script

### ⚙️ Sistema

- Calculadora de espacio — analizar `/`, `/home`, `/var/lib/docker` o ruta personalizada

---

## Funcionalidades completas

| Módulo | Funciones |
|--------|-----------|
| 🔐 **Seguridad** | PIN sesión, bloqueo por intentos, alertas de acceso ajeno, expiración automática |
| 📊 **Dashboard** | Sistema en vivo, métricas históricas, uso de red real, top procesos, zombies+kill, reinicios, salud SD, montajes |
| 🛡️ **Red** | Test velocidad, VPN on/off, WG peers, ping/traceroute, escaneo LAN, puertos, Geo-IP |
| 🔒 **Seguridad** | Jails Fail2Ban, IPs baneadas, desbanear IP |
| 📋 **Logs** | SSH OK, SSH fallidos, VPN, Fail2Ban, actividad bot, errores sistema, reinicios |
| 📦 **APT** | Paquetes actualizables, actualizar, limpiar, historial |
| 🐳 **Docker** | Listar, start/stop/restart, logs, stats, imágenes, log servicio, prune |
| 📂 **Archivos** | Explorador, previsualizar, subir, descargar, borrar, carpetas, buscar, ocultos |
| 💻 **Terminal** | Terminal interactiva directa desde el menú principal |
| ⚙️ **Sistema** | Interfaces de red, cronjobs, servicios systemd, calculadora de espacio |
| 🛠️ **Avanzado** | Comandos favoritos, notas, WOL, scripts .sh, silenciar alertas, umbrales, reboot, shutdown |
| ⚠️ **Alertas** | Recursos, servicios caídos, contenedores caídos, intentos de login, resumen diario 08:00 |

---

## Arquitectura y funcionamiento

### Flujo general

```
Tu móvil → Servidores Telegram → Raspberry Pi (polling)
                                       │
                              python-telegram-bot
                                       │
                          ┌────────────┴────────────┐
                     Handlers                   Job Queue
                  (botones y texto)         (monitores cada Xmin)
                          │                        │
                    subprocess / psutil      subprocess / psutil
                          │                        │
                    Sistema operativo        Alertas proactivas
```

### Flujo de autenticación

```
/start recibido
    │
    ├─ ID desconocido ──→ silencio + alerta "Intento de acceso: ID XXXXX"
    │
    └─ ID correcto (MI_USUARIO_ID)
           │
           ├─ Bot bloqueado (3 fallos) ──→ "Espera X minutos"
           │
           ├─ Sesión activa y no expirada ──→ Panel principal
           │
           └─ Sin sesión válida ──→ "Introduce PIN:"
                    │
                    ├─ Correcto ──→ sesión 4h ──→ Panel principal
                    ├─ Fallo 1-2 ──→ "X intentos restantes"
                    └─ Fallo 3 ──→ bloqueo 15min + alerta al propietario
```

### Estados de modo (context.user_data['mode'])

El bot usa un sistema de estados para gestionar la entrada de texto:

| Modo | Acción esperada |
|------|----------------|
| `pin` | Introducir el PIN de acceso |
| `terminal` | Ejecutar el texto como comando shell |
| `upload` | Enviar documento para guardarlo |
| `unban_ip` | Escribir IP a desbanear |
| `geoip` | Escribir IP a consultar |
| `ping` | Escribir host para ping/traceroute |
| `mkdir` | Nombre de carpeta nueva |
| `file_search` | Término de búsqueda de archivos |
| `espacio` | Ruta para calcular espacio |
| `notas_add` | Texto de la nota a guardar |
| `None` | Informa de usar /start |

### Sistema de caché de paths

Los `callback_data` de Telegram tienen límite de 64 bytes. Para el explorador de archivos se usa el diccionario `_PATH_CACHE` en memoria que mapea un hash MD5 de 12 caracteres al path completo.

---

## Capturas de pantalla

### Pantalla de PIN

```
🔐 RAB v4.0 — Acceso protegido

Introduce el PIN de acceso:
```

```
❌ PIN incorrecto. 2 intento(s) restante(s):
```

```
🔒 Demasiados intentos fallidos.
Bot bloqueado 15 minutos.
```

### Menú principal

```
🏠 Panel de Control RAB v4.0
15/01/2025 09:14

┌─────────────────┬──────────────────┐
│  📊 Dashboard   │  🐳 Docker       │
├─────────────────┼──────────────────┤
│  📂 Archivos    │  💻 Terminal     │
├─────────────────┼──────────────────┤
│  🛡️ Red/Seg    │  📦 APT          │
├─────────────────┼──────────────────┤
│  ⚙️ Sistema    │  🛠️ Avanzado     │
└─────────────────┴──────────────────┘
```

### Dashboard en vivo

```
📊 Dashboard del Sistema

🟢 CPU:    8%
🟢 RAM:    54%  (554MB/1024MB)
🟢 Disco:  38%  (12GB/32GB)
🟢 Temp:   48.1C
🟢 VPN:    activa
📶 IP:     192.168.0.42
⏱️ Uptime:  12d 4h 22m
⚖️ Carga:   0.12 / 0.15 / 0.10

        [⬅️ Volver]
```

### Métricas históricas

```
📈 Métricas Históricas

🖥️ CPU   ▁▁▂▁▁▃▂▁▁▂▄▃▂▁▁▂▁▁▃▂▁▁▂▁  8%
🧠 RAM   ▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄ 54%
💾 Disco ▃▃▃▃▃▃▃▃▃▃▃▃▃▃▃▃▃▃▃▃▃▃▃▃ 38%
🌡️ Temp  ▃▃▃▃▄▃▃▃▃▃▃▃▄▄▃▃▃▃▃▃▃▃▃▃ 48.1C

Última: 09:10
```

### Uso de red en tiempo real

```
🌐 Uso de Red en Tiempo Real

*eth0*
  ⬇️ 42.3 KB/s  (total: 1842MB recibidos)
  ⬆️ 8.1 KB/s   (total: 312MB enviados)

*wlan0*
  ⬇️ 0.0 KB/s   (total: 0MB)
  ⬆️ 0.0 KB/s   (total: 0MB)
```

### Procesos zombies

```
☠️ Procesos Zombies:

PID 1842 `defunct` (ppid:1)
PID 2301 `sh` (ppid:1842)

[💀 Kill 1842 (defunct)  ]
[💀 Kill 2301 (sh)       ]
[⬅️ Volver              ]
```

```
⚠️ Matar proceso PID 1842?

[💀 Si, kill 1842]  [❌ Cancelar]
```

### Red/Seg — menú completo

```
🛡️ Red y Seguridad

─── 🌐 RED ─────────────────────────
[🚀 Test velocidad              ]
[🟢 VPN — Encender              ]  ← dinámico
[🛰️ WireGuard peers             ]
[📡 Ping / Traceroute           ]
[🔍 Escaneo LAN                 ]
[🔌 Puertos abiertos            ]
[🌍 Geo-IP                      ]

─── 🔒 SEGURIDAD ────────────────────
[🚨 Jails Fail2Ban              ]
[🚫 IPs baneadas                ]
[🔓 Desbanear IP                ]

─── 📋 LOGS ─────────────────────────
[🔑 SSH logins OK] [❌ SSH fallidos]
[🛰️ Log VPN      ] [🛡️ Log Fail2Ban]
[🤖 Actividad bot] [💥 Errores sist]
[🔄 Log reinicios               ]
[⬅️ Volver                     ]
```

### Avanzado — menú

```
🛠️ Herramientas Avanzadas

─── ⚡ HERRAMIENTAS ─────────────────
[📌 Comandos favoritos          ]
[📝 Notas rápidas               ]
[🪄 Wake-on-LAN                 ]
[📜 Ejecutar .sh                ]

─── 🔔 ALERTAS ──────────────────────
[🔕 Silenciar alertas           ]
[⚙️ Ajustar umbrales            ]

─── ⚠️ PELIGROSO ────────────────────
[🔄 Reiniciar sistema           ]
[⏻ Apagar sistema               ]
[⬅️ Volver                     ]
```

### Umbrales editables desde el bot

```
⚙️ Umbrales de Alerta (±5 por pulsación)

[🖥️ CPU: 80%  ] [➖] [➕]
[🧠 RAM: 85%  ] [➖] [➕]
[💾 Disco: 90%] [➖] [➕]
[🌡️ Temp: 70C ] [➖] [➕]
[⬅️ Volver    ]
```

### Silenciar alertas

```
🔔 Control de Alertas

🔔 Alertas activas

[🔕 Silenciar 1h          ]
[🔕 Silenciar 4h          ]
[🔕 Silenciar indefinido  ]
[🔔 Reactivar ahora       ]
[⬅️ Volver               ]
```

### Alertas proactivas automáticas

```
⚠️ ALERTA DE RECURSOS

🔴 CPU al 87% (umbral 80%)
🔴 RAM al 91% (umbral 85%)
```

```
🚨 Intento de acceso al bot
ID: `987654321`
Nombre: John Doe
```

```
☀️ Resumen Diario — 15/01/2025 08:00

🖥️ CPU:      8%
🧠 RAM:      51% (522MB/1024MB)
💾 Disco:    38%
🌡️ Temp:     46.1C
🌐 IP local: 192.168.0.42
⏱️ Uptime:   12d 4h 0m
🐳 Docker:   4 corriendo: portainer, nginx, vaultwarden, syncthing
```

---

## Requisitos del sistema

### Paquetes APT

```bash
sudo apt update && sudo apt install -y \
    python3 python3-pip python3-venv \
    wireguard fail2ban \
    speedtest-cli wakeonlan \
    nmap traceroute \
    iproute2 procps
```

### Docker

Instalar siguiendo la [guía oficial para Raspberry Pi](https://docs.docker.com/engine/install/raspberry-pi-os/) y añadir el usuario al grupo:

```bash
sudo usermod -aG docker $USER
# Cerrar sesión y volver a entrar para que tenga efecto
newgrp docker
```

---

## Instalación paso a paso

### 1. Obtener credenciales de Telegram

**Token del bot:**
1. Busca **@BotFather** en Telegram
2. Envía `/newbot` y sigue las instrucciones
3. Guarda el token (formato: `123456789:ABCdef...`)

**Tu ID de usuario:**
1. Busca **@userinfobot** en Telegram
2. Envía `/start` — te responde con tu `Id` numérico

### 2. Clonar el repositorio

```bash
git clone https://gitea.tudominio.com/$USER/rab-bot.git /home/$USER/tgbot
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

# Desactivarlo
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

```bash
nano /home/$USER/tgbot/bot_control.py
```

### Variables obligatorias

```python
TOKEN         = "123456789:ABCdefGHIjklMNOpqrSTUvwxYZ"  # de @BotFather
MI_USUARIO_ID = 123456789     # tu ID numérico de Telegram
PIN_SECRETO   = "000000"      # ← CAMBIA ESTO antes de arrancar
```

### Variables opcionales pero recomendadas

```python
ROOT_DIR      = "/home/$USER"    # raíz del explorador de archivos
WG_INTERFACE  = "wg0"            # nombre de la interfaz WireGuard
LAN_RED       = "192.168.0.0/24" # red local para el escaneo nmap

PIN_TTL_MIN      = 240  # minutos de sesión activa (4h por defecto)
PIN_MAX_INTENTOS = 3    # intentos antes de bloqueo
PIN_BLOQUEO_MIN  = 15   # minutos de bloqueo tras N fallos

# Equipos para Wake-on-LAN
WOL_MACS = {
    "PC-Principal": "AA:BB:CC:DD:EE:FF",
    "NAS":          "11:22:33:44:55:66",
}

# Umbrales de alerta (también ajustables desde el bot en tiempo real)
UMBRALES = {
    "cpu":  80,
    "ram":  85,
    "disk": 90,
    "temp": 70,
}

# Servicios vigilados cada 2 minutos
SERVICIOS_WATCH = ["docker", "wg-quick@wg0", "fail2ban", "ssh"]

# Comandos favoritos — personaliza a tu gusto
CMDS_FAVORITOS = {
    "📊 Estado servicios": "systemctl status docker fail2ban ssh --no-pager | head -40",
    "💾 Espacio disco":    "df -h",
    "🧠 Memoria":          "free -h",
    "🌡️ Temperatura":     "vcgencmd measure_temp",
    "📋 Últimos errores":  "journalctl -p err -n 20 --no-pager",
    "🐳 Docker ps":        "docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'",
}
```

### Mantener token y PIN fuera del repositorio (recomendado)

Crea el fichero `.env` (excluido en `.gitignore`):

```bash
cat > /home/$USER/tgbot/.env << EOF
RAB_TOKEN=123456789:ABCdef...
RAB_PIN=tu_pin_secreto
EOF
chmod 600 /home/$USER/tgbot/.env
```

En `bot_control.py`, sustituye las líneas de TOKEN y PIN por:

```python
import os
TOKEN       = os.environ.get("RAB_TOKEN", "")
PIN_SECRETO = os.environ.get("RAB_PIN", "000000")
```

Añade en el `[Service]` del `.service`:

```ini
EnvironmentFile=/home/$USER/tgbot/.env
```

---

## Permisos sudo

El bot necesita ejecutar comandos privilegiados. Concede permisos específicos con `visudo` — **nunca acceso total a sudo**:

```bash
sudo visudo
```

Añade al final (verifica rutas con `which <comando>`):

```
$USER ALL=(ALL) NOPASSWD: /usr/bin/wg, \
    /usr/bin/fail2ban-client, \
    /usr/sbin/fail2ban-client, \
    /usr/sbin/shutdown, \
    /usr/sbin/reboot, \
    /usr/bin/apt-get, \
    /usr/bin/journalctl, \
    /usr/bin/tail, \
    /usr/bin/systemctl
```

Verificar que funciona sin contraseña:

```bash
sudo -n wg show
sudo -n journalctl -p err -n 5 --no-pager
sudo -n systemctl start wg-quick@wg0
```

---

## Servicio systemd

### Fichero `rab-bot.service`

```ini
[Unit]
Description=RAB - Raspberry Admin Bot v4.0
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

### Instalar y activar

```bash
sudo cp /home/$USER/tgbot/rab-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable rab-bot
sudo systemctl start rab-bot

# Verificar que arranca
sudo systemctl status rab-bot
```

La salida debe mostrar `Active: active (running)`.

### Comandos de gestión

```bash
# Ver logs en tiempo real
sudo journalctl -u rab-bot -f

# Últimos 50 logs
sudo journalctl -u rab-bot -n 50

# Reiniciar tras modificar bot_control.py
sudo systemctl restart rab-bot

# Parar / Deshabilitar
sudo systemctl stop rab-bot
sudo systemctl disable rab-bot
```

---

## Descripción detallada de módulos

### Sistema de PIN y sesión

Usa `context.bot_data` (memoria compartida del proceso) para almacenar el estado de autenticación. Se pierde al reiniciar el bot — en ese caso simplemente hay que introducir el PIN de nuevo.

Variables en `bot_data`:
- `pin_validado` — bool de sesión activa
- `pin_timestamp` — datetime de última autenticación
- `pin_intentos` — contador de fallos consecutivos
- `pin_bloqueado_hasta` — datetime de desbloqueo (`None` si no hay bloqueo)

### `alertas_silenciadas()` — control de monitores

Helper que todos los monitores consultan antes de ejecutarse. Lee `bot_data['alertas_silenciadas_hasta']` y devuelve `True` si hay silencio activo, limpiando automáticamente el flag si ya expiró. Permite pausar todas las alertas sin reiniciar el bot.

### `exec_cmd()` — ejecutor seguro de comandos

Ejecuta cualquier comando del sistema vía `subprocess.run()`. Gestiona timeout configurable, captura de `stdout`/`stderr`, y devuelve siempre un string limpio o un mensaje de error descriptivo.

### `guardar_metrica()` y `leer_metricas_recientes()` — histórico

Cada 5 minutos se añade una línea al CSV con timestamp, CPU, RAM, disco y temperatura. `leer_metricas_recientes(n)` devuelve las últimas `n` mediciones para los sparklines.

### `sparkline()` — gráficas de bloques

Convierte una lista de valores en caracteres Unicode `▁▂▃▄▅▆▇█`. Normaliza automáticamente entre el mínimo y máximo del conjunto.

### `monitor_recursos()` — cada 5 minutos

Guarda métrica en CSV y comprueba umbrales. Los umbrales se leen del dict global `UMBRALES` modificable en tiempo de ejecución — por eso los cambios desde el bot tienen efecto inmediato sin reiniciar.

### `monitor_servicios()` — cada 2 minutos

Itera `SERVICIOS_WATCH` y ejecuta `systemctl is-active <servicio>`. Si alguno no está `active`, notifica inmediatamente. Respeta el silencio de alertas.

### `monitor_docker()` — cada 3 minutos

Comprueba con `docker ps -a` que todos los contenedores estén `Up`. Notifica si alguno está `Exited` o en error.

### `monitor_intentos_login()` — cada 10 minutos

Lee `/var/log/auth.log` incrementalmente usando un offset guardado en `bot_data`. Si detecta 5 o más líneas nuevas con `Failed password` o `Invalid user`, envía alerta con el resumen.

### VPN on/off

El botón del menú Red/Seg es dinámico — muestra el estado actual y la acción opuesta. Usa `systemctl start/stop wg-quick@wg0` vía sudo. El dashboard también muestra el estado de VPN en tiempo real.

### Kill de procesos zombies

Detecta procesos con `psutil.STATUS_ZOMBIE` y los lista. Cada uno tiene un botón de kill que ejecuta `os.kill(pid, SIGKILL)`. Pide confirmación antes de matar.

### Uso de red en tiempo real

Llama a `psutil.net_io_counters(pernic=True)` dos veces con 2 segundos de diferencia y calcula bytes/s por interfaz. Excluye la interfaz `lo` (loopback).

### Umbrales editables

Los botones `➖`/`➕` modifican directamente el dict `UMBRALES` en memoria, ajustando ±5 con límites entre 10 y 99. El efecto es inmediato en todos los monitores sin reiniciar el bot.

### Comandos favoritos

El dict `CMDS_FAVORITOS` mapea etiquetas a comandos shell. Al pulsar un botón, se ejecuta el comando correspondiente y se muestra el resultado. Personalizable en la sección de configuración del script.

### Notas rápidas

Se guardan en `NOTAS_FILE` (por defecto `/home/$USER/rab_notas.txt`) con timestamp. Se pueden añadir nuevas o borrar todas desde el bot.

---

## Alertas proactivas

El bot funciona de forma proactiva aunque el usuario no interactúe.

```python
jq.run_repeating(monitor_recursos,       interval=300, first=60)   # cada 5 min
jq.run_repeating(monitor_servicios,      interval=120, first=30)   # cada 2 min
jq.run_repeating(monitor_docker,         interval=180, first=45)   # cada 3 min
jq.run_repeating(monitor_intentos_login, interval=600, first=120)  # cada 10 min
jq.run_daily(resumen_diario, time=datetime.time(hour=8, minute=0)) # 08:00 diario
```

El parámetro `first` indica segundos de espera tras el arranque antes de la primera ejecución.

Todas las alertas respetan el flag de silencio — si están silenciadas, la función retorna inmediatamente sin ejecutar nada. `guardar_metrica()` se llama siempre aunque las alertas estén silenciadas, para no perder el histórico.

---

## Troubleshooting

### El bot no responde al /start

```bash
# Verificar que corre
sudo systemctl status rab-bot

# Ver errores
sudo journalctl -u rab-bot -n 50

# Probar el token
curl https://api.telegram.org/bot<TOKEN>/getMe
```

Si `getMe` devuelve `{"ok":true}` el token es correcto. Si no, regenera uno con @BotFather.

---

### Error: `ModuleNotFoundError: No module named 'telegram'`

El servicio no está usando el entorno virtual correcto.

```bash
# Verificar que el venv existe
ls /home/$USER/tgbot/venv/bin/python

# Verificar instalación
/home/$USER/tgbot/venv/bin/pip list | grep telegram

# Reinstalar si falta
/home/$USER/tgbot/venv/bin/pip install -r /home/$USER/tgbot/requirements.txt
```

Asegúrate de que `ExecStart` apunta a `venv/bin/python` y **no** a `/usr/bin/python3`.

---

### El bot pide PIN en cada mensaje

La sesión se almacena en `bot_data` en memoria. Si el bot se reinició, hay que volver a introducir el PIN — es el comportamiento esperado. Si se reinicia demasiado, comprueba los logs:

```bash
sudo journalctl -u rab-bot -f
```

---

### La VPN no se enciende/apaga desde el bot

```bash
# Verificar permiso sudo
sudo -n systemctl start wg-quick@wg0
sudo -n systemctl stop wg-quick@wg0

# Si pide contraseña, añadir /usr/bin/systemctl a visudo
# Verificar nombre de interfaz
ip link show | grep wg
```

Asegúrate de que `WG_INTERFACE` en el script coincide con el nombre real de tu interfaz (puede ser `wg0`, `wg1`, etc.).

---

### El escaneo LAN no encuentra dispositivos

```bash
# Verificar que nmap está instalado
which nmap

# Probar manualmente
sudo nmap -sn 192.168.0.0/24

# Si nmap no está disponible, instalar
sudo apt install nmap
```

Si nmap no está disponible, el bot hace fallback automático a `arp -a` o `ip neigh`, que solo muestra dispositivos con los que se ha comunicado recientemente.

---

### El test de velocidad falla o da timeout

```bash
# Verificar instalación
which speedtest-cli

# Probar manualmente
speedtest-cli --simple
```

El timeout está en 90 segundos. Si tu conexión es muy lenta, auméntalo en la línea:

```python
res = await exec_cmd("speedtest-cli --simple 2>&1", shell=True, timeout=90)
```

---

### Los comandos sudo devuelven error de contraseña

```bash
# Verificar configuración sudoers
sudo visudo -c

# Probar cada comando individualmente
sudo -n wg show
sudo -n fail2ban-client status
sudo -n journalctl -p err -n 5
```

Si alguno pide contraseña, revisa que la ruta en el bloque `NOPASSWD` sea exactamente la que devuelve `which <comando>`.

---

### Las alertas proactivas no llegan

```bash
# Verificar que job-queue está instalado
source /home/$USER/tgbot/venv/bin/activate
python -c "from telegram.ext import JobQueue; print('OK')"
deactivate
```

Si falla, reinstalar con el extra correcto:

```bash
source /home/$USER/tgbot/venv/bin/activate
pip install "python-telegram-bot[job-queue]==21.6"
deactivate
sudo systemctl restart rab-bot
```

También comprueba que las alertas no estén silenciadas — el Dashboard muestra `(alertas silenciadas)` si es el caso.

---

### MI_USUARIO_ID incorrecto — el bot no responde a nada

```bash
# Verificar tu ID real
# Busca @userinfobot en Telegram y envía /start
# El ID debe ser un número entero, no una cadena

MI_USUARIO_ID = 123456789    # correcto
MI_USUARIO_ID = "123456789"  # incorrecto
```

---

### Alto consumo de CPU del propio bot

`psutil.cpu_percent(interval=2)` bloquea 2 segundos por llamada. En Raspberry Pi 3 con carga alta puede ser notable. Para reducirlo, baja el intervalo a `interval=0.5` en los monitores o aumenta los intervalos de los jobs en la sección de inicio.

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
├── requirements.txt    # dependencias Python
├── rab-bot.service     # unidad systemd
└── README.md           # esta documentación

# Ficheros generados en tiempo de ejecución (no incluir en el repo):
/home/$USER/rab_actividad.log   # log CSV de acciones del bot
/home/$USER/rab_metricas.csv    # histórico de métricas del sistema
/home/$USER/rab_notas.txt       # notas guardadas desde el bot
```

---

## Seguridad

- El bot ignora cualquier mensaje de un ID distinto a `MI_USUARIO_ID` y te avisa
- Sin PIN validado ningún botón ni comando funciona
- La sesión expira automáticamente tras `PIN_TTL_MIN` minutos (por defecto 4h)
- Tras 3 intentos de PIN fallidos el bot se bloquea 15 minutos y te notifica
- Las operaciones destructivas (borrar, parar contenedores, reboot, shutdown) requieren confirmación
- Los permisos sudo están acotados a comandos específicos, sin acceso root total
- Se recomienda guardar token y PIN en `.env` excluido del repositorio con `.gitignore`

---

## Licencia

Uso personal. Sin garantías.
