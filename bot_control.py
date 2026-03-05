#!/usr/bin/env python3
# ==============================================================================
# RAB - Raspberry Admin Bot v4.0
# ==============================================================================

import logging
import subprocess
import os
import re
import csv
import json
import datetime
import glob
import shutil
import signal
import time
import psutil
import pytz
import hashlib
import urllib.request

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes
)

# ==============================================================================
# CONFIGURACION — edita esto
# ==============================================================================
TOKEN           = "TU_TOKEN_AQUI"
MI_USUARIO_ID   = 123456789
ROOT_DIR        = "/home/ruben"
ZONA_HORARIA    = pytz.timezone("Europe/Madrid")
LOG_ACTIVIDAD   = "/home/ruben/rab_actividad.log"
METRICAS_CSV    = "/home/ruben/rab_metricas.csv"
NOTAS_FILE      = "/home/ruben/rab_notas.txt"
WG_INTERFACE    = "wg0"
LAN_RED         = "192.168.0.0/24"

WOL_MACS = {
    "PC-Principal": "AA:BB:CC:DD:EE:FF",
    "NAS":          "11:22:33:44:55:66",
}

UMBRALES = {"cpu": 80, "ram": 85, "disk": 90, "temp": 70}

SERVICIOS_WATCH = ["docker", "wg-quick@wg0", "fail2ban", "ssh"]

# Seguridad
PIN_SECRETO      = "000000"   # <-- cambialo
PIN_TTL_MIN      = 240        # minutos de sesion activa
PIN_MAX_INTENTOS = 3
PIN_BLOQUEO_MIN  = 15

# Comandos favoritos  nombre -> comando shell
CMDS_FAVORITOS = {
    "\U0001f4ca Estado servicios": "systemctl status docker fail2ban ssh --no-pager | head -40",
    "\U0001f4be Espacio disco":    "df -h",
    "\U0001f9e0 Memoria":          "free -h",
    "\U0001f321\ufe0f Temperatura": "vcgencmd measure_temp 2>/dev/null || cat /sys/class/thermal/thermal_zone0/temp",
    "\U0001f4cb Ultimos errores":  "journalctl -p err -n 20 --no-pager",
    "\U0001f433 Docker ps":        "docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'",
}

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

_PATH_CACHE: dict = {}


def path_a_key(path: str) -> str:
    key = hashlib.md5(path.encode()).hexdigest()[:12]
    _PATH_CACHE[key] = path
    return key


def key_a_path(key: str) -> str:
    return _PATH_CACHE.get(key, ROOT_DIR)


# ==============================================================================
# UTILIDADES
# ==============================================================================

async def exec_cmd(comando, shell=False, timeout=45) -> str:
    try:
        res = subprocess.run(
            comando, capture_output=True, text=True,
            shell=shell, timeout=timeout
        )
        out = res.stdout.strip()
        err = res.stderr.strip()
        if res.returncode != 0:
            return f"Error (rc={res.returncode}):\n{err or out}"
        return out if out else "(sin salida)"
    except subprocess.TimeoutExpired:
        return "\u23f1\ufe0f Tiempo de espera agotado"
    except Exception as e:
        return f"\u274c Error critico: {e}"


def get_temperatura() -> float:
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return round(int(f.read().strip()) / 1000, 1)
    except Exception:
        try:
            out = subprocess.check_output(["vcgencmd", "measure_temp"], text=True)
            return float(re.search(r"[\d.]+", out).group())
        except Exception:
            return 0.0


def get_ip_local() -> str:
    try:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "Desconocida"


def get_ip_publica() -> str:
    try:
        with urllib.request.urlopen("https://api.ipify.org", timeout=5) as r:
            return r.read().decode()
    except Exception:
        return "No disponible"


def registrar_actividad(usuario: str, accion: str):
    try:
        ahora = datetime.datetime.now(ZONA_HORARIA).strftime("%Y-%m-%d %H:%M:%S")
        with open(LOG_ACTIVIDAD, "a", newline="") as f:
            csv.writer(f).writerow([ahora, usuario, accion])
    except Exception:
        pass


def guardar_metrica():
    try:
        cpu  = psutil.cpu_percent(interval=1)
        ram  = psutil.virtual_memory().percent
        disk = psutil.disk_usage("/").percent
        temp = get_temperatura()
        ahora = datetime.datetime.now(ZONA_HORARIA).strftime("%Y-%m-%d %H:%M:%S")
        with open(METRICAS_CSV, "a", newline="") as f:
            csv.writer(f).writerow([ahora, cpu, ram, disk, temp])
    except Exception:
        pass


def sparkline(valores: list) -> str:
    chars = ["\u2581", "\u2582", "\u2583", "\u2584", "\u2585", "\u2586", "\u2587", "\u2588"]
    if not valores:
        return ""
    mn, mx = min(valores), max(valores)
    rng = mx - mn or 1
    return "".join(chars[int((v - mn) / rng * 7)] for v in valores)


def leer_metricas_recientes(n=24) -> dict:
    res = {"cpu": [], "ram": [], "disk": [], "temp": [], "ts": []}
    try:
        if not os.path.exists(METRICAS_CSV):
            return res
        with open(METRICAS_CSV) as f:
            rows = list(csv.reader(f))[-n:]
        for row in rows:
            if len(row) >= 5:
                res["ts"].append(row[0][-5:])
                res["cpu"].append(float(row[1]))
                res["ram"].append(float(row[2]))
                res["disk"].append(float(row[3]))
                res["temp"].append(float(row[4]))
    except Exception:
        pass
    return res


def salud_sd() -> str:
    lineas = []
    try:
        with open("/sys/block/mmcblk0/stat") as f:
            campos = f.read().split()
        lineas.append(f"\U0001f4d6 Lecturas totales:   {int(campos[0]):,}")
        lineas.append(f"\u270f\ufe0f  Escrituras totales: {int(campos[4]):,}")
    except Exception:
        lineas.append("\u26a0\ufe0f No se pudo leer estadisticas del bloque")
    try:
        uso = psutil.disk_usage("/")
        ic  = "\U0001f7e2" if uso.percent < 70 else "\U0001f7e1" if uso.percent < 85 else "\U0001f534"
        lineas.append(f"{ic} Disco raiz: {uso.percent}% ({uso.used//1024**3}GB/{uso.total//1024**3}GB)")
    except Exception:
        pass
    temp = get_temperatura()
    ic   = "\U0001f7e2" if temp < 55 else "\U0001f7e1" if temp < 70 else "\U0001f534"
    lineas.append(f"{ic} Temperatura SoC: {temp}C")
    try:
        uptime_sec = int(open("/proc/uptime").read().split()[0].split(".")[0])
        d, r = divmod(uptime_sec, 86400)
        h    = r // 3600
        m    = (r % 3600) // 60
        lineas.append(f"\u23f1\ufe0f  Uptime: {d}d {h}h {m}m")
    except Exception:
        pass
    return "\n".join(lineas)


def alertas_silenciadas(context) -> bool:
    hasta = context.bot_data.get("alertas_silenciadas_hasta")
    if hasta is None:
        return False
    if datetime.datetime.now() < hasta:
        return True
    context.bot_data["alertas_silenciadas_hasta"] = None
    return False


# ==============================================================================
# SEGURIDAD — PIN DE SESION
# ==============================================================================

def sesion_valida(context) -> bool:
    if not context.bot_data.get("pin_validado", False):
        return False
    ts = context.bot_data.get("pin_timestamp")
    if ts is None:
        return False
    elapsed = (datetime.datetime.now() - ts).total_seconds() / 60
    if elapsed > PIN_TTL_MIN:
        context.bot_data["pin_validado"] = False
        return False
    return True


def pin_bloqueado(context):
    hasta = context.bot_data.get("pin_bloqueado_hasta")
    if hasta and datetime.datetime.now() < hasta:
        return hasta
    return None


# ==============================================================================
# MONITORES PROACTIVOS
# ==============================================================================

async def monitor_recursos(context: ContextTypes.DEFAULT_TYPE):
    guardar_metrica()
    if alertas_silenciadas(context):
        return
    alertas = []
    cpu  = psutil.cpu_percent(interval=2)
    ram  = psutil.virtual_memory().percent
    disk = psutil.disk_usage("/").percent
    temp = get_temperatura()
    if cpu  > UMBRALES["cpu"]:  alertas.append(f"\U0001f534 CPU al {cpu}% (umbral {UMBRALES['cpu']}%)")
    if ram  > UMBRALES["ram"]:  alertas.append(f"\U0001f534 RAM al {ram}% (umbral {UMBRALES['ram']}%)")
    if disk > UMBRALES["disk"]: alertas.append(f"\U0001f534 Disco al {disk}% (umbral {UMBRALES['disk']}%)")
    if temp > UMBRALES["temp"]: alertas.append(f"\U0001f321\ufe0f Temperatura {temp}C (umbral {UMBRALES['temp']}C)")
    if alertas:
        await context.bot.send_message(
            chat_id=MI_USUARIO_ID,
            text="\u26a0\ufe0f *ALERTA DE RECURSOS*\n\n" + "\n".join(alertas),
            parse_mode='Markdown'
        )


async def monitor_servicios(context: ContextTypes.DEFAULT_TYPE):
    if alertas_silenciadas(context):
        return
    caidos = []
    for srv in SERVICIOS_WATCH:
        res = subprocess.run(["systemctl", "is-active", srv], capture_output=True, text=True)
        if res.stdout.strip() != "active":
            caidos.append(srv)
    if caidos:
        await context.bot.send_message(
            chat_id=MI_USUARIO_ID,
            text="\U0001f6a8 *SERVICIOS CAIDOS*\n\n" + "\n".join(f"- `{s}`" for s in caidos),
            parse_mode='Markdown'
        )


async def monitor_docker(context: ContextTypes.DEFAULT_TYPE):
    if alertas_silenciadas(context):
        return
    try:
        res = subprocess.run(
            ["docker", "ps", "-a", "--format", "{{.Names}}|{{.Status}}"],
            capture_output=True, text=True, timeout=15
        )
        problemas = []
        for linea in res.stdout.strip().splitlines():
            if "|" in linea:
                nombre, estado = linea.split("|", 1)
                if not estado.lower().startswith("up"):
                    problemas.append(f"- `{nombre}` -> {estado}")
        if problemas:
            await context.bot.send_message(
                chat_id=MI_USUARIO_ID,
                text="\U0001f433 *CONTENEDOR DOCKER CAIDO*\n\n" + "\n".join(problemas),
                parse_mode='Markdown'
            )
    except Exception:
        pass


async def monitor_intentos_login(context: ContextTypes.DEFAULT_TYPE):
    if alertas_silenciadas(context):
        return
    offset = context.bot_data.setdefault("auth_offset", 0)
    try:
        with open("/var/log/auth.log", errors="replace") as f:
            lineas = f.readlines()
        nuevas = lineas[offset:]
        context.bot_data["auth_offset"] = len(lineas)
        fallos = [l.strip() for l in nuevas if "Failed password" in l or "Invalid user" in l]
        if len(fallos) >= 5:
            resumen = "\n".join(fallos[-10:])
            await context.bot.send_message(
                chat_id=MI_USUARIO_ID,
                text=f"\U0001f510 *{len(fallos)} intentos de login fallidos*\n```\n{resumen[-2000:]}\n```",
                parse_mode='Markdown'
            )
    except Exception:
        pass


async def resumen_diario(context: ContextTypes.DEFAULT_TYPE):
    cpu  = psutil.cpu_percent(interval=2)
    ram  = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    temp = get_temperatura()
    try:
        dr = subprocess.run(["docker", "ps", "--format", "{{.Names}}"],
                            capture_output=True, text=True, timeout=10)
        contenedores = dr.stdout.strip().splitlines()
        docker_txt = f"{len(contenedores)} corriendo: " + ", ".join(contenedores[:5])
    except Exception:
        docker_txt = "No disponible"
    try:
        us = int(open("/proc/uptime").read().split()[0].split(".")[0])
        d, r = divmod(us, 86400)
        h    = r // 3600
        uptime_txt = f"{d}d {h}h"
    except Exception:
        uptime_txt = "?"
    ahora = datetime.datetime.now(ZONA_HORARIA).strftime("%d/%m/%Y %H:%M")
    await context.bot.send_message(
        chat_id=MI_USUARIO_ID,
        text=(
            f"\u2600\ufe0f *Resumen Diario \u2014 {ahora}*\n\n"
            f"\U0001f5a5\ufe0f CPU:      {cpu}%\n"
            f"\U0001f9e0 RAM:      {ram.percent}% ({ram.used//1024**2}MB/{ram.total//1024**2}MB)\n"
            f"\U0001f4be Disco:    {disk.percent}%\n"
            f"\U0001f321\ufe0f Temp:     {temp}C\n"
            f"\U0001f310 IP local: `{get_ip_local()}`\n"
            f"\u23f1\ufe0f Uptime:   {uptime_txt}\n"
            f"\U0001f433 Docker:   {docker_txt}"
        ),
        parse_mode='Markdown'
    )


async def anuncio_inicio(app):
    ahora = datetime.datetime.now(ZONA_HORARIA).strftime("%d/%m/%Y %H:%M:%S")
    await app.bot.send_message(
        chat_id=MI_USUARIO_ID,
        text=f"\U0001f680 *RAB v4.0 online* \u2014 {ahora}\nEscribe /start para abrir el panel.",
        parse_mode='Markdown'
    )


# ==============================================================================
# MENUS
# ==============================================================================

def menu_principal():
    kb = [
        [
            InlineKeyboardButton("\U0001f4ca Dashboard", callback_data='m_dashboard'),
            InlineKeyboardButton("\U0001f433 Docker",    callback_data='m_docker'),
        ],
        [
            InlineKeyboardButton("\U0001f4c2 Archivos",  callback_data=f'm_files_{path_a_key(ROOT_DIR)}_0'),
            InlineKeyboardButton("\U0001f4bb Terminal",  callback_data='m_term'),
        ],
        [
            InlineKeyboardButton("\U0001f6e1\ufe0f Red/Seg", callback_data='m_net'),
            InlineKeyboardButton("\U0001f4e6 APT",           callback_data='m_apt'),
        ],
        [
            InlineKeyboardButton("\u2699\ufe0f Sistema",     callback_data='m_sys'),
            InlineKeyboardButton("\U0001f6e0\ufe0f Avanzado", callback_data='m_adv'),
        ],
    ]
    return InlineKeyboardMarkup(kb)


def btn_volver(destino='menu_principal'):
    return [InlineKeyboardButton("\u2b05\ufe0f Volver", callback_data=destino)]


# ==============================================================================
# HANDLERS
# ==============================================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid != MI_USUARIO_ID:
        await context.bot.send_message(
            chat_id=MI_USUARIO_ID,
            text=f"\U0001f6a8 *Intento de acceso al bot*\nID: `{uid}`\nNombre: {update.effective_user.full_name}",
            parse_mode='Markdown'
        )
        return

    context.user_data.clear()
    registrar_actividad(str(uid), "start")

    bloq = pin_bloqueado(context)
    if bloq:
        resta = int((bloq - datetime.datetime.now()).total_seconds() / 60) + 1
        await update.message.reply_text(
            f"\U0001f512 *Bot bloqueado*\nDemasiados intentos fallidos.\nEspera {resta} minuto(s).",
            parse_mode='Markdown'
        )
        return

    if sesion_valida(context):
        ahora = datetime.datetime.now(ZONA_HORARIA).strftime("%d/%m/%Y %H:%M")
        await update.message.reply_text(
            f'\U0001f3e0 *Panel de Control RAB v4.0*\n_{ahora}_',
            reply_markup=menu_principal(),
            parse_mode='Markdown'
        )
        return

    context.bot_data.setdefault("pin_intentos", 0)
    context.user_data['mode'] = 'pin'
    await update.message.reply_text(
        "\U0001f510 *RAB v4.0 \u2014 Acceso protegido*\n\nIntroduce el PIN de acceso:",
        parse_mode='Markdown'
    )


async def router_botones(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != MI_USUARIO_ID:
        return

    if not sesion_valida(context):
        await query.answer("Sesion expirada")
        context.user_data['mode'] = 'pin'
        await query.edit_message_text(
            "\U0001f510 *Sesion expirada*\nIntroduce el PIN:",
            parse_mode='Markdown'
        )
        return

    await query.answer()
    data = query.data
    registrar_actividad(str(query.from_user.id), f"btn:{data[:40]}")

    # ── MENU PRINCIPAL ────────────────────────────────────────────────────────
    if data == 'menu_principal':
        context.user_data['mode'] = None
        ahora = datetime.datetime.now(ZONA_HORARIA).strftime("%d/%m/%Y %H:%M")
        await query.edit_message_text(
            f'\U0001f3e0 *Panel Principal*\n_{ahora}_',
            reply_markup=menu_principal(),
            parse_mode='Markdown'
        )

    elif data == 'noop':
        pass

    # ── TERMINAL DIRECTO ──────────────────────────────────────────────────────
    elif data == 'm_term':
        context.user_data['mode'] = 'terminal'
        context.user_data['term_history'] = []
        await query.edit_message_text(
            "\U0001f4bb *Terminal Activa*\nEnvia cualquier comando. Escribe `salir` para cerrar.",
            parse_mode='Markdown'
        )

    # ── DASHBOARD ─────────────────────────────────────────────────────────────
    elif data == 'm_dashboard':
        kb = [
            [InlineKeyboardButton("\U0001f4cb Dashboard en vivo",        callback_data='sys_dash')],
            [InlineKeyboardButton("\U0001f4c8 Metricas historicas",       callback_data='m_metrics')],
            [InlineKeyboardButton("\U0001f310 Uso de red en tiempo real", callback_data='sys_net_usage')],
            [InlineKeyboardButton("\U0001f51d Top procesos",              callback_data='sys_top')],
            [InlineKeyboardButton("\u2620\ufe0f Procesos zombies",        callback_data='sys_zombies')],
            [InlineKeyboardButton("\U0001f504 Historial reinicios",       callback_data='sys_reinicios')],
            [InlineKeyboardButton("\U0001f3e5 Salud SD/Sistema",          callback_data='sys_sd')],
            [InlineKeyboardButton("\U0001f4bf Montajes y discos",         callback_data='sys_mounts')],
            btn_volver()
        ]
        await query.edit_message_text(
            "\U0001f4ca *Dashboard*",
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode='Markdown'
        )

    elif data == 'sys_dash':
        cpu   = psutil.cpu_percent(interval=1)
        ram   = psutil.virtual_memory()
        disk  = psutil.disk_usage("/")
        temp  = get_temperatura()
        carga = os.getloadavg()
        try:
            us = int(open("/proc/uptime").read().split()[0].split(".")[0])
            d, r = divmod(us, 86400)
            h, r = divmod(r, 3600)
            m    = r // 60
            uptime_txt = f"{d}d {h}h {m}m"
        except Exception:
            uptime_txt = "?"

        def ic(v, u):
            return "\U0001f534" if v > u else "\U0001f7e2"

        wg_res = subprocess.run(
            ["systemctl", "is-active", f"wg-quick@{WG_INTERFACE}"],
            capture_output=True, text=True
        )
        vpn_activa = wg_res.stdout.strip() == "active"
        vpn_ic     = "\U0001f7e2" if vpn_activa else "\U0001f534"
        sil_txt    = " _(alertas silenciadas)_" if alertas_silenciadas(context) else ""

        txt = (
            f"\U0001f4ca *Dashboard del Sistema*{sil_txt}\n\n"
            f"{ic(cpu, UMBRALES['cpu'])} CPU:    {cpu}%\n"
            f"{ic(ram.percent, UMBRALES['ram'])} RAM:    {ram.percent}%  ({ram.used//1024**2}MB/{ram.total//1024**2}MB)\n"
            f"{ic(disk.percent, UMBRALES['disk'])} Disco:  {disk.percent}%  ({disk.used//1024**3}GB/{disk.total//1024**3}GB)\n"
            f"{ic(temp, UMBRALES['temp'])} Temp:   {temp}C\n"
            f"{vpn_ic} VPN:    {'activa' if vpn_activa else 'inactiva'}\n"
            f"\U0001f4f6 IP:     `{get_ip_local()}`\n"
            f"\u23f1\ufe0f Uptime:  {uptime_txt}\n"
            f"\u2696\ufe0f Carga:   {carga[0]:.2f} / {carga[1]:.2f} / {carga[2]:.2f}"
        )
        await query.edit_message_text(
            txt,
            reply_markup=InlineKeyboardMarkup([btn_volver('m_dashboard')]),
            parse_mode='Markdown'
        )

    elif data == 'm_metrics':
        datos = leer_metricas_recientes(24)
        if not datos["cpu"]:
            txt = "\U0001f4c8 *Metricas Historicas*\n\nAun no hay datos.\nEl bot registra metricas cada 5 minutos."
        else:
            txt = (
                "\U0001f4c8 *Metricas Historicas*\n\n"
                f"\U0001f5a5\ufe0f CPU   `{sparkline(datos['cpu'])}` {datos['cpu'][-1]:.0f}%\n"
                f"\U0001f9e0 RAM   `{sparkline(datos['ram'])}` {datos['ram'][-1]:.0f}%\n"
                f"\U0001f4be Disco `{sparkline(datos['disk'])}` {datos['disk'][-1]:.0f}%\n"
                f"\U0001f321\ufe0f Temp  `{sparkline(datos['temp'])}` {datos['temp'][-1]:.1f}C\n\n"
                f"_Ultima: {datos['ts'][-1]}_"
            )
        await query.edit_message_text(
            txt,
            reply_markup=InlineKeyboardMarkup([btn_volver('m_dashboard')]),
            parse_mode='Markdown'
        )

    elif data == 'sys_net_usage':
        await query.edit_message_text("\u23f3 Midiendo trafico de red (2s)...", parse_mode='Markdown')
        try:
            antes   = psutil.net_io_counters(pernic=True)
            time.sleep(2)
            despues = psutil.net_io_counters(pernic=True)
            lineas  = []
            for iface in antes:
                if iface == "lo":
                    continue
                rx = (despues[iface].bytes_recv - antes[iface].bytes_recv) / 2
                tx = (despues[iface].bytes_sent - antes[iface].bytes_sent) / 2
                rx_total = despues[iface].bytes_recv
                tx_total = despues[iface].bytes_sent
                lineas.append(
                    f"*{iface}*\n"
                    f"  \u2b07\ufe0f {rx/1024:.1f} KB/s  (total: {rx_total//1024**2}MB)\n"
                    f"  \u2b06\ufe0f {tx/1024:.1f} KB/s  (total: {tx_total//1024**2}MB)"
                )
            txt = "\U0001f310 *Uso de Red en Tiempo Real*\n\n" + "\n\n".join(lineas) if lineas else "\U0001f310 Sin interfaces detectadas"
        except Exception as e:
            txt = f"\u274c Error: {e}"
        await query.edit_message_text(
            txt,
            reply_markup=InlineKeyboardMarkup([btn_volver('m_dashboard')]),
            parse_mode='Markdown'
        )

    elif data == 'sys_top':
        procs = sorted(
            psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_percent']),
            key=lambda x: x.info['memory_percent'],
            reverse=True
        )[:15]
        lineas = [
            f"{p.info['name'][:14]:<14} {p.info['cpu_percent']:>5}%CPU {p.info['memory_percent']:>4.1f}%RAM"
            for p in procs
        ]
        await query.edit_message_text(
            "\U0001f51d *Top 15 Procesos (por RAM):*\n```\n" + "\n".join(lineas) + "\n```",
            reply_markup=InlineKeyboardMarkup([btn_volver('m_dashboard')]),
            parse_mode='Markdown'
        )

    elif data == 'sys_zombies':
        zombies = []
        for p in psutil.process_iter(['pid', 'name', 'status', 'ppid']):
            try:
                if p.info['status'] == psutil.STATUS_ZOMBIE:
                    zombies.append(p.info)
            except Exception:
                pass
        if not zombies:
            txt = "\u2705 No hay procesos zombies."
            kb  = [btn_volver('m_dashboard')]
        else:
            lineas = [f"PID {z['pid']} `{z['name']}` (ppid:{z['ppid']})" for z in zombies]
            txt    = "\u2620\ufe0f *Procesos Zombies:*\n\n" + "\n".join(lineas)
            kb     = []
            for z in zombies[:5]:
                kb.append([InlineKeyboardButton(
                    f"\U0001f480 Kill {z['pid']} ({z['name'][:12]})",
                    callback_data=f"kill_ask_{z['pid']}"
                )])
            kb.append(btn_volver('m_dashboard'))
        await query.edit_message_text(
            txt,
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode='Markdown'
        )

    elif data.startswith('kill_ask_'):
        pid = data[9:]
        kb  = [[
            InlineKeyboardButton(f"\U0001f480 Si, kill {pid}", callback_data=f"kill_go_{pid}"),
            InlineKeyboardButton("\u274c Cancelar",             callback_data='sys_zombies')
        ]]
        await query.edit_message_text(
            f"\u26a0\ufe0f *Matar proceso PID {pid}?*",
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode='Markdown'
        )

    elif data.startswith('kill_go_'):
        pid_int = int(data[8:])
        try:
            os.kill(pid_int, signal.SIGKILL)
            msg = f"\U0001f480 Proceso `{pid_int}` eliminado."
        except Exception as e:
            msg = f"\u274c Error al matar {pid_int}: {e}"
        await query.edit_message_text(
            msg,
            reply_markup=InlineKeyboardMarkup([btn_volver('m_dashboard')]),
            parse_mode='Markdown'
        )

    elif data == 'sys_reinicios':
        res = await exec_cmd("last reboot | head -20", shell=True)
        motivos = await exec_cmd(
            "journalctl -b -1 -n 5 --no-pager 2>/dev/null | tail -5 || echo 'Sin datos del boot anterior'",
            shell=True
        )
        await query.edit_message_text(
            f"\U0001f504 *Historial de Reinicios:*\n```\n{res}\n```\n"
            f"\U0001f4cb *Ultimas lineas boot anterior:*\n```\n{motivos[-800:]}\n```",
            reply_markup=InlineKeyboardMarkup([btn_volver('m_dashboard')]),
            parse_mode='Markdown'
        )

    elif data == 'sys_sd':
        await query.edit_message_text(
            f"\U0001f3e5 *Salud del Sistema*\n\n{salud_sd()}",
            reply_markup=InlineKeyboardMarkup([btn_volver('m_dashboard')]),
            parse_mode='Markdown'
        )

    elif data == 'sys_mounts':
        partes = psutil.disk_partitions()
        lineas = []
        for p in partes:
            try:
                u = psutil.disk_usage(p.mountpoint)
                lineas.append(
                    f"{p.mountpoint:<18} {u.percent:>3}%  "
                    f"{u.used//1024**2:>6}MB/{u.total//1024**2:>6}MB"
                )
            except Exception:
                lineas.append(f"{p.mountpoint:<18} sin acceso")
        await query.edit_message_text(
            "\U0001f4bf *Montajes y Discos:*\n```\n" + "\n".join(lineas) + "\n```",
            reply_markup=InlineKeyboardMarkup([btn_volver('m_dashboard')]),
            parse_mode='Markdown'
        )

    # ── RED Y SEGURIDAD ───────────────────────────────────────────────────────
    elif data == 'm_net':
        wg_res     = subprocess.run(["systemctl", "is-active", f"wg-quick@{WG_INTERFACE}"],
                                    capture_output=True, text=True)
        vpn_activa = wg_res.stdout.strip() == "active"
        vpn_label  = "\U0001f534 VPN \u2014 Apagar" if vpn_activa else "\U0001f7e2 VPN \u2014 Encender"
        kb = [
            [InlineKeyboardButton("\u2500\u2500 \U0001f310 RED \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500", callback_data='noop')],
            [InlineKeyboardButton("\U0001f680 Test velocidad",      callback_data='net_speed')],
            [InlineKeyboardButton(vpn_label,                        callback_data='net_vpn_toggle')],
            [InlineKeyboardButton("\U0001f6f0\ufe0f WireGuard peers", callback_data='net_wg_peers')],
            [InlineKeyboardButton("\U0001f4e1 Ping / Traceroute",   callback_data='net_ping_ask')],
            [InlineKeyboardButton("\U0001f50d Escaneo LAN",         callback_data='net_lanscan')],
            [InlineKeyboardButton("\U0001f50c Puertos abiertos",    callback_data='net_ports')],
            [InlineKeyboardButton("\U0001f30d Geo-IP",              callback_data='net_geoip_ask')],
            [InlineKeyboardButton("\u2500\u2500 \U0001f512 SEGURIDAD \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500", callback_data='noop')],
            [InlineKeyboardButton("\U0001f6a8 Jails Fail2Ban",      callback_data='net_jails')],
            [InlineKeyboardButton("\U0001f6ab IPs baneadas",        callback_data='net_banned')],
            [InlineKeyboardButton("\U0001f513 Desbanear IP",        callback_data='net_unban_ask')],
            [InlineKeyboardButton("\u2500\u2500 \U0001f4cb LOGS \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500", callback_data='noop')],
            [InlineKeyboardButton("\U0001f511 SSH logins OK",       callback_data='log_ssh_ok'),
             InlineKeyboardButton("\u274c SSH fallidos",            callback_data='log_ssh_fail')],
            [InlineKeyboardButton("\U0001f6f0\ufe0f Log VPN",       callback_data='log_vpn'),
             InlineKeyboardButton("\U0001f6e1\ufe0f Log Fail2Ban",  callback_data='log_f2b')],
            [InlineKeyboardButton("\U0001f916 Actividad bot",       callback_data='log_bot'),
             InlineKeyboardButton("\U0001f4a5 Errores sistema",     callback_data='log_errors')],
            [InlineKeyboardButton("\U0001f504 Log reinicios",       callback_data='log_reinicios')],
            btn_volver()
        ]
        await query.edit_message_text(
            "\U0001f6e1\ufe0f *Red y Seguridad*",
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode='Markdown'
        )

    elif data == 'net_speed':
        await query.edit_message_text("\u23f3 Test de velocidad en curso (~30s)...", parse_mode='Markdown')
        res = await exec_cmd("speedtest-cli --simple 2>&1", shell=True, timeout=90)
        await query.edit_message_text(
            f"\U0001f680 *Test de Velocidad:*\n```\n{res}\n```",
            reply_markup=InlineKeyboardMarkup([btn_volver('m_net')]),
            parse_mode='Markdown'
        )

    elif data == 'net_vpn_toggle':
        wg_res     = subprocess.run(["systemctl", "is-active", f"wg-quick@{WG_INTERFACE}"],
                                    capture_output=True, text=True)
        vpn_activa = wg_res.stdout.strip() == "active"
        if vpn_activa:
            kb = [[InlineKeyboardButton("\U0001f534 Si, apagar VPN", callback_data='net_vpn_off'),
                   InlineKeyboardButton("\u274c Cancelar",            callback_data='m_net')]]
            await query.edit_message_text(
                f"\u26a0\ufe0f *Apagar WireGuard ({WG_INTERFACE})?*\nPerderas la conexion VPN.",
                reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown'
            )
        else:
            kb = [[InlineKeyboardButton("\U0001f7e2 Si, encender VPN", callback_data='net_vpn_on'),
                   InlineKeyboardButton("\u274c Cancelar",              callback_data='m_net')]]
            await query.edit_message_text(
                f"\u26a0\ufe0f *Encender WireGuard ({WG_INTERFACE})?*",
                reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown'
            )

    elif data == 'net_vpn_on':
        res    = await exec_cmd(["sudo", "systemctl", "start", f"wg-quick@{WG_INTERFACE}"], timeout=15)
        status = await exec_cmd(["systemctl", "is-active", f"wg-quick@{WG_INTERFACE}"])
        ic     = "\U0001f7e2" if status.strip() == "active" else "\U0001f534"
        await query.edit_message_text(
            f"{ic} VPN *encendida*\n`{res or 'OK'}`",
            reply_markup=InlineKeyboardMarkup([btn_volver('m_net')]), parse_mode='Markdown'
        )

    elif data == 'net_vpn_off':
        res = await exec_cmd(["sudo", "systemctl", "stop", f"wg-quick@{WG_INTERFACE}"], timeout=15)
        await query.edit_message_text(
            f"\U0001f534 VPN *apagada*\n`{res or 'OK'}`",
            reply_markup=InlineKeyboardMarkup([btn_volver('m_net')]), parse_mode='Markdown'
        )

    elif data == 'net_wg_peers':
        res = await exec_cmd(["sudo", "wg", "show", "all"], timeout=10)
        await query.edit_message_text(
            f"\U0001f6f0\ufe0f *WireGuard Peers:*\n```\n{res[-3500:]}\n```",
            reply_markup=InlineKeyboardMarkup([btn_volver('m_net')]), parse_mode='Markdown'
        )

    elif data == 'net_ping_ask':
        context.user_data['mode'] = 'ping'
        await query.edit_message_text(
            "\U0001f4e1 *Ping / Traceroute*\nEscribe una IP o dominio (o 'cancelar'):\n\n_Ej: 8.8.8.8  o  google.com_",
            parse_mode='Markdown'
        )

    elif data == 'net_lanscan':
        await query.edit_message_text(f"\u23f3 Escaneando {LAN_RED}... (puede tardar ~30s)", parse_mode='Markdown')
        res = await exec_cmd(
            f"nmap -sn {LAN_RED} 2>&1 | grep -E 'report|MAC'",
            shell=True, timeout=60
        )
        if not res or res.startswith("Error"):
            res = await exec_cmd("arp -a 2>/dev/null || ip neigh", shell=True, timeout=15)
        await query.edit_message_text(
            f"\U0001f50d *Escaneo LAN ({LAN_RED}):*\n```\n{res[-3500:]}\n```",
            reply_markup=InlineKeyboardMarkup([btn_volver('m_net')]), parse_mode='Markdown'
        )

    elif data == 'net_ports':
        res = await exec_cmd("ss -tlnp | column -t", shell=True)
        await query.edit_message_text(
            f"\U0001f50c *Puertos en escucha:*\n```\n{res[-3500:]}\n```",
            reply_markup=InlineKeyboardMarkup([btn_volver('m_net')]), parse_mode='Markdown'
        )

    elif data == 'net_geoip_ask':
        context.user_data['mode'] = 'geoip'
        await query.edit_message_text(
            "\U0001f30d *Geo-IP:*\nEscribe la IP a consultar (o 'cancelar'):",
            parse_mode='Markdown'
        )

    elif data == 'net_jails':
        await query.edit_message_text("\u23f3 Consultando jails...", parse_mode='Markdown')
        status_raw = await exec_cmd(["sudo", "fail2ban-client", "status"])
        jails = []
        for linea in status_raw.splitlines():
            if "Jail list:" in linea:
                parte = linea.split("Jail list:")[1].strip()
                jails = [j.strip() for j in re.split(r'[,\s]+', parte) if j.strip()]
        if not jails:
            txt = f"\u26a0\ufe0f No se detectaron jails.\n```\n{status_raw}\n```"
        else:
            partes = [f"\U0001f6a8 *Fail2Ban \u2014 {len(jails)} jails:*\n"]
            for jail in jails:
                detalle = await exec_cmd(["sudo", "fail2ban-client", "status", jail])
                partes.append(f"\U0001f4cc *{jail}:*\n```\n{detalle}\n```\n")
            txt = "\n".join(partes)
        await query.edit_message_text(
            txt[:4000],
            reply_markup=InlineKeyboardMarkup([btn_volver('m_net')]), parse_mode='Markdown'
        )

    elif data == 'net_banned':
        res = await exec_cmd(
            "sudo fail2ban-client status | grep 'Jail list' | sed 's/.*://;s/,//g' | "
            "xargs -I{} sudo fail2ban-client status {} 2>/dev/null | grep 'Banned IP'",
            shell=True
        )
        await query.edit_message_text(
            f"\U0001f6ab *IPs Baneadas:*\n```\n{res[-3000:]}\n```",
            reply_markup=InlineKeyboardMarkup([btn_volver('m_net')]), parse_mode='Markdown'
        )

    elif data == 'net_unban_ask':
        context.user_data['mode'] = 'unban_ip'
        await query.edit_message_text(
            "\U0001f513 *Desbanear IP:*\nEscribe la IP (o 'cancelar'):",
            parse_mode='Markdown'
        )

    # ── LOGS ──────────────────────────────────────────────────────────────────
    elif data == 'log_ssh_ok':
        res = await exec_cmd("grep 'Accepted' /var/log/auth.log | tail -30", shell=True)
        await query.edit_message_text(
            f"\U0001f511 *SSH \u2014 Logins exitosos:*\n```\n{res[-3500:]}\n```",
            reply_markup=InlineKeyboardMarkup([btn_volver('m_net')]), parse_mode='Markdown'
        )

    elif data == 'log_ssh_fail':
        res = await exec_cmd(
            "grep -E 'Failed password|Invalid user' /var/log/auth.log | tail -30", shell=True
        )
        await query.edit_message_text(
            f"\u274c *SSH \u2014 Logins fallidos:*\n```\n{res[-3500:]}\n```",
            reply_markup=InlineKeyboardMarkup([btn_volver('m_net')]), parse_mode='Markdown'
        )

    elif data == 'log_vpn':
        res = await exec_cmd(
            f"sudo journalctl -u wg-quick@{WG_INTERFACE} -n 40 --no-pager", shell=True
        )
        await query.edit_message_text(
            f"\U0001f6f0\ufe0f *Log VPN (WireGuard):*\n```\n{res[-3500:]}\n```",
            reply_markup=InlineKeyboardMarkup([btn_volver('m_net')]), parse_mode='Markdown'
        )

    elif data == 'log_f2b':
        res = await exec_cmd("sudo journalctl -u fail2ban -n 40 --no-pager", shell=True)
        await query.edit_message_text(
            f"\U0001f6e1\ufe0f *Log Fail2Ban:*\n```\n{res[-3500:]}\n```",
            reply_markup=InlineKeyboardMarkup([btn_volver('m_net')]), parse_mode='Markdown'
        )

    elif data == 'log_bot':
        if not os.path.exists(LOG_ACTIVIDAD):
            txt = "\U0001f916 Sin registros todavia."
        else:
            with open(LOG_ACTIVIDAD) as f:
                lineas = f.readlines()[-40:]
            txt = "\U0001f916 *Actividad del bot (ultimas 40):*\n```\n" + "".join(lineas)[-3000:] + "\n```"
        await query.edit_message_text(
            txt, reply_markup=InlineKeyboardMarkup([btn_volver('m_net')]), parse_mode='Markdown'
        )

    elif data == 'log_errors':
        res = await exec_cmd("sudo journalctl -p err -n 40 --no-pager", shell=True)
        await query.edit_message_text(
            f"\U0001f4a5 *Errores del sistema:*\n```\n{res[-3500:]}\n```",
            reply_markup=InlineKeyboardMarkup([btn_volver('m_net')]), parse_mode='Markdown'
        )

    elif data == 'log_reinicios':
        res = await exec_cmd("last reboot | head -20", shell=True)
        await query.edit_message_text(
            f"\U0001f504 *Log de Reinicios:*\n```\n{res}\n```",
            reply_markup=InlineKeyboardMarkup([btn_volver('m_net')]), parse_mode='Markdown'
        )

    # ── SISTEMA ───────────────────────────────────────────────────────────────
    elif data == 'm_sys':
        kb = [
            [InlineKeyboardButton("\U0001f310 Interfaces de red",   callback_data='sys_net_info')],
            [InlineKeyboardButton("\U0001f4c5 Cronjobs",             callback_data='srv_cron')],
            [InlineKeyboardButton("\U0001f4dc Servicios systemd",    callback_data='srv_systemd')],
            [InlineKeyboardButton("\U0001f5c2\ufe0f Calculadora espacio", callback_data='sys_espacio')],
            btn_volver()
        ]
        await query.edit_message_text(
            "\u2699\ufe0f *Sistema*",
            reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown'
        )

    elif data == 'sys_net_info':
        ip_l = get_ip_local()
        ip_p = get_ip_publica()
        ifaces = []
        for iface, addrs in psutil.net_if_addrs().items():
            for a in addrs:
                if a.family.name == "AF_INET":
                    ifaces.append(f"  {iface:<10} {a.address}")
        txt = (
            "\U0001f310 *Informacion de Red*\n\n"
            f"\U0001f3e0 IP local:   `{ip_l}`\n"
            f"\U0001f30d IP publica: `{ip_p}`\n\n"
            "*Interfaces:*\n```\n" + "\n".join(ifaces) + "\n```"
        )
        await query.edit_message_text(
            txt, reply_markup=InlineKeyboardMarkup([btn_volver('m_sys')]), parse_mode='Markdown'
        )

    elif data == 'srv_cron':
        res = await exec_cmd(["crontab", "-l"])
        await query.edit_message_text(
            f"\U0001f4c5 *Cronjobs:*\n```\n{res[-3500:]}\n```",
            reply_markup=InlineKeyboardMarkup([btn_volver('m_sys')]), parse_mode='Markdown'
        )

    elif data == 'srv_systemd':
        res = await exec_cmd(
            "systemctl list-units --type=service --state=running --no-pager | head -40", shell=True
        )
        await query.edit_message_text(
            f"\U0001f4dc *Servicios activos:*\n```\n{res[-3500:]}\n```",
            reply_markup=InlineKeyboardMarkup([btn_volver('m_sys')]), parse_mode='Markdown'
        )

    elif data == 'sys_espacio':
        kb = [
            [InlineKeyboardButton("\U0001f4c1 Raiz /",          callback_data='espacio_root'),
             InlineKeyboardButton("\U0001f3e0 /home",            callback_data='espacio_home')],
            [InlineKeyboardButton("\U0001f433 /var/lib/docker",  callback_data='espacio_docker'),
             InlineKeyboardButton("\U0001f4dd Ruta personalizada", callback_data='espacio_ask')],
            btn_volver('m_sys')
        ]
        await query.edit_message_text(
            "\U0001f5c2\ufe0f *Calculadora de espacio*\nSelecciona que analizar:",
            reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown'
        )

    elif data in ('espacio_root', 'espacio_home', 'espacio_docker'):
        dirs = {'espacio_root': '/', 'espacio_home': '/home', 'espacio_docker': '/var/lib/docker'}
        d    = dirs[data]
        await query.edit_message_text(f"\u23f3 Analizando `{d}`...", parse_mode='Markdown')
        res  = await exec_cmd(f"du -sh {d}/* 2>/dev/null | sort -rh | head -20", shell=True, timeout=60)
        await query.edit_message_text(
            f"\U0001f5c2\ufe0f *Espacio en `{d}`:*\n```\n{res}\n```",
            reply_markup=InlineKeyboardMarkup([btn_volver('m_sys')]), parse_mode='Markdown'
        )

    elif data == 'espacio_ask':
        context.user_data['mode'] = 'espacio'
        await query.edit_message_text(
            "\U0001f5c2\ufe0f *Calculadora de espacio*\nEscribe la ruta (o 'cancelar'):",
            parse_mode='Markdown'
        )

    # ── APT ───────────────────────────────────────────────────────────────────
    elif data == 'm_apt':
        kb = [
            [InlineKeyboardButton("\U0001f4cb Paquetes actualizables", callback_data='apt_list')],
            [InlineKeyboardButton("\u2b06\ufe0f Actualizar todo",       callback_data='apt_upgrade_ask')],
            [InlineKeyboardButton("\U0001f9f9 Limpieza del sistema",    callback_data='apt_clean')],
            [InlineKeyboardButton("\U0001f4dc Historial APT",           callback_data='apt_history')],
            btn_volver()
        ]
        await query.edit_message_text(
            "\U0001f4e6 *APT / Mantenimiento*",
            reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown'
        )

    elif data == 'apt_list':
        await query.edit_message_text("\u23f3 Consultando paquetes...", parse_mode='Markdown')
        res = await exec_cmd("apt list --upgradable 2>/dev/null | grep -v 'Listing'", shell=True, timeout=60)
        if not res or res == "(sin salida)":
            res = "\u2705 No hay paquetes pendientes."
        await query.edit_message_text(
            f"\U0001f4cb *Paquetes actualizables:*\n```\n{res[-3500:]}\n```",
            reply_markup=InlineKeyboardMarkup([btn_volver('m_apt')]), parse_mode='Markdown'
        )

    elif data == 'apt_upgrade_ask':
        kb = [[InlineKeyboardButton("\u2705 Si, actualizar", callback_data='apt_upgrade_go'),
               InlineKeyboardButton("\u274c Cancelar",        callback_data='m_apt')]]
        await query.edit_message_text(
            "\u26a0\ufe0f *Actualizar todos los paquetes?*\nEjecutara `apt-get upgrade -y`.",
            reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown'
        )

    elif data == 'apt_upgrade_go':
        await query.edit_message_text("\u23f3 Actualizando... (puede tardar varios minutos)", parse_mode='Markdown')
        res = await exec_cmd(
            "sudo apt-get update -qq && sudo apt-get upgrade -y 2>&1 | tail -20",
            shell=True, timeout=300
        )
        await query.edit_message_text(
            f"\u2705 *Actualizacion completada:*\n```\n{res[-3500:]}\n```",
            reply_markup=InlineKeyboardMarkup([btn_volver('m_apt')]), parse_mode='Markdown'
        )

    elif data == 'apt_clean':
        await query.edit_message_text("\u23f3 Limpiando...", parse_mode='Markdown')
        antes = psutil.disk_usage("/").used
        res   = await exec_cmd(
            "sudo apt-get autoremove -y && sudo apt-get autoclean -y 2>&1 | tail -10",
            shell=True, timeout=120
        )
        liberado = (antes - psutil.disk_usage("/").used) // 1024**2
        await query.edit_message_text(
            f"\U0001f9f9 *Limpieza completada*\n\U0001f4be Espacio liberado: ~{liberado}MB\n```\n{res[-2000:]}\n```",
            reply_markup=InlineKeyboardMarkup([btn_volver('m_apt')]), parse_mode='Markdown'
        )

    elif data == 'apt_history':
        res = await exec_cmd(
            "grep 'install\\|upgrade\\|remove' /var/log/dpkg.log | tail -30", shell=True
        )
        await query.edit_message_text(
            f"\U0001f4dc *Historial APT:*\n```\n{res[-3500:]}\n```",
            reply_markup=InlineKeyboardMarkup([btn_volver('m_apt')]), parse_mode='Markdown'
        )

    # ── DOCKER ────────────────────────────────────────────────────────────────
    elif data == 'm_docker':
        kb = [
            [InlineKeyboardButton("\U0001f4cb Listar contenedores", callback_data='docker_list')],
            [InlineKeyboardButton("\U0001f4ca Stats recursos",       callback_data='docker_stats')],
            [InlineKeyboardButton("\U0001f5bc\ufe0f Imagenes",       callback_data='docker_images')],
            [InlineKeyboardButton("\U0001f4dc Log Docker servicio",  callback_data='log_docker_svc')],
            [InlineKeyboardButton("\U0001f9f9 Limpiar no usados",    callback_data='docker_prune_ask')],
            btn_volver()
        ]
        await query.edit_message_text(
            "\U0001f433 *Docker*",
            reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown'
        )

    elif data == 'docker_list':
        res = await exec_cmd('docker ps -a --format "{{.Names}}|{{.Status}}|{{.Image}}"', shell=True)
        lineas  = []
        nombres = []
        for linea in res.splitlines():
            partes = linea.split("|")
            if len(partes) >= 2:
                nombre, estado = partes[0], partes[1]
                nombres.append(nombre)
                ic = "\U0001f7e2" if estado.lower().startswith("up") else "\U0001f534"
                lineas.append(f"{ic} {nombre:<20} {estado[:20]}")
        kb_cont = []
        for n in nombres[:8]:
            kb_cont.append([
                InlineKeyboardButton(f"\U0001f4dc {n[:12]}", callback_data=f"dc_log_{n}"),
                InlineKeyboardButton("\u23f9",               callback_data=f"dc_stop_ask_{n}"),
                InlineKeyboardButton("\u25b6\ufe0f",         callback_data=f"dc_start_{n}"),
                InlineKeyboardButton("\U0001f504",           callback_data=f"dc_restart_ask_{n}"),
            ])
        kb_cont.append(btn_volver('m_docker'))
        await query.edit_message_text(
            "\U0001f433 *Contenedores:*\n```\n" + "\n".join(lineas) + "\n```",
            reply_markup=InlineKeyboardMarkup(kb_cont), parse_mode='Markdown'
        )

    elif data == 'docker_stats':
        res = await exec_cmd(
            "docker stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}'",
            shell=True, timeout=20
        )
        await query.edit_message_text(
            f"\U0001f4ca *Docker Stats:*\n```\n{res[-3500:]}\n```",
            reply_markup=InlineKeyboardMarkup([btn_volver('m_docker')]), parse_mode='Markdown'
        )

    elif data == 'docker_images':
        res = await exec_cmd(
            "docker images --format 'table {{.Repository}}\t{{.Tag}}\t{{.Size}}'", shell=True
        )
        await query.edit_message_text(
            f"\U0001f5bc\ufe0f *Imagenes Docker:*\n```\n{res[-3500:]}\n```",
            reply_markup=InlineKeyboardMarkup([btn_volver('m_docker')]), parse_mode='Markdown'
        )

    elif data == 'log_docker_svc':
        res = await exec_cmd("sudo journalctl -u docker -n 40 --no-pager", shell=True)
        await query.edit_message_text(
            f"\U0001f4dc *Log Docker (servicio):*\n```\n{res[-3500:]}\n```",
            reply_markup=InlineKeyboardMarkup([btn_volver('m_docker')]), parse_mode='Markdown'
        )

    elif data == 'docker_prune_ask':
        kb = [[InlineKeyboardButton("\u2705 Si, limpiar", callback_data='docker_prune_go'),
               InlineKeyboardButton("\u274c Cancelar",    callback_data='m_docker')]]
        await query.edit_message_text(
            "\u26a0\ufe0f *Eliminar contenedores/imagenes no usados?*",
            reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown'
        )

    elif data == 'docker_prune_go':
        res = await exec_cmd("docker system prune -f 2>&1", shell=True, timeout=60)
        await query.edit_message_text(
            f"\U0001f9f9 *Docker prune:*\n```\n{res[-3000:]}\n```",
            reply_markup=InlineKeyboardMarkup([btn_volver('m_docker')]), parse_mode='Markdown'
        )

    elif data.startswith('dc_log_'):
        nombre = data[7:]
        res = await exec_cmd(f"docker logs --tail 30 {nombre} 2>&1", shell=True, timeout=15)
        await query.edit_message_text(
            f"\U0001f4dc *Logs {nombre}:*\n```\n{res[-3500:]}\n```",
            reply_markup=InlineKeyboardMarkup([btn_volver('m_docker')]), parse_mode='Markdown'
        )

    elif data.startswith('dc_stop_ask_'):
        nombre = data[12:]
        kb = [[InlineKeyboardButton(f"\u23f9 Parar {nombre[:15]}", callback_data=f"dc_stop_{nombre}"),
               InlineKeyboardButton("\u274c Cancelar",              callback_data='docker_list')]]
        await query.edit_message_text(
            f"\u26a0\ufe0f *Parar `{nombre}`?*",
            reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown'
        )

    elif data.startswith('dc_stop_'):
        nombre = data[8:]
        res = await exec_cmd(f"docker stop {nombre}", shell=True, timeout=30)
        await query.edit_message_text(
            f"\u23f9 `{nombre}` detenido:\n`{res}`",
            reply_markup=InlineKeyboardMarkup([btn_volver('m_docker')]), parse_mode='Markdown'
        )

    elif data.startswith('dc_start_'):
        nombre = data[9:]
        res = await exec_cmd(f"docker start {nombre}", shell=True, timeout=30)
        await query.edit_message_text(
            f"\u25b6\ufe0f `{nombre}` iniciado:\n`{res}`",
            reply_markup=InlineKeyboardMarkup([btn_volver('m_docker')]), parse_mode='Markdown'
        )

    elif data.startswith('dc_restart_ask_'):
        nombre = data[15:]
        kb = [[InlineKeyboardButton(f"\U0001f504 Reiniciar {nombre[:12]}", callback_data=f"dc_restart_{nombre}"),
               InlineKeyboardButton("\u274c Cancelar",                      callback_data='docker_list')]]
        await query.edit_message_text(
            f"\u26a0\ufe0f *Reiniciar `{nombre}`?*",
            reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown'
        )

    elif data.startswith('dc_restart_'):
        nombre = data[11:]
        res = await exec_cmd(f"docker restart {nombre}", shell=True, timeout=30)
        await query.edit_message_text(
            f"\U0001f504 `{nombre}` reiniciado:\n`{res}`",
            reply_markup=InlineKeyboardMarkup([btn_volver('m_docker')]), parse_mode='Markdown'
        )

    # ── EXPLORADOR DE ARCHIVOS ────────────────────────────────────────────────
    elif data.startswith('m_files_') or data.startswith('path_'):
        partes = data.split('_')
        try:
            offset = int(partes[-1])
            key    = partes[-2]
        except (ValueError, IndexError):
            offset = 0
            key    = path_a_key(ROOT_DIR)
        path = key_a_path(key)
        context.user_data['current_path'] = path
        try:
            items = sorted(os.listdir(path))
            if not context.user_data.get('show_hidden', False):
                items = [f for f in items if not f.startswith('.')]
        except PermissionError:
            await query.edit_message_text(
                f"\u274c Sin permiso para leer `{path}`",
                reply_markup=InlineKeyboardMarkup([btn_volver()]), parse_mode='Markdown'
            )
            return
        total = len(items)
        chunk = items[offset:offset + 8]
        kb    = []
        if path != ROOT_DIR:
            pk = path_a_key(os.path.dirname(path))
            kb.append([InlineKeyboardButton("\U0001f4c1 \u2b06\ufe0f Subir nivel", callback_data=f"path_{pk}_0")])
        for item in chunk:
            full = os.path.join(path, item)
            if os.path.isdir(full):
                ck = path_a_key(full)
                kb.append([InlineKeyboardButton(f"\U0001f4c1 {item[:35]}", callback_data=f"path_{ck}_0")])
            else:
                fk = path_a_key(full)
                kb.append([InlineKeyboardButton(f"\U0001f4c4 {item[:35]}", callback_data=f"get_{fk}")])
        nav = []
        if offset > 0:
            nav.append(InlineKeyboardButton("\u25c0\ufe0f Ant", callback_data=f"m_files_{key}_{offset-8}"))
        if offset + 8 < total:
            nav.append(InlineKeyboardButton("Sig \u25b6\ufe0f", callback_data=f"m_files_{key}_{offset+8}"))
        if nav:
            kb.append(nav)
        sh = context.user_data.get('show_hidden', False)
        kb.append([
            InlineKeyboardButton("\U0001f4e4 Subir archivo", callback_data="file_upload"),
            InlineKeyboardButton("\U0001f4c1 Nueva carpeta", callback_data="file_mkdir"),
        ])
        kb.append([
            InlineKeyboardButton("\U0001f50d Buscar",                             callback_data="file_search_ask"),
            InlineKeyboardButton(f"\U0001f441\ufe0f Ocultos: {'ON' if sh else 'OFF'}", callback_data=f"file_toggle_{key}"),
        ])
        kb.append(btn_volver())
        await query.edit_message_text(
            f"\U0001f4cd `{path}`\n_{min(offset+1, total)}-{min(offset+8, total)} de {total}_",
            reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown'
        )

    elif data.startswith('file_toggle_'):
        key = data[12:]
        context.user_data['show_hidden'] = not context.user_data.get('show_hidden', False)
        query.data = f"m_files_{key}_0"
        await router_botones(update, context)
        return

    elif data == 'file_mkdir':
        context.user_data['mode'] = 'mkdir'
        await query.edit_message_text(
            "\U0001f4c1 *Nueva carpeta:*\nEscribe el nombre (o 'cancelar'):", parse_mode='Markdown'
        )

    elif data == 'file_search_ask':
        context.user_data['mode'] = 'file_search'
        await query.edit_message_text(
            "\U0001f50d *Buscar archivo:*\nEscribe nombre o parte (o 'cancelar'):", parse_mode='Markdown'
        )

    elif data == 'file_upload':
        context.user_data['mode'] = 'upload'
        await query.edit_message_text(
            "\U0001f4e4 *Subir archivo:*\nEnv\u00edame el documento para guardarlo aqu\u00ed.",
            parse_mode='Markdown'
        )

    elif data.startswith('get_'):
        fkey   = data[4:]
        f_path = key_a_path(fkey)
        if not os.path.isfile(f_path):
            await query.message.reply_text("\u274c Archivo no encontrado.")
            return
        size = os.path.getsize(f_path)
        if size > 50 * 1024 * 1024:
            await query.message.reply_text("\u26a0\ufe0f Archivo demasiado grande (>50MB).")
            return
        ext      = os.path.splitext(f_path)[1].lower()
        txt_exts = {'.log', '.txt', '.conf', '.yaml', '.yml', '.ini',
                    '.env', '.sh', '.py', '.json', '.xml', '.md'}
        kb_file  = [[
            InlineKeyboardButton("\U0001f4e5 Descargar", callback_data=f"dl_{fkey}"),
            InlineKeyboardButton("\U0001f5d1\ufe0f Borrar", callback_data=f"del_ask_{fkey}")
        ]]
        if ext in txt_exts and size < 50000:
            try:
                with open(f_path, errors='replace') as tf:
                    contenido = tf.read(3000)
                await query.message.reply_text(
                    f"\U0001f4c4 *{os.path.basename(f_path)}*\n```\n{contenido}\n```",
                    reply_markup=InlineKeyboardMarkup(kb_file), parse_mode='Markdown'
                )
                return
            except Exception:
                pass
        await query.message.reply_text(
            f"\U0001f4c4 `{os.path.basename(f_path)}` ({size//1024}KB)",
            reply_markup=InlineKeyboardMarkup(kb_file), parse_mode='Markdown'
        )

    elif data.startswith('dl_'):
        fkey   = data[3:]
        f_path = key_a_path(fkey)
        if os.path.isfile(f_path):
            await query.message.reply_document(document=open(f_path, 'rb'))
        else:
            await query.message.reply_text("\u274c Archivo no encontrado.")

    elif data.startswith('del_ask_'):
        fkey   = data[8:]
        f_path = key_a_path(fkey)
        kb = [[
            InlineKeyboardButton("\U0001f5d1\ufe0f Si, borrar", callback_data=f"del_go_{fkey}"),
            InlineKeyboardButton("\u274c Cancelar",              callback_data='menu_principal')
        ]]
        await query.edit_message_text(
            f"\u26a0\ufe0f *Borrar definitivamente?*\n`{f_path}`",
            reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown'
        )

    elif data.startswith('del_go_'):
        fkey   = data[7:]
        f_path = key_a_path(fkey)
        try:
            if os.path.isfile(f_path):
                os.remove(f_path)
                msg = f"\U0001f5d1\ufe0f Borrado: `{f_path}`"
            elif os.path.isdir(f_path):
                shutil.rmtree(f_path)
                msg = f"\U0001f5d1\ufe0f Directorio borrado: `{f_path}`"
            else:
                msg = "\u274c No encontrado."
        except Exception as e:
            msg = f"\u274c Error al borrar: {e}"
        await query.edit_message_text(msg, reply_markup=menu_principal(), parse_mode='Markdown')

    # ── AVANZADO ──────────────────────────────────────────────────────────────
    elif data == 'm_adv':
        kb = [
            [InlineKeyboardButton("\u2500\u2500 \u26a1 HERRAMIENTAS \u2500\u2500\u2500\u2500\u2500\u2500\u2500", callback_data='noop')],
            [InlineKeyboardButton("\U0001f4cc Comandos favoritos",     callback_data='adv_cmds')],
            [InlineKeyboardButton("\U0001f4dd Notas rapidas",          callback_data='adv_notas')],
            [InlineKeyboardButton("\U0001fa84 Wake-on-LAN",            callback_data='adv_wol')],
            [InlineKeyboardButton("\U0001f4dc Ejecutar .sh",           callback_data='adv_sh')],
            [InlineKeyboardButton("\u2500\u2500 \U0001f514 ALERTAS \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500", callback_data='noop')],
            [InlineKeyboardButton("\U0001f515 Silenciar alertas",      callback_data='adv_silenciar')],
            [InlineKeyboardButton("\u2699\ufe0f Ajustar umbrales",     callback_data='adv_umbrales')],
            [InlineKeyboardButton("\u2500\u2500 \u26a0\ufe0f PELIGROSO \u2500\u2500\u2500\u2500\u2500\u2500\u2500", callback_data='noop')],
            [InlineKeyboardButton("\U0001f504 Reiniciar sistema",      callback_data='adv_reboot_ask')],
            [InlineKeyboardButton("\u23fb Apagar sistema",             callback_data='adv_shutdown_ask')],
            btn_volver()
        ]
        await query.edit_message_text(
            "\U0001f6e0\ufe0f *Herramientas Avanzadas*",
            reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown'
        )

    elif data == 'adv_cmds':
        cmds_map = {hashlib.md5(n.encode()).hexdigest()[:8]: cmd for n, cmd in CMDS_FAVORITOS.items()}
        context.bot_data['cmds_map'] = cmds_map
        kb = [
            [InlineKeyboardButton(nombre, callback_data=f"cmd_{hashlib.md5(nombre.encode()).hexdigest()[:8]}")]
            for nombre in CMDS_FAVORITOS
        ]
        kb.append(btn_volver('m_adv'))
        await query.edit_message_text(
            "\U0001f4cc *Comandos Favoritos:*",
            reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown'
        )

    elif data.startswith('cmd_'):
        ckey = data[4:]
        cmd  = context.bot_data.get('cmds_map', {}).get(ckey, "echo 'Comando no encontrado'")
        res  = await exec_cmd(cmd, shell=True, timeout=30)
        await query.edit_message_text(
            f"\U0001f4cc *Resultado:*\n```\n{res[:3500]}\n```",
            reply_markup=InlineKeyboardMarkup([btn_volver('adv_cmds')]), parse_mode='Markdown'
        )

    elif data == 'adv_notas':
        notas = ""
        if os.path.exists(NOTAS_FILE):
            with open(NOTAS_FILE) as f:
                notas = f.read().strip()
        kb = [
            [InlineKeyboardButton("\u270f\ufe0f Anadir nota",  callback_data='notas_add')],
            [InlineKeyboardButton("\U0001f5d1\ufe0f Borrar notas", callback_data='notas_clear_ask')],
            btn_volver('m_adv')
        ]
        txt = f"\U0001f4dd *Notas Rapidas:*\n\n{notas}" if notas else "\U0001f4dd *Notas Rapidas:*\n\n_(vacio \u2014 usa Anadir nota)_"
        await query.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')

    elif data == 'notas_add':
        context.user_data['mode'] = 'notas_add'
        await query.edit_message_text(
            "\U0001f4dd *Anadir nota:*\nEscribe el texto (o 'cancelar'):", parse_mode='Markdown'
        )

    elif data == 'notas_clear_ask':
        kb = [[InlineKeyboardButton("\U0001f5d1\ufe0f Si, borrar todo", callback_data='notas_clear_go'),
               InlineKeyboardButton("\u274c Cancelar",                   callback_data='adv_notas')]]
        await query.edit_message_text(
            "\u26a0\ufe0f *Borrar todas las notas?*",
            reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown'
        )

    elif data == 'notas_clear_go':
        try:
            if os.path.exists(NOTAS_FILE):
                os.remove(NOTAS_FILE)
            msg = "\U0001f5d1\ufe0f Notas borradas."
        except Exception as e:
            msg = f"\u274c Error: {e}"
        await query.edit_message_text(
            msg, reply_markup=InlineKeyboardMarkup([btn_volver('m_adv')]), parse_mode='Markdown'
        )

    elif data == 'adv_silenciar':
        hasta = context.bot_data.get("alertas_silenciadas_hasta")
        if hasta and datetime.datetime.now() < hasta:
            resta  = int((hasta - datetime.datetime.now()).total_seconds() / 60)
            estado = f"\U0001f515 Alertas silenciadas \u2014 {resta} min restantes"
        else:
            estado = "\U0001f514 Alertas activas"
        kb = [
            [InlineKeyboardButton("\U0001f515 Silenciar 1h",         callback_data='sil_60')],
            [InlineKeyboardButton("\U0001f515 Silenciar 4h",         callback_data='sil_240')],
            [InlineKeyboardButton("\U0001f515 Silenciar indefinido", callback_data='sil_0')],
            [InlineKeyboardButton("\U0001f514 Reactivar ahora",      callback_data='sil_off')],
            btn_volver('m_adv')
        ]
        await query.edit_message_text(
            f"\U0001f514 *Control de Alertas*\n\n{estado}",
            reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown'
        )

    elif data.startswith('sil_'):
        mins = data[4:]
        if mins == 'off':
            context.bot_data['alertas_silenciadas_hasta'] = None
            msg = "\U0001f514 *Alertas reactivadas.*"
        elif mins == '0':
            context.bot_data['alertas_silenciadas_hasta'] = datetime.datetime(9999, 1, 1)
            msg = "\U0001f515 *Alertas silenciadas indefinidamente.*"
        else:
            m   = int(mins)
            context.bot_data['alertas_silenciadas_hasta'] = (
                datetime.datetime.now() + datetime.timedelta(minutes=m)
            )
            msg = f"\U0001f515 *Alertas silenciadas durante {m} minutos.*"
        await query.edit_message_text(
            msg, reply_markup=InlineKeyboardMarkup([btn_volver('m_adv')]), parse_mode='Markdown'
        )

    elif data == 'adv_umbrales':
        u  = UMBRALES
        kb = [
            [InlineKeyboardButton(f"\U0001f5a5\ufe0f CPU: {u['cpu']}%",   callback_data='noop'),
             InlineKeyboardButton("\u2796", callback_data='umb_cpu_down'),
             InlineKeyboardButton("\u2795", callback_data='umb_cpu_up')],
            [InlineKeyboardButton(f"\U0001f9e0 RAM: {u['ram']}%",          callback_data='noop'),
             InlineKeyboardButton("\u2796", callback_data='umb_ram_down'),
             InlineKeyboardButton("\u2795", callback_data='umb_ram_up')],
            [InlineKeyboardButton(f"\U0001f4be Disco: {u['disk']}%",        callback_data='noop'),
             InlineKeyboardButton("\u2796", callback_data='umb_disk_down'),
             InlineKeyboardButton("\u2795", callback_data='umb_disk_up')],
            [InlineKeyboardButton(f"\U0001f321\ufe0f Temp: {u['temp']}C",  callback_data='noop'),
             InlineKeyboardButton("\u2796", callback_data='umb_temp_down'),
             InlineKeyboardButton("\u2795", callback_data='umb_temp_up')],
            btn_volver('m_adv')
        ]
        await query.edit_message_text(
            "\u2699\ufe0f *Umbrales de Alerta* (\u00b15 por pulsacion)",
            reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown'
        )

    elif data.startswith('umb_'):
        partes_umb = data.split('_')
        metrica    = partes_umb[1]
        direccion  = partes_umb[2]
        delta      = 5 if direccion == 'up' else -5
        UMBRALES[metrica] = max(10, min(99, UMBRALES[metrica] + delta))
        query.data = 'adv_umbrales'
        await router_botones(update, context)
        return

    elif data == 'adv_wol':
        if not WOL_MACS:
            await query.edit_message_text(
                "\u26a0\ufe0f No hay MACs configuradas en WOL_MACS.",
                reply_markup=InlineKeyboardMarkup([btn_volver('m_adv')]), parse_mode='Markdown'
            )
            return
        kb = [[InlineKeyboardButton(f"\U0001fa84 {n}", callback_data=f"wol_{n}")] for n in WOL_MACS]
        kb.append(btn_volver('m_adv'))
        await query.edit_message_text(
            "\U0001fa84 *Wake-on-LAN \u2014 Elige equipo:*",
            reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown'
        )

    elif data.startswith('wol_'):
        nombre = data[4:]
        mac    = WOL_MACS.get(nombre)
        if not mac:
            await query.edit_message_text("\u274c MAC no encontrada.")
            return
        res = await exec_cmd(
            f"wakeonlan {mac} 2>/dev/null || etherwake {mac} 2>/dev/null "
            f"|| echo 'Instala: sudo apt install wakeonlan'",
            shell=True
        )
        await query.edit_message_text(
            f"\U0001fa84 WOL enviado a *{nombre}* (`{mac}`):\n`{res}`",
            reply_markup=InlineKeyboardMarkup([btn_volver('m_adv')]), parse_mode='Markdown'
        )

    elif data == 'adv_sh':
        scripts = sorted(glob.glob('/usr/local/bin/*.sh') + glob.glob(f'{ROOT_DIR}/*.sh'))
        if not scripts:
            await query.edit_message_text(
                "\U0001f4dc No se encontraron scripts .sh.",
                reply_markup=InlineKeyboardMarkup([btn_volver('m_adv')]), parse_mode='Markdown'
            )
            return
        kb = [
            [InlineKeyboardButton(f"\u25b6\ufe0f {os.path.basename(s)}", callback_data=f"run_{path_a_key(s)}")]
            for s in scripts[:15]
        ]
        kb.append(btn_volver('m_adv'))
        await query.edit_message_text(
            "\U0001f4dc *Scripts disponibles:*",
            reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown'
        )

    elif data.startswith('run_'):
        s   = key_a_path(data[4:])
        res = await exec_cmd(["bash", s], timeout=60)
        await query.edit_message_text(
            f"\u2705 `{os.path.basename(s)}`:\n```\n{res[:3500]}\n```",
            reply_markup=InlineKeyboardMarkup([btn_volver('m_adv')]), parse_mode='Markdown'
        )

    elif data == 'adv_reboot_ask':
        kb = [[InlineKeyboardButton("\U0001f504 Si, reiniciar", callback_data='adv_reboot_go'),
               InlineKeyboardButton("\u274c Cancelar",           callback_data='m_adv')]]
        await query.edit_message_text(
            "\u26a0\ufe0f *Reiniciar la Raspberry Pi?*\nEl bot tardara unos minutos en volver.",
            reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown'
        )

    elif data == 'adv_reboot_go':
        await query.edit_message_text("\U0001f504 Reiniciando... Hasta pronto \U0001f44b")
        await exec_cmd(["sudo", "reboot"])

    elif data == 'adv_shutdown_ask':
        kb = [[InlineKeyboardButton("\u23fb Si, apagar", callback_data='adv_shutdown_go'),
               InlineKeyboardButton("\u274c Cancelar",   callback_data='m_adv')]]
        await query.edit_message_text(
            "\u26a0\ufe0f *Apagar la Raspberry Pi?*\nNo podras encenderla remotamente sin WOL.",
            reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown'
        )

    elif data == 'adv_shutdown_go':
        await query.edit_message_text("\u23fb Apagando... Hasta pronto \U0001f44b")
        await exec_cmd(["sudo", "shutdown", "-h", "now"])


# ==============================================================================
# HANDLER DE MENSAJES
# ==============================================================================

async def handle_everything(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id

    if uid != MI_USUARIO_ID:
        await context.bot.send_message(
            chat_id=MI_USUARIO_ID,
            text=f"\U0001f6a8 *Mensaje de ID desconocido*\nID: `{uid}`\nTexto: {update.message.text or '(doc)'}",
            parse_mode='Markdown'
        )
        return

    mode = context.user_data.get('mode')

    # ── PIN ───────────────────────────────────────────────────────────────────
    if mode == 'pin' and update.message and update.message.text:
        txt  = update.message.text.strip()
        bloq = pin_bloqueado(context)
        if bloq:
            resta = int((bloq - datetime.datetime.now()).total_seconds() / 60) + 1
            await update.message.reply_text(f"\U0001f512 Bot bloqueado. Espera {resta} minuto(s).")
            return
        if txt == PIN_SECRETO:
            context.bot_data['pin_validado']  = True
            context.bot_data['pin_timestamp'] = datetime.datetime.now()
            context.bot_data['pin_intentos']  = 0
            context.user_data['mode'] = None
            ahora = datetime.datetime.now(ZONA_HORARIA).strftime("%d/%m/%Y %H:%M")
            await update.message.reply_text(
                f"\u2705 *Acceso concedido*\n\n\U0001f3e0 *Panel RAB v4.0*\n_{ahora}_",
                reply_markup=menu_principal(), parse_mode='Markdown'
            )
        else:
            intentos  = context.bot_data.get('pin_intentos', 0) + 1
            context.bot_data['pin_intentos'] = intentos
            restantes = PIN_MAX_INTENTOS - intentos
            if intentos >= PIN_MAX_INTENTOS:
                context.bot_data['pin_bloqueado_hasta'] = (
                    datetime.datetime.now() + datetime.timedelta(minutes=PIN_BLOQUEO_MIN)
                )
                context.bot_data['pin_intentos'] = 0
                await update.message.reply_text(
                    f"\U0001f512 *Demasiados intentos fallidos.*\nBot bloqueado {PIN_BLOQUEO_MIN} minutos.",
                    parse_mode='Markdown'
                )
                await context.bot.send_message(
                    chat_id=MI_USUARIO_ID,
                    text=f"\U0001f6a8 *Alerta de seguridad*\n{PIN_MAX_INTENTOS} intentos de PIN fallidos.\nBot bloqueado {PIN_BLOQUEO_MIN} min.",
                    parse_mode='Markdown'
                )
            else:
                await update.message.reply_text(
                    f"\u274c PIN incorrecto. {restantes} intento(s) restante(s):", parse_mode='Markdown'
                )
        return

    if not sesion_valida(context):
        context.user_data['mode'] = 'pin'
        await update.message.reply_text(
            "\U0001f510 *Sesion expirada*\nIntroduce el PIN:", parse_mode='Markdown'
        )
        return

    # ── TERMINAL ──────────────────────────────────────────────────────────────
    if mode == 'terminal' and update.message and update.message.text:
        txt = update.message.text.strip()
        if txt.lower() == 'salir':
            context.user_data['mode'] = None
            await update.message.reply_text("\U0001f4bb Terminal cerrada.", reply_markup=menu_principal())
            return
        res = await exec_cmd(txt, shell=True)
        kb  = [[
            InlineKeyboardButton("\U0001f3e0 Menu",           callback_data='menu_principal'),
            InlineKeyboardButton("\u274c Cerrar terminal",    callback_data='adv_term_close')
        ]]
        await update.message.reply_text(
            f"```\n{res[:3800]}\n```",
            parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(kb)
        )

    # ── SUBIDA DE ARCHIVOS ────────────────────────────────────────────────────
    elif mode == 'upload' and update.message and update.message.document:
        doc   = update.message.document
        path  = context.user_data.get('current_path', ROOT_DIR)
        f_obj = await doc.get_file()
        dest  = os.path.join(path, doc.file_name)
        await f_obj.download_to_drive(dest)
        context.user_data['mode'] = None
        registrar_actividad(str(uid), f"upload:{dest}")
        await update.message.reply_text(
            f"\u2705 Guardado en `{dest}`",
            reply_markup=menu_principal(), parse_mode='Markdown'
        )

    # ── DESBANEAR IP ──────────────────────────────────────────────────────────
    elif mode == 'unban_ip' and update.message and update.message.text:
        txt = update.message.text.strip()
        context.user_data['mode'] = None
        if txt.lower() == 'cancelar':
            await update.message.reply_text("\u274c Cancelado.", reply_markup=menu_principal())
            return
        if not re.match(r'^\d{1,3}(\.\d{1,3}){3}$', txt):
            await update.message.reply_text("\u26a0\ufe0f IP no valida.", reply_markup=menu_principal())
            return
        res = await exec_cmd(f"sudo fail2ban-client unban {txt}", shell=True)
        await update.message.reply_text(
            f"\U0001f513 Desbaneada `{txt}`:\n`{res}`",
            reply_markup=menu_principal(), parse_mode='Markdown'
        )

    # ── GEO-IP ────────────────────────────────────────────────────────────────
    elif mode == 'geoip' and update.message and update.message.text:
        txt = update.message.text.strip()
        context.user_data['mode'] = None
        if txt.lower() == 'cancelar':
            await update.message.reply_text("\u274c Cancelado.", reply_markup=menu_principal())
            return
        try:
            url = f"http://ip-api.com/json/{txt}?fields=status,country,regionName,city,isp,as,query"
            with urllib.request.urlopen(url, timeout=5) as r:
                d = json.loads(r.read())
            if d.get("status") == "success":
                msg = (
                    f"\U0001f30d *Geo-IP: `{d['query']}`*\n\n"
                    f"\U0001f5fa\ufe0f Pais:   {d.get('country', '?')}\n"
                    f"\U0001f3d9\ufe0f Ciudad: {d.get('city', '?')}, {d.get('regionName', '?')}\n"
                    f"\U0001f3e2 ISP:    {d.get('isp', '?')}\n"
                    f"\U0001f4e1 AS:     {d.get('as', '?')}"
                )
            else:
                msg = f"\u26a0\ufe0f Sin datos para `{txt}`"
        except Exception as e:
            msg = f"\u274c Error: {e}"
        await update.message.reply_text(msg, reply_markup=menu_principal(), parse_mode='Markdown')

    # ── PING / TRACEROUTE ─────────────────────────────────────────────────────
    elif mode == 'ping' and update.message and update.message.text:
        txt = update.message.text.strip()
        context.user_data['mode'] = None
        if txt.lower() == 'cancelar':
            await update.message.reply_text("\u274c Cancelado.", reply_markup=menu_principal())
            return
        if not re.match(r'^[a-zA-Z0-9.\-]+$', txt):
            await update.message.reply_text("\u26a0\ufe0f Host no valido.", reply_markup=menu_principal())
            return
        await update.message.reply_text(f"\u23f3 Ejecutando ping y traceroute a `{txt}`...", parse_mode='Markdown')
        ping_res  = await exec_cmd(f"ping -c 4 -W 2 {txt} 2>&1", shell=True, timeout=15)
        trace_res = await exec_cmd(f"traceroute -m 10 -w 2 {txt} 2>&1", shell=True, timeout=30)
        await update.message.reply_text(
            f"\U0001f4e1 *Ping a `{txt}`:*\n```\n{ping_res}\n```\n"
            f"\U0001f5fa\ufe0f *Traceroute:*\n```\n{trace_res[-2000:]}\n```",
            reply_markup=menu_principal(), parse_mode='Markdown'
        )

    # ── CREAR CARPETA ─────────────────────────────────────────────────────────
    elif mode == 'mkdir' and update.message and update.message.text:
        nombre = update.message.text.strip()
        context.user_data['mode'] = None
        if nombre.lower() == 'cancelar':
            await update.message.reply_text("\u274c Cancelado.", reply_markup=menu_principal())
            return
        destino = os.path.join(context.user_data.get('current_path', ROOT_DIR), nombre)
        try:
            os.makedirs(destino, exist_ok=True)
            await update.message.reply_text(
                f"\U0001f4c1 Carpeta creada: `{destino}`",
                reply_markup=menu_principal(), parse_mode='Markdown'
            )
        except Exception as e:
            await update.message.reply_text(f"\u274c Error: {e}", reply_markup=menu_principal())

    # ── BUSQUEDA DE ARCHIVOS ──────────────────────────────────────────────────
    elif mode == 'file_search' and update.message and update.message.text:
        nombre = update.message.text.strip()
        context.user_data['mode'] = None
        if nombre.lower() == 'cancelar':
            await update.message.reply_text("\u274c Cancelado.", reply_markup=menu_principal())
            return
        path = context.user_data.get('current_path', ROOT_DIR)
        res  = await exec_cmd(f"find {path} -iname '*{nombre}*' 2>/dev/null | head -20", shell=True, timeout=30)
        await update.message.reply_text(
            f"\U0001f50d *Resultados:*\n```\n{res}\n```",
            reply_markup=menu_principal(), parse_mode='Markdown'
        )

    # ── CALCULADORA DE ESPACIO ────────────────────────────────────────────────
    elif mode == 'espacio' and update.message and update.message.text:
        ruta = update.message.text.strip()
        context.user_data['mode'] = None
        if ruta.lower() == 'cancelar':
            await update.message.reply_text("\u274c Cancelado.", reply_markup=menu_principal())
            return
        if not os.path.exists(ruta):
            await update.message.reply_text(
                f"\u26a0\ufe0f Ruta no encontrada: `{ruta}`",
                reply_markup=menu_principal(), parse_mode='Markdown'
            )
            return
        await update.message.reply_text(f"\u23f3 Analizando `{ruta}`...", parse_mode='Markdown')
        res = await exec_cmd(f"du -sh {ruta}/* 2>/dev/null | sort -rh | head -20", shell=True, timeout=60)
        await update.message.reply_text(
            f"\U0001f5c2\ufe0f *Espacio en `{ruta}`:*\n```\n{res}\n```",
            reply_markup=menu_principal(), parse_mode='Markdown'
        )

    # ── NOTAS ─────────────────────────────────────────────────────────────────
    elif mode == 'notas_add' and update.message and update.message.text:
        txt = update.message.text.strip()
        context.user_data['mode'] = None
        if txt.lower() == 'cancelar':
            await update.message.reply_text("\u274c Cancelado.", reply_markup=menu_principal())
            return
        ahora = datetime.datetime.now(ZONA_HORARIA).strftime("%d/%m %H:%M")
        try:
            with open(NOTAS_FILE, "a") as f:
                f.write(f"[{ahora}] {txt}\n")
            await update.message.reply_text(
                f"\U0001f4dd Nota guardada:\n_{txt}_",
                reply_markup=menu_principal(), parse_mode='Markdown'
            )
        except Exception as e:
            await update.message.reply_text(f"\u274c Error: {e}", reply_markup=menu_principal())

    # ── SIN MODO ACTIVO ───────────────────────────────────────────────────────
    elif update.message and update.message.text and not mode:
        await update.message.reply_text(
            "\u2139\ufe0f Usa /start para abrir el panel de control.",
            reply_markup=menu_principal()
        )


async def close_terminal_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data['mode'] = None
    await query.edit_message_text('\U0001f4bb Terminal cerrada.', reply_markup=menu_principal())


# ==============================================================================
# INICIO
# ==============================================================================

if __name__ == '__main__':
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(close_terminal_cb, pattern='^adv_term_close$'))
    app.add_handler(CallbackQueryHandler(router_botones))
    app.add_handler(MessageHandler(filters.TEXT | filters.Document.ALL, handle_everything))

    jq = app.job_queue
    jq.run_repeating(monitor_recursos,       interval=300, first=60)
    jq.run_repeating(monitor_servicios,      interval=120, first=30)
    jq.run_repeating(monitor_docker,         interval=180, first=45)
    jq.run_repeating(monitor_intentos_login, interval=600, first=120)
    jq.run_daily(
        resumen_diario,
        time=datetime.time(hour=8, minute=0, tzinfo=ZONA_HORARIA)
    )

    app.post_init = anuncio_inicio

    print("\U0001f680 RAB v4.0 iniciado.")
    app.run_polling()
