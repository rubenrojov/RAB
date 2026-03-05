#!/usr/bin/env python3
# ==============================================================================
# RAB - Raspberry Admin Bot v3.0
# Bot de Telegram para administracion completa de Raspberry Pi
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
TOKEN = ""
MI_USUARIO_ID = ""
ROOT_DIR        = "/home/$USER"
ZONA_HORARIA    = pytz.timezone("Europe/Madrid")
LOG_ACTIVIDAD   = "/home/$USER/rab_actividad.log"
METRICAS_CSV    = "/home/$USER/rab_metricas.csv"
WOL_MACS        = {
    "PC-Principal": "AA:BB:CC:DD:EE:FF",
    "NAS":          "11:22:33:44:55:66",
}
UMBRALES = {"cpu": 80, "ram": 85, "disk": 90, "temp": 70}
SERVICIOS_WATCH = ["docker", "wg-quick@wg0", "fail2ban", "ssh"]

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Cache para paths largos (evita limite 64 bytes en callback_data)
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
        return "Tiempo de espera agotado"
    except Exception as e:
        return f"Error critico: {e}"


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
    chars = ["▁","▂","▃","▄","▅","▆","▇","█"]
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
        lineas.append(f"📖 Lecturas totales:  {int(campos[0]):,}")
        lineas.append(f"✏️ Escrituras totales: {int(campos[4]):,}")
    except Exception:
        lineas.append("⚠️ No se pudo leer estadisticas del bloque")
    try:
        uso = psutil.disk_usage("/")
        ic  = "🟢" if uso.percent < 70 else "🟡" if uso.percent < 85 else "🔴"
        lineas.append(f"{ic} Disco raiz: {uso.percent}% ({uso.used//1024**3}GB/{uso.total//1024**3}GB)")
    except Exception:
        pass
    temp = get_temperatura()
    ic   = "🟢" if temp < 55 else "🟡" if temp < 70 else "🔴"
    lineas.append(f"{ic} Temperatura SoC: {temp}C")
    try:
        uptime_sec = int(open("/proc/uptime").read().split()[0].split(".")[0])
        d, r = divmod(uptime_sec, 86400)
        h    = r // 3600
        m    = (r % 3600) // 60
        lineas.append(f"⏱️ Uptime: {d}d {h}h {m}m")
    except Exception:
        pass
    return "\n".join(lineas)


# ==============================================================================
# MONITORES PROACTIVOS
# ==============================================================================

async def monitor_recursos(context: ContextTypes.DEFAULT_TYPE):
    guardar_metrica()
    alertas = []
    cpu  = psutil.cpu_percent(interval=2)
    ram  = psutil.virtual_memory().percent
    disk = psutil.disk_usage("/").percent
    temp = get_temperatura()
    if cpu  > UMBRALES["cpu"]:   alertas.append(f"🔴 CPU al {cpu}% (umbral {UMBRALES['cpu']}%)")
    if ram  > UMBRALES["ram"]:   alertas.append(f"🔴 RAM al {ram}% (umbral {UMBRALES['ram']}%)")
    if disk > UMBRALES["disk"]:  alertas.append(f"🔴 Disco al {disk}% (umbral {UMBRALES['disk']}%)")
    if temp > UMBRALES["temp"]:  alertas.append(f"🌡️ Temperatura {temp}C (umbral {UMBRALES['temp']}C)")
    if alertas:
        await context.bot.send_message(
            chat_id=MI_USUARIO_ID,
            text="⚠️ *ALERTA DE RECURSOS*\n\n" + "\n".join(alertas),
            parse_mode='Markdown'
        )


async def monitor_servicios(context: ContextTypes.DEFAULT_TYPE):
    caidos = []
    for srv in SERVICIOS_WATCH:
        res = subprocess.run(
            ["systemctl", "is-active", srv],
            capture_output=True, text=True
        )
        if res.stdout.strip() != "active":
            caidos.append(srv)
    if caidos:
        await context.bot.send_message(
            chat_id=MI_USUARIO_ID,
            text="🚨 *SERVICIOS CAIDOS*\n\n" + "\n".join(f"- `{s}`" for s in caidos),
            parse_mode='Markdown'
        )


async def monitor_docker(context: ContextTypes.DEFAULT_TYPE):
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
                text="🐳 *CONTENEDOR DOCKER CAIDO*\n\n" + "\n".join(problemas),
                parse_mode='Markdown'
            )
    except Exception:
        pass


async def monitor_intentos_login(context: ContextTypes.DEFAULT_TYPE):
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
                text=f"🔐 *{len(fallos)} intentos de login fallidos*\n```\n{resumen[-2000:]}\n```",
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
        dr = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}"],
            capture_output=True, text=True, timeout=10
        )
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
            f"☀️ *Resumen Diario - {ahora}*\n\n"
            f"🖥️ CPU:      {cpu}%\n"
            f"🧠 RAM:      {ram.percent}% ({ram.used//1024**2}MB/{ram.total//1024**2}MB)\n"
            f"💾 Disco:    {disk.percent}%\n"
            f"🌡️ Temp:     {temp}C\n"
            f"🌐 IP local: `{get_ip_local()}`\n"
            f"⏱️ Uptime:   {uptime_txt}\n"
            f"🐳 Docker:   {docker_txt}"
        ),
        parse_mode='Markdown'
    )


async def anuncio_inicio(app: Application):
    ahora = datetime.datetime.now(ZONA_HORARIA).strftime("%d/%m/%Y %H:%M:%S")
    await app.bot.send_message(
        chat_id=MI_USUARIO_ID,
        text=f"🚀 *RAB v3.0 online* - {ahora}\nEscribe /start para abrir el panel.",
        parse_mode='Markdown'
    )


# ==============================================================================
# MENUS
# ==============================================================================

def menu_principal():
    kb = [
        [
            InlineKeyboardButton("📊 Sistema",    callback_data='m_sys'),
            InlineKeyboardButton("⚙️ Servicios",  callback_data='m_srv')
        ],
        [
            InlineKeyboardButton("📦 APT/Manten", callback_data='m_apt'),
            InlineKeyboardButton("🛡️ Red/Seg",  callback_data='m_net')
        ],
        [
            InlineKeyboardButton("🐳 Docker",     callback_data='m_docker'),
            InlineKeyboardButton("📂 Archivos",   callback_data=f'm_files_{path_a_key(ROOT_DIR)}_0')
        ],
        [
            InlineKeyboardButton("🛠️ Avanzado",   callback_data='m_adv'),
            InlineKeyboardButton("📈 Metricas",   callback_data='m_metrics')
        ],
    ]
    return InlineKeyboardMarkup(kb)


def btn_volver(destino='menu_principal'):
    return [InlineKeyboardButton("⬅️ Volver", callback_data=destino)]


# ==============================================================================
# HANDLERS
# ==============================================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != MI_USUARIO_ID:
        return
    context.user_data.clear()
    registrar_actividad(str(update.effective_user.id), "start")
    ahora = datetime.datetime.now(ZONA_HORARIA).strftime("%d/%m/%Y %H:%M")
    await update.message.reply_text(
        f'🏠 *Panel de Control RAB v3.0*\n_{ahora}_',
        reply_markup=menu_principal(),
        parse_mode='Markdown'
    )


async def router_botones(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != MI_USUARIO_ID:
        return
    await query.answer()
    data = query.data
    registrar_actividad(str(query.from_user.id), f"btn:{data[:40]}")

    # MENU PRINCIPAL
    if data == 'menu_principal':
        context.user_data['mode'] = None
        await query.edit_message_text(
            '🏠 *Panel Principal*',
            reply_markup=menu_principal(),
            parse_mode='Markdown'
        )

    # ── SISTEMA ──────────────────────────────────────────────────────────────
    elif data == 'm_sys':
        kb = [
            [InlineKeyboardButton("📋 Dashboard",        callback_data='sys_dash')],
            [InlineKeyboardButton("🔝 Top Procesos",     callback_data='sys_top')],
            [InlineKeyboardButton("🏥 Salud SD/Sistema", callback_data='sys_sd')],
            [InlineKeyboardButton("🌐 Info Red",         callback_data='sys_net_info')],
            [InlineKeyboardButton("💾 Montajes/Discos",  callback_data='sys_mounts')],
            btn_volver()
        ]
        await query.edit_message_text(
            "📊 *Menu Sistema*",
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
            return "🔴" if v > u else "🟢"

        txt = (
            "📊 *Dashboard del Sistema*\n\n"
            f"{ic(cpu, UMBRALES['cpu'])}  CPU:   {cpu}%\n"
            f"{ic(ram.percent, UMBRALES['ram'])}  RAM:   {ram.percent}%  ({ram.used//1024**2}MB/{ram.total//1024**2}MB)\n"
            f"{ic(disk.percent, UMBRALES['disk'])}  Disco: {disk.percent}%  ({disk.used//1024**3}GB/{disk.total//1024**3}GB)\n"
            f"{ic(temp, UMBRALES['temp'])}  Temp:  {temp}C\n"
            f"📶 IP:     `{get_ip_local()}`\n"
            f"⏱️ Uptime: {uptime_txt}\n"
            f"⚖️ Carga:  {carga[0]:.2f} / {carga[1]:.2f} / {carga[2]:.2f}"
        )
        await query.edit_message_text(
            txt,
            reply_markup=InlineKeyboardMarkup([btn_volver('m_sys')]),
            parse_mode='Markdown'
        )

    elif data == 'sys_top':
        procs = sorted(
            psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_percent']),
            key=lambda x: x.info['memory_percent'],
            reverse=True
        )[:12]
        lineas = [
            f"{p.info['name'][:14]:<14} {p.info['cpu_percent']:>5}%CPU {p.info['memory_percent']:>4.1f}%RAM"
            for p in procs
        ]
        await query.edit_message_text(
            "🔝 *Top 12 Procesos (por RAM):*\n```\n" + "\n".join(lineas) + "\n```",
            reply_markup=InlineKeyboardMarkup([btn_volver('m_sys')]),
            parse_mode='Markdown'
        )

    elif data == 'sys_sd':
        await query.edit_message_text(
            f"🏥 *Salud del Sistema*\n\n{salud_sd()}",
            reply_markup=InlineKeyboardMarkup([btn_volver('m_sys')]),
            parse_mode='Markdown'
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
            "🌐 *Informacion de Red*\n\n"
            f"🏠 IP local:   `{ip_l}`\n"
            f"🌍 IP publica: `{ip_p}`\n\n"
            "*Interfaces:*\n```\n" + "\n".join(ifaces) + "\n```"
        )
        await query.edit_message_text(
            txt,
            reply_markup=InlineKeyboardMarkup([btn_volver('m_sys')]),
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
            "💾 *Montajes y Discos:*\n```\n" + "\n".join(lineas) + "\n```",
            reply_markup=InlineKeyboardMarkup([btn_volver('m_sys')]),
            parse_mode='Markdown'
        )

    # ── METRICAS ──────────────────────────────────────────────────────────────
    elif data == 'm_metrics':
        datos = leer_metricas_recientes(24)
        if not datos["cpu"]:
            txt = "📈 *Metricas Historicas*\n\nAun no hay datos.\nEl bot registra metricas cada 5 minutos."
        else:
            txt = (
                "📈 *Metricas Historicas*\n\n"
                f"🖥️ CPU   `{sparkline(datos['cpu'])}` {datos['cpu'][-1]:.0f}%\n"
                f"🧠 RAM   `{sparkline(datos['ram'])}` {datos['ram'][-1]:.0f}%\n"
                f"💾 Disco `{sparkline(datos['disk'])}` {datos['disk'][-1]:.0f}%\n"
                f"🌡️ Temp  `{sparkline(datos['temp'])}` {datos['temp'][-1]:.1f}C\n\n"
                f"_Ultima: {datos['ts'][-1]}_"
            )
        await query.edit_message_text(
            txt,
            reply_markup=InlineKeyboardMarkup([btn_volver()]),
            parse_mode='Markdown'
        )

    # ── SERVICIOS ─────────────────────────────────────────────────────────────
    elif data == 'm_srv':
        kb = [
            [InlineKeyboardButton("🛰️ Estado WireGuard",  callback_data='srv_wg_status')],
            [InlineKeyboardButton("🛡️ Log Fail2Ban",      callback_data='log_f2b'),
             InlineKeyboardButton("🔑 Log SSH",           callback_data='log_ssh')],
            [InlineKeyboardButton("🐳 Log Docker",        callback_data='log_docker_svc')],
            [InlineKeyboardButton("📋 Cronjobs",          callback_data='srv_cron'),
             InlineKeyboardButton("📜 Servicios activos", callback_data='srv_systemd')],
            btn_volver()
        ]
        await query.edit_message_text(
            "⚙️ *Logs y Servicios*",
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode='Markdown'
        )

    elif data == 'srv_wg_status':
        res = await exec_cmd(["sudo", "wg", "show"], timeout=10)
        await query.edit_message_text(
            f"🛰️ *WireGuard - Estado:*\n```\n{res[-3500:]}\n```",
            reply_markup=InlineKeyboardMarkup([btn_volver('m_srv')]),
            parse_mode='Markdown'
        )

    elif data in ('log_f2b', 'log_ssh', 'log_docker_svc'):
        mapa = {
            'log_f2b':        ("sudo journalctl -u fail2ban -n 30 --no-pager", "Fail2Ban"),
            'log_ssh':        ("sudo tail -n 30 /var/log/auth.log",            "SSH/Auth"),
            'log_docker_svc': ("sudo journalctl -u docker -n 30 --no-pager",   "Docker"),
        }
        cmd, nombre = mapa[data]
        res = await exec_cmd(cmd, shell=True)
        await query.edit_message_text(
            f"📜 *Log {nombre}:*\n```\n{res[-3500:]}\n```",
            reply_markup=InlineKeyboardMarkup([btn_volver('m_srv')]),
            parse_mode='Markdown'
        )

    elif data == 'srv_cron':
        res = await exec_cmd(["crontab", "-l"])
        await query.edit_message_text(
            f"📋 *Cronjobs:*\n```\n{res[-3500:]}\n```",
            reply_markup=InlineKeyboardMarkup([btn_volver('m_srv')]),
            parse_mode='Markdown'
        )

    elif data == 'srv_systemd':
        res = await exec_cmd(
            "systemctl list-units --type=service --state=running --no-pager | head -40",
            shell=True
        )
        await query.edit_message_text(
            f"📜 *Servicios activos:*\n```\n{res[-3500:]}\n```",
            reply_markup=InlineKeyboardMarkup([btn_volver('m_srv')]),
            parse_mode='Markdown'
        )

    # ── RED Y SEGURIDAD ───────────────────────────────────────────────────────
    elif data == 'm_net':
        kb = [
            [InlineKeyboardButton("🚀 Test Velocidad",    callback_data='net_speed')],
            [InlineKeyboardButton("🛰️ WG Peers",          callback_data='net_wg_peers')],
            [InlineKeyboardButton("🚨 Estado Jails F2B",  callback_data='net_jails')],
            [InlineKeyboardButton("🚫 IPs Baneadas",      callback_data='net_banned')],
            [InlineKeyboardButton("🔓 Desbanear IP",      callback_data='net_unban_ask')],
            [InlineKeyboardButton("🔌 Puertos abiertos",  callback_data='net_ports')],
            [InlineKeyboardButton("🌍 Geo-IP",            callback_data='net_geoip_ask')],
            btn_volver()
        ]
        await query.edit_message_text(
            "🛡️ *Red y Seguridad*",
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode='Markdown'
        )

    elif data == 'net_speed':
        await query.edit_message_text(
            "⏳ Test de velocidad en curso (~30s)...",
            parse_mode='Markdown'
        )
        res = await exec_cmd("speedtest-cli --simple 2>&1", shell=True, timeout=90)
        await query.edit_message_text(
            f"🚀 *Test de Velocidad:*\n```\n{res}\n```",
            reply_markup=InlineKeyboardMarkup([btn_volver('m_net')]),
            parse_mode='Markdown'
        )

    elif data == 'net_wg_peers':
        res = await exec_cmd(["sudo", "wg", "show", "all"], timeout=10)
        await query.edit_message_text(
            f"🛰️ *WireGuard Peers:*\n```\n{res[-3500:]}\n```",
            reply_markup=InlineKeyboardMarkup([btn_volver('m_net')]),
            parse_mode='Markdown'
        )

    elif data == 'net_jails':
        await query.edit_message_text("⏳ Consultando jails...", parse_mode='Markdown')
        status_raw = await exec_cmd(["sudo", "fail2ban-client", "status"])
        jails = []
        for linea in status_raw.splitlines():
            if "Jail list:" in linea:
                parte = linea.split("Jail list:")[1].strip()
                jails = [j.strip() for j in re.split(r'[,\s]+', parte) if j.strip()]
        if not jails:
            txt = f"⚠️ No se pudieron detectar jails.\n```\n{status_raw}\n```"
        else:
            partes = [f"🚨 *Fail2Ban - {len(jails)} jails activas:*\n"]
            for jail in jails:
                detalle = await exec_cmd(["sudo", "fail2ban-client", "status", jail])
                partes.append(f"📌 *{jail}:*\n```\n{detalle}\n```\n")
            txt = "\n".join(partes)
        await query.edit_message_text(
            txt[:4000],
            reply_markup=InlineKeyboardMarkup([btn_volver('m_net')]),
            parse_mode='Markdown'
        )

    elif data == 'net_banned':
        res = await exec_cmd(
            "sudo fail2ban-client status | grep 'Jail list' | sed 's/.*://;s/,//g' | "
            "xargs -I{} sudo fail2ban-client status {} 2>/dev/null | grep 'Banned IP'",
            shell=True
        )
        await query.edit_message_text(
            f"🚫 *IPs Baneadas:*\n```\n{res[-3000:]}\n```",
            reply_markup=InlineKeyboardMarkup([btn_volver('m_net')]),
            parse_mode='Markdown'
        )

    elif data == 'net_unban_ask':
        context.user_data['mode'] = 'unban_ip'
        await query.edit_message_text(
            "🔓 *Desbanear IP:*\nEscribe la IP (o 'cancelar'):",
            parse_mode='Markdown'
        )

    elif data == 'net_ports':
        res = await exec_cmd("ss -tlnp | column -t", shell=True)
        await query.edit_message_text(
            f"🔌 *Puertos en escucha:*\n```\n{res[-3500:]}\n```",
            reply_markup=InlineKeyboardMarkup([btn_volver('m_net')]),
            parse_mode='Markdown'
        )

    elif data == 'net_geoip_ask':
        context.user_data['mode'] = 'geoip'
        await query.edit_message_text(
            "🌍 *Geo-IP:*\nEscribe la IP a consultar (o 'cancelar'):",
            parse_mode='Markdown'
        )

    # ── APT ───────────────────────────────────────────────────────────────────
    elif data == 'm_apt':
        kb = [
            [InlineKeyboardButton("📋 Paquetes actualizables", callback_data='apt_list')],
            [InlineKeyboardButton("⬆️ Actualizar todo",        callback_data='apt_upgrade_ask')],
            [InlineKeyboardButton("🧹 Limpieza del sistema",   callback_data='apt_clean')],
            [InlineKeyboardButton("📜 Historial APT",          callback_data='apt_history')],
            btn_volver()
        ]
        await query.edit_message_text(
            "📦 *APT / Mantenimiento*",
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode='Markdown'
        )

    elif data == 'apt_list':
        await query.edit_message_text("⏳ Consultando paquetes...", parse_mode='Markdown')
        res = await exec_cmd(
            "apt list --upgradable 2>/dev/null | grep -v 'Listing'",
            shell=True, timeout=60
        )
        if not res or res == "(sin salida)":
            res = "✅ No hay paquetes pendientes de actualizacion."
        await query.edit_message_text(
            f"📋 *Paquetes actualizables:*\n```\n{res[-3500:]}\n```",
            reply_markup=InlineKeyboardMarkup([btn_volver('m_apt')]),
            parse_mode='Markdown'
        )

    elif data == 'apt_upgrade_ask':
        kb = [
            [InlineKeyboardButton("✅ Si, actualizar", callback_data='apt_upgrade_go'),
             InlineKeyboardButton("❌ Cancelar",       callback_data='m_apt')]
        ]
        await query.edit_message_text(
            "⚠️ *Actualizar todos los paquetes?*\nEsto ejecutara `apt-get upgrade -y`.",
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode='Markdown'
        )

    elif data == 'apt_upgrade_go':
        await query.edit_message_text(
            "⏳ Actualizando... (puede tardar varios minutos)",
            parse_mode='Markdown'
        )
        res = await exec_cmd(
            "sudo apt-get update -qq && sudo apt-get upgrade -y 2>&1 | tail -20",
            shell=True, timeout=300
        )
        await query.edit_message_text(
            f"✅ *Actualizacion completada:*\n```\n{res[-3500:]}\n```",
            reply_markup=InlineKeyboardMarkup([btn_volver('m_apt')]),
            parse_mode='Markdown'
        )

    elif data == 'apt_clean':
        await query.edit_message_text("⏳ Limpiando...", parse_mode='Markdown')
        antes = psutil.disk_usage("/").used
        res = await exec_cmd(
            "sudo apt-get autoremove -y && sudo apt-get autoclean -y 2>&1 | tail -10",
            shell=True, timeout=120
        )
        liberado = (antes - psutil.disk_usage("/").used) // 1024**2
        await query.edit_message_text(
            f"🧹 *Limpieza completada*\n💾 Espacio liberado: ~{liberado}MB\n```\n{res[-2000:]}\n```",
            reply_markup=InlineKeyboardMarkup([btn_volver('m_apt')]),
            parse_mode='Markdown'
        )

    elif data == 'apt_history':
        res = await exec_cmd(
            "grep 'install\\|upgrade\\|remove' /var/log/dpkg.log | tail -30",
            shell=True
        )
        await query.edit_message_text(
            f"📜 *Historial APT:*\n```\n{res[-3500:]}\n```",
            reply_markup=InlineKeyboardMarkup([btn_volver('m_apt')]),
            parse_mode='Markdown'
        )

    # ── DOCKER ────────────────────────────────────────────────────────────────
    elif data == 'm_docker':
        kb = [
            [InlineKeyboardButton("📋 Listar contenedores", callback_data='docker_list')],
            [InlineKeyboardButton("📊 Stats recursos",      callback_data='docker_stats')],
            [InlineKeyboardButton("🖼️ Imagenes",            callback_data='docker_images')],
            [InlineKeyboardButton("🧹 Limpiar no usados",   callback_data='docker_prune_ask')],
            btn_volver()
        ]
        await query.edit_message_text(
            "🐳 *Docker*",
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode='Markdown'
        )

    elif data == 'docker_list':
        res = await exec_cmd(
            'docker ps -a --format "{{.Names}}|{{.Status}}|{{.Image}}"',
            shell=True
        )
        lineas  = []
        nombres = []
        for linea in res.splitlines():
            partes = linea.split("|")
            if len(partes) >= 2:
                nombre, estado = partes[0], partes[1]
                nombres.append(nombre)
                ic = "🟢" if estado.lower().startswith("up") else "🔴"
                lineas.append(f"{ic} {nombre:<20} {estado[:20]}")
        kb_cont = []
        for n in nombres[:8]:
            kb_cont.append([
                InlineKeyboardButton(f"📜 {n[:12]}", callback_data=f"dc_log_{n}"),
                InlineKeyboardButton("⏹",               callback_data=f"dc_stop_ask_{n}"),
                InlineKeyboardButton("▶️",         callback_data=f"dc_start_{n}"),
                InlineKeyboardButton("🔄",           callback_data=f"dc_restart_ask_{n}"),
            ])
        kb_cont.append(btn_volver('m_docker'))
        await query.edit_message_text(
            "🐳 *Contenedores:*\n```\n" + "\n".join(lineas) + "\n```",
            reply_markup=InlineKeyboardMarkup(kb_cont),
            parse_mode='Markdown'
        )

    elif data == 'docker_stats':
        res = await exec_cmd(
            "docker stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}'",
            shell=True, timeout=20
        )
        await query.edit_message_text(
            f"📊 *Docker Stats:*\n```\n{res[-3500:]}\n```",
            reply_markup=InlineKeyboardMarkup([btn_volver('m_docker')]),
            parse_mode='Markdown'
        )

    elif data == 'docker_images':
        res = await exec_cmd(
            "docker images --format 'table {{.Repository}}\t{{.Tag}}\t{{.Size}}'",
            shell=True
        )
        await query.edit_message_text(
            f"🖼️ *Imagenes Docker:*\n```\n{res[-3500:]}\n```",
            reply_markup=InlineKeyboardMarkup([btn_volver('m_docker')]),
            parse_mode='Markdown'
        )

    elif data == 'docker_prune_ask':
        kb = [
            [InlineKeyboardButton("✅ Si, limpiar", callback_data='docker_prune_go'),
             InlineKeyboardButton("❌ Cancelar",    callback_data='m_docker')]
        ]
        await query.edit_message_text(
            "⚠️ *Eliminar contenedores/imagenes no usados?*",
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode='Markdown'
        )

    elif data == 'docker_prune_go':
        res = await exec_cmd("docker system prune -f 2>&1", shell=True, timeout=60)
        await query.edit_message_text(
            f"🧹 *Docker prune:*\n```\n{res[-3000:]}\n```",
            reply_markup=InlineKeyboardMarkup([btn_volver('m_docker')]),
            parse_mode='Markdown'
        )

    elif data.startswith('dc_log_'):
        nombre = data[7:]
        res = await exec_cmd(f"docker logs --tail 30 {nombre} 2>&1", shell=True, timeout=15)
        await query.edit_message_text(
            f"📜 *Logs {nombre}:*\n```\n{res[-3500:]}\n```",
            reply_markup=InlineKeyboardMarkup([btn_volver('m_docker')]),
            parse_mode='Markdown'
        )

    elif data.startswith('dc_stop_ask_'):
        nombre = data[12:]
        kb = [
            [InlineKeyboardButton(f"⏹ Parar {nombre[:15]}", callback_data=f"dc_stop_{nombre}"),
             InlineKeyboardButton("❌ Cancelar",             callback_data='docker_list')]
        ]
        await query.edit_message_text(
            f"⚠️ *Parar `{nombre}`?*",
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode='Markdown'
        )

    elif data.startswith('dc_stop_'):
        nombre = data[8:]
        res = await exec_cmd(f"docker stop {nombre}", shell=True, timeout=30)
        await query.edit_message_text(
            f"⏹ `{nombre}` detenido:\n`{res}`",
            reply_markup=InlineKeyboardMarkup([btn_volver('m_docker')]),
            parse_mode='Markdown'
        )

    elif data.startswith('dc_start_'):
        nombre = data[9:]
        res = await exec_cmd(f"docker start {nombre}", shell=True, timeout=30)
        await query.edit_message_text(
            f"▶️ `{nombre}` iniciado:\n`{res}`",
            reply_markup=InlineKeyboardMarkup([btn_volver('m_docker')]),
            parse_mode='Markdown'
        )

    elif data.startswith('dc_restart_ask_'):
        nombre = data[15:]
        kb = [
            [InlineKeyboardButton(f"🔄 Reiniciar {nombre[:12]}", callback_data=f"dc_restart_{nombre}"),
             InlineKeyboardButton("❌ Cancelar",                  callback_data='docker_list')]
        ]
        await query.edit_message_text(
            f"⚠️ *Reiniciar `{nombre}`?*",
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode='Markdown'
        )

    elif data.startswith('dc_restart_'):
        nombre = data[11:]
        res = await exec_cmd(f"docker restart {nombre}", shell=True, timeout=30)
        await query.edit_message_text(
            f"🔄 `{nombre}` reiniciado:\n`{res}`",
            reply_markup=InlineKeyboardMarkup([btn_volver('m_docker')]),
            parse_mode='Markdown'
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
                f"Sin permiso para leer `{path}`",
                reply_markup=InlineKeyboardMarkup([btn_volver()]),
                parse_mode='Markdown'
            )
            return

        total = len(items)
        chunk = items[offset:offset + 8]
        kb    = []

        if path != ROOT_DIR:
            pk = path_a_key(os.path.dirname(path))
            kb.append([InlineKeyboardButton("📁 ⬆️ Subir nivel", callback_data=f"path_{pk}_0")])

        for item in chunk:
            full = os.path.join(path, item)
            if os.path.isdir(full):
                ck = path_a_key(full)
                kb.append([InlineKeyboardButton(f"📁 {item[:35]}", callback_data=f"path_{ck}_0")])
            else:
                fk = path_a_key(full)
                kb.append([InlineKeyboardButton(f"📄 {item[:35]}", callback_data=f"get_{fk}")])

        nav = []
        if offset > 0:
            nav.append(InlineKeyboardButton("◀️ Ant", callback_data=f"m_files_{key}_{offset-8}"))
        if offset + 8 < total:
            nav.append(InlineKeyboardButton("Sig ▶️", callback_data=f"m_files_{key}_{offset+8}"))
        if nav:
            kb.append(nav)

        sh = context.user_data.get('show_hidden', False)
        kb.append([
            InlineKeyboardButton("📤 Subir archivo",  callback_data="file_upload"),
            InlineKeyboardButton("📁 Nueva carpeta",  callback_data="file_mkdir"),
        ])
        kb.append([
            InlineKeyboardButton("🔍 Buscar",         callback_data="file_search_ask"),
            InlineKeyboardButton(f"👁️ Ocultos: {'ON' if sh else 'OFF'}", callback_data=f"file_toggle_{key}"),
        ])
        kb.append(btn_volver())

        await query.edit_message_text(
            f"📍 `{path}`\n_{min(offset+1, total)}-{min(offset+8, total)} de {total}_",
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode='Markdown'
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
            "📁 *Nueva carpeta:*\nEscribe el nombre (o 'cancelar'):",
            parse_mode='Markdown'
        )

    elif data == 'file_search_ask':
        context.user_data['mode'] = 'file_search'
        await query.edit_message_text(
            "🔍 *Buscar archivo:*\nEscribe nombre o parte (o 'cancelar'):",
            parse_mode='Markdown'
        )

    elif data == 'file_upload':
        context.user_data['mode'] = 'upload'
        await query.edit_message_text(
            "📤 *Subir archivo:*\nEnvíame el documento para guardarlo aquí.",
            parse_mode='Markdown'
        )

    elif data.startswith('get_'):
        fkey   = data[4:]
        f_path = key_a_path(fkey)
        if not os.path.isfile(f_path):
            await query.message.reply_text("❌ Archivo no encontrado.")
            return
        size = os.path.getsize(f_path)
        if size > 50 * 1024 * 1024:
            await query.message.reply_text("⚠️ Archivo demasiado grande (>50MB).")
            return
        ext      = os.path.splitext(f_path)[1].lower()
        txt_exts = {'.log', '.txt', '.conf', '.yaml', '.yml', '.ini',
                    '.env', '.sh', '.py', '.json', '.xml', '.md'}
        kb_file  = [[
            InlineKeyboardButton("📥 Descargar", callback_data=f"dl_{fkey}"),
            InlineKeyboardButton("🗑️ Borrar",    callback_data=f"del_ask_{fkey}")
        ]]
        if ext in txt_exts and size < 50000:
            try:
                with open(f_path, errors='replace') as tf:
                    contenido = tf.read(3000)
                await query.message.reply_text(
                    f"📄 *{os.path.basename(f_path)}*\n```\n{contenido}\n```",
                    reply_markup=InlineKeyboardMarkup(kb_file),
                    parse_mode='Markdown'
                )
                return
            except Exception:
                pass
        await query.message.reply_text(
            f"📄 `{os.path.basename(f_path)}` ({size//1024}KB)",
            reply_markup=InlineKeyboardMarkup(kb_file),
            parse_mode='Markdown'
        )

    elif data.startswith('dl_'):
        fkey   = data[3:]
        f_path = key_a_path(fkey)
        if os.path.isfile(f_path):
            await query.message.reply_document(document=open(f_path, 'rb'))
        else:
            await query.message.reply_text("❌ Archivo no encontrado.")

    elif data.startswith('del_ask_'):
        fkey   = data[8:]
        f_path = key_a_path(fkey)
        kb = [[
            InlineKeyboardButton("🗑️ Si, borrar", callback_data=f"del_go_{fkey}"),
            InlineKeyboardButton("❌ Cancelar",   callback_data='menu_principal')
        ]]
        await query.edit_message_text(
            f"⚠️ *Borrar definitivamente?*\n`{f_path}`",
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode='Markdown'
        )

    elif data.startswith('del_go_'):
        fkey   = data[7:]
        f_path = key_a_path(fkey)
        try:
            if os.path.isfile(f_path):
                os.remove(f_path)
                msg = f"🗑️ Borrado: `{f_path}`"
            elif os.path.isdir(f_path):
                shutil.rmtree(f_path)
                msg = f"🗑️ Directorio borrado: `{f_path}`"
            else:
                msg = "❌ No encontrado."
        except Exception as e:
            msg = f"❌ Error al borrar: {e}"
        await query.edit_message_text(
            msg,
            reply_markup=menu_principal(),
            parse_mode='Markdown'
        )

    # ── AVANZADO ──────────────────────────────────────────────────────────────
    elif data == 'm_adv':
        kb = [
            [InlineKeyboardButton("💻 Terminal",         callback_data='adv_term')],
            [InlineKeyboardButton("📜 Ejecutar .sh",     callback_data='adv_sh')],
            [InlineKeyboardButton("🪄 Wake-on-LAN",      callback_data='adv_wol')],
            [InlineKeyboardButton("🔄 Reiniciar sistema", callback_data='adv_reboot_ask')],
            [InlineKeyboardButton("⏻ Apagar sistema",   callback_data='adv_shutdown_ask')],
            [InlineKeyboardButton("📜 Log actividad bot", callback_data='adv_botlog')],
            [InlineKeyboardButton("⚙️ Ver umbrales",     callback_data='adv_umbrales')],
            btn_volver()
        ]
        await query.edit_message_text(
            "🛠️ *Herramientas Avanzadas*",
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode='Markdown'
        )

    elif data == 'adv_term':
        context.user_data['mode'] = 'terminal'
        context.user_data['term_history'] = []
        await query.edit_message_text(
            "💻 *Terminal Activa*\nEnvia cualquier comando. Escribe `salir` para cerrar.",
            parse_mode='Markdown'
        )

    elif data == 'adv_sh':
        scripts = sorted(
            glob.glob('/usr/local/bin/*.sh') + glob.glob(f'{ROOT_DIR}/*.sh')
        )
        if not scripts:
            await query.edit_message_text(
                "📜 No se encontraron scripts .sh.",
                reply_markup=InlineKeyboardMarkup([btn_volver('m_adv')]),
                parse_mode='Markdown'
            )
            return
        kb = [
            [InlineKeyboardButton(f"▶️ {os.path.basename(s)}", callback_data=f"run_{path_a_key(s)}")]
            for s in scripts[:15]
        ]
        kb.append(btn_volver('m_adv'))
        await query.edit_message_text(
            "📜 *Scripts disponibles:*",
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode='Markdown'
        )

    elif data.startswith('run_'):
        s   = key_a_path(data[4:])
        res = await exec_cmd(["bash", s], timeout=60)
        await query.edit_message_text(
            f"✅ `{os.path.basename(s)}`:\n```\n{res[:3500]}\n```",
            reply_markup=InlineKeyboardMarkup([btn_volver('m_adv')]),
            parse_mode='Markdown'
        )

    elif data == 'adv_wol':
        if not WOL_MACS:
            await query.edit_message_text(
                "⚠️ No hay MACs configuradas en WOL_MACS.",
                reply_markup=InlineKeyboardMarkup([btn_volver('m_adv')]),
                parse_mode='Markdown'
            )
            return
        kb = [[InlineKeyboardButton(f"🪄 {n}", callback_data=f"wol_{n}")] for n in WOL_MACS]
        kb.append(btn_volver('m_adv'))
        await query.edit_message_text(
            "🪄 *Wake-on-LAN - Elige equipo:*",
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode='Markdown'
        )

    elif data.startswith('wol_'):
        nombre = data[4:]
        mac    = WOL_MACS.get(nombre)
        if not mac:
            await query.edit_message_text("MAC no encontrada.")
            return
        res = await exec_cmd(
            f"wakeonlan {mac} 2>/dev/null || etherwake {mac} 2>/dev/null "
            f"|| echo 'Instala: sudo apt install wakeonlan'",
            shell=True
        )
        await query.edit_message_text(
            f"🪄 WOL enviado a *{nombre}* (`{mac}`):\n`{res}`",
            reply_markup=InlineKeyboardMarkup([btn_volver('m_adv')]),
            parse_mode='Markdown'
        )

    elif data == 'adv_reboot_ask':
        kb = [[
            InlineKeyboardButton("🔄 Si, reiniciar", callback_data='adv_reboot_go'),
            InlineKeyboardButton("❌ Cancelar",      callback_data='m_adv')
        ]]
        await query.edit_message_text(
            "⚠️ *Reiniciar la Raspberry Pi?*\nEl bot tardara unos minutos en volver.",
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode='Markdown'
        )

    elif data == 'adv_reboot_go':
        await query.edit_message_text("🔄 Reiniciando... Hasta pronto")
        await exec_cmd(["sudo", "reboot"])

    elif data == 'adv_shutdown_ask':
        kb = [[
            InlineKeyboardButton("⏻ Si, apagar", callback_data='adv_shutdown_go'),
            InlineKeyboardButton("❌ Cancelar",   callback_data='m_adv')
        ]]
        await query.edit_message_text(
            "⚠️ *Apagar la Raspberry Pi?*\nNo podras encenderla remotamente sin WOL.",
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode='Markdown'
        )

    elif data == 'adv_shutdown_go':
        await query.edit_message_text("⏻ Apagando... Hasta pronto")
        await exec_cmd(["sudo", "shutdown", "-h", "now"])

    elif data == 'adv_botlog':
        if not os.path.exists(LOG_ACTIVIDAD):
            await query.edit_message_text(
                "📜 Sin registros todavia.",
                reply_markup=InlineKeyboardMarkup([btn_volver('m_adv')]),
                parse_mode='Markdown'
            )
            return
        with open(LOG_ACTIVIDAD) as f:
            lineas = f.readlines()[-30:]
        await query.edit_message_text(
            "📜 *Ultimas 30 acciones:*\n```\n" + "".join(lineas)[-3000:] + "\n```",
            reply_markup=InlineKeyboardMarkup([btn_volver('m_adv')]),
            parse_mode='Markdown'
        )

    elif data == 'adv_umbrales':
        await query.edit_message_text(
            f"⚙️ *Umbrales de Alerta:*\n\n"
            f"🖥️ CPU:   {UMBRALES['cpu']}%\n"
            f"🧠 RAM:   {UMBRALES['ram']}%\n"
            f"💾 Disco: {UMBRALES['disk']}%\n"
            f"🌡️ Temp:  {UMBRALES['temp']}C\n\n"
            "_Para cambiarlos edita UMBRALES en el script._",
            reply_markup=InlineKeyboardMarkup([btn_volver('m_adv')]),
            parse_mode='Markdown'
        )


# ==============================================================================
# HANDLER DE MENSAJES
# ==============================================================================

async def handle_everything(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != MI_USUARIO_ID:
        return
    mode = context.user_data.get('mode')

    # TERMINAL
    if mode == 'terminal' and update.message.text:
        txt = update.message.text.strip()
        if txt.lower() == 'salir':
            context.user_data['mode'] = None
            await update.message.reply_text("💻 Terminal cerrada.", reply_markup=menu_principal())
            return
        context.user_data.setdefault('term_history', []).append(txt)
        res = await exec_cmd(txt, shell=True)
        kb  = [[
            InlineKeyboardButton("🏠 Menu",            callback_data='menu_principal'),
            InlineKeyboardButton("❌ Cerrar terminal", callback_data='adv_term_close')
        ]]
        await update.message.reply_text(
            f"```\n{res[:3800]}\n```",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(kb)
        )

    # SUBIDA DE ARCHIVOS
    elif mode == 'upload' and update.message.document:
        doc   = update.message.document
        path  = context.user_data.get('current_path', ROOT_DIR)
        f_obj = await doc.get_file()
        dest  = os.path.join(path, doc.file_name)
        await f_obj.download_to_drive(dest)
        context.user_data['mode'] = None
        registrar_actividad(str(update.effective_user.id), f"upload:{dest}")
        await update.message.reply_text(
            f"✅ Guardado en `{dest}`",
            reply_markup=menu_principal(),
            parse_mode='Markdown'
        )

    # DESBANEAR IP
    elif mode == 'unban_ip' and update.message.text:
        txt = update.message.text.strip()
        context.user_data['mode'] = None
        if txt.lower() == 'cancelar':
            await update.message.reply_text("❌ Cancelado.", reply_markup=menu_principal())
            return
        if not re.match(r'^\d{1,3}(\.\d{1,3}){3}$', txt):
            await update.message.reply_text("⚠️ IP no valida.", reply_markup=menu_principal())
            return
        res = await exec_cmd(f"sudo fail2ban-client unban {txt}", shell=True)
        await update.message.reply_text(
            f"🔓 Desbaneada `{txt}`:\n`{res}`",
            reply_markup=menu_principal(),
            parse_mode='Markdown'
        )

    # GEO-IP
    elif mode == 'geoip' and update.message.text:
        txt = update.message.text.strip()
        context.user_data['mode'] = None
        if txt.lower() == 'cancelar':
            await update.message.reply_text("Cancelado.", reply_markup=menu_principal())
            return
        try:
            url = f"http://ip-api.com/json/{txt}?fields=status,country,regionName,city,isp,as,query"
            with urllib.request.urlopen(url, timeout=5) as r:
                d = json.loads(r.read())
            if d.get("status") == "success":
                msg = (
                    f"🌍 *Geo-IP: `{d['query']}`*\n\n"
                    f"🗺️ País:   {d.get('country', '?')}\n"
                    f"🏙️ Ciudad: {d.get('city', '?')}. {d.get('regionName', '?')}\n"
                    f"🏢 ISP:    {d.get('isp', '?')}\n"
                    f"📡 AS:     {d.get('as', '?')}"
                )
            else:
                msg = f"⚠️ Sin datos para `{txt}`"
        except Exception as e:
            msg = f"❌ Error: {e}"
        await update.message.reply_text(
            msg,
            reply_markup=menu_principal(),
            parse_mode='Markdown'
        )

    # CREAR CARPETA
    elif mode == 'mkdir' and update.message.text:
        nombre = update.message.text.strip()
        context.user_data['mode'] = None
        if nombre.lower() == 'cancelar':
            await update.message.reply_text("Cancelado.", reply_markup=menu_principal())
            return
        destino = os.path.join(context.user_data.get('current_path', ROOT_DIR), nombre)
        try:
            os.makedirs(destino, exist_ok=True)
            await update.message.reply_text(
                f"📁 Carpeta creada: `{destino}`",
                reply_markup=menu_principal(),
                parse_mode='Markdown'
            )
        except Exception as e:
            await update.message.reply_text(f"❌ Error: {e}", reply_markup=menu_principal())

    # BUSQUEDA DE ARCHIVOS
    elif mode == 'file_search' and update.message.text:
        nombre = update.message.text.strip()
        context.user_data['mode'] = None
        if nombre.lower() == 'cancelar':
            await update.message.reply_text("Cancelado.", reply_markup=menu_principal())
            return
        path = context.user_data.get('current_path', ROOT_DIR)
        res  = await exec_cmd(
            f"find {path} -iname '*{nombre}*' 2>/dev/null | head -20",
            shell=True, timeout=30
        )
        await update.message.reply_text(
            f"🔍 *Resultados:*\n```\n{res}\n```",
            reply_markup=menu_principal(),
            parse_mode='Markdown'
        )

    # SIN MODO ACTIVO
    elif update.message.text and not mode:
        await update.message.reply_text(
            "ℹ️ Usa /start para abrir el panel de control.",
            reply_markup=menu_principal()
        )


async def close_terminal_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data['mode'] = None
    await query.edit_message_text('💻 Terminal cerrada.', reply_markup=menu_principal())


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

    print("🚀 RAB v3.0 iniciado.")
    app.run_polling()
