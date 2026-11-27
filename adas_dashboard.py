#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
adas_dashboard.py — Panel de Control y Orquestador (HMI Central) del clúster ADAS
=================================================================================

Aplicación de escritorio Tkinter (tema oscuro) que orquesta el clúster de percepción
vehicular (eCAL + Protobuf). El MISMO código funciona en dos escenarios:

  * Ubuntu ARM64 / Raspberry Pi (Nodo Central 192.168.50.63): el HMI corre dentro de
    un contenedor Docker con acceso X11 (`docker run ... --network host`).
  * Windows 10/11 (cualquier PC del equipo): el HMI corre de forma NATIVA
    (Tkinter + OpenCV en pantalla, sin X11 ni DISPLAY) y solo los nodos Edge se
    orquestan por SSH + Docker en las Raspberry Pi.

Nodos gestionados
-----------------
  * ADASCore   core@192.168.50.61  (Raspberry Pi 5 + Hailo-8L)  imagen adas:pi5
  * zorros-desk zorro@192.168.50.32 (nodo auxiliar CPU)          imagen adas:edge-cpu
  * HMI local  (esta máquina)      Linux: contenedor adas:edge-cpu + X11
                                  Windows: proceso nativo (sys.executable)

Características
---------------
  * Estado de nodos cada 5 s (SSH, /dev/hailo0, /dev/video0, docker).
  * 5 escenarios de 1 clic (limpieza previa de adas_* en los 3 nodos), incluido
    EDGE OFFLOADING + VIDEO: video JPEG real con la silueta segmentada superpuesta.
  * Telemetría docker stats --no-stream cada 3 s en los 3 nodos.
  * Snapshot de métricas a CSV con marca de tiempo.
  * Consola de logs en tiempo real con colores por nivel.
  * Cierre seguro: detiene los contenedores adas_* en los 3 nodos antes de salir.

Toda la E/S (SSH, docker, logs, monitoreo) corre en hilos secundarios y se comunica
con la GUI únicamente mediante queue.Queue: la ventana nunca se bloquea.

Requisitos
----------
  Linux (Nodo Central):
    sudo apt install -y python3-tk
    SSH sin contraseña:  ssh-copy-id core@192.168.50.61 ; ssh-copy-id zorro@192.168.50.32
    Docker accesible sin sudo para el usuario que ejecuta la app.
  Windows 10/11 (HMI nativo, ver README_WINDOWS.md):
    py -3 -m venv .venv ; .venv\Scripts\pip install -r requirements-windows.txt
    eCAL 5.12 (.msi + wheel ecal5), cliente OpenSSH y llaves en %USERPROFILE%\.ssh

Uso
---
  Linux:    python3 adas_dashboard.py
  Windows:  .\iniciar_windows.ps1   (o  .venv\Scripts\python adas_dashboard.py)
"""

from __future__ import annotations

import csv
import getpass
import os
import platform
import queue
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
except ImportError as exc:  # pragma: no cover - depende del sistema
    raise SystemExit(
        "Tkinter no está disponible en este sistema.\n"
        "Instálelo con:  sudo apt update && sudo apt install -y python3-tk"
    ) from exc


# ══════════════════════════════════════════════════════════════════════════════
#  Constantes de presentación
# ══════════════════════════════════════════════════════════════════════════════

APP_TITLE = "ADAS Cluster HMI — Panel de Control"
PROJECT_DIR = "Adas-agosto26"

# Directorio real del proyecto resuelto dinámicamente con pathlib: funciona en cualquier
# disco o carpeta (C:\..., D:\..., E:\...) sin rutas absolutas ni usuarios fijos.
# En el HMI Linux coincide con ~/Adas-agosto26; en los nodos remotos se usa $HOME/Adas-agosto26.
PROJECT_ROOT = Path(__file__).resolve().parent
LOCAL_PROJECT_DIR = str(PROJECT_ROOT)

# Windows: HMI nativo (Tkinter/OpenCV en pantalla, sin X11) + nodos Edge por SSH/Docker.
IS_WINDOWS = os.name == "nt" or platform.system() == "Windows"

STATUS_INTERVAL = 5.0   # s entre comprobaciones de nodo
STATS_INTERVAL = 3.0    # s entre muestreos de docker stats
QUEUE_POLL_MS = 120     # ms entre drenajes de la cola de eventos

BG = "#0d1117"
PANEL = "#151b23"
PANEL_2 = "#1c2430"
BORDER = "#2a3441"
FG = "#e6edf3"
MUTED = "#8b98a5"
GREEN = "#3fb950"
RED = "#f85149"
AMBER = "#d29922"
BLUE = "#79c0ff"
ACCENT = "#4da3ff"
CONSOLE_BG = "#090c10"

FONT = "Segoe UI" if os.name == "nt" else "DejaVu Sans"
MONO = "Consolas" if os.name == "nt" else "DejaVu Sans Mono"

SSH_OPTS = [
    "-o", "BatchMode=yes",
    "-o", "ConnectTimeout=5",
    "-o", "StrictHostKeyChecking=accept-new",
    "-o", "ServerAliveInterval=5",
    "-o", "LogLevel=ERROR",
]

IPV4_RE = re.compile(r"^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$")


# ══════════════════════════════════════════════════════════════════════════════
#  Modelo de nodos
# ══════════════════════════════════════════════════════════════════════════════

DEFAULT_NODES = {
    "core": {
        "label": "ADASCore",
        "user": "core",
        "ip": "192.168.50.61",
        "local": False,
        "requires": ("hailo0", "video0"),
        "role": "EDGE · Hailo-8L",
    },
    "desk": {
        "label": "zorros-desk",
        "user": "zorro",
        "ip": "192.168.50.32",
        "local": False,
        "requires": ("video0",),
        "role": "EDGE · CPU",
    },
    "hmi": {
        "label": "Nodo Central (HMI)",
        "user": (os.environ.get("USER") or os.environ.get("USERNAME")
                 or getpass.getuser()),
        "ip": "192.168.50.63",
        "local": True,
        "requires": (),
        "role": "LOCAL · visualizador",
    },
}


@dataclass(frozen=True)
class Node:
    key: str
    label: str
    user: str
    ip: str
    local: bool = False
    requires: tuple = ()

    @property
    def target(self) -> str:
        return f"{self.user}@{self.ip}"

    @property
    def address(self) -> str:
        return f"local · {self.ip}" if self.local else self.target


@dataclass(frozen=True)
class ContainerSpec:
    """Descripción declarativa de un contenedor del escenario."""
    name: str
    image: str
    args: tuple = ()
    devices: tuple = ()
    env: tuple = ()
    models: bool = False      # montar ~/Adas-agosto26/models:/app/models:ro
    detached: bool = True     # False => docker run en primer plano (HMI con X11)
    gui: bool = False         # montar X11 + DISPLAY
    skip_if_missing: bool = False  # no lanzar si falta alguno de sus devices en el nodo


# ══════════════════════════════════════════════════════════════════════════════
#  Ejecución remota (SSH) y local
# ══════════════════════════════════════════════════════════════════════════════

CHECK_SCRIPT = (
    'echo "PING=1"; '
    'for d in hailo0 video0 video2; do '
    'if [ -e "/dev/$d" ]; then echo "$d=1"; else echo "$d=0"; fi; done; '
    'command -v docker >/dev/null 2>&1 && echo "DOCKER=1" || echo "DOCKER=0"; '
    'docker info >/dev/null 2>&1 && echo "DAEMON=1" || echo "DAEMON=0"'
)

CLEAN_SCRIPT = 'docker rm -f $(docker ps -aq --filter name=adas_) >/dev/null 2>&1 || true'

# Sonda dinámica de dispositivos: evita lanzar contenedores con --device inexistente.
DEVICES_SCRIPT = "ls -1 /dev/video* /dev/hailo* 2>/dev/null || true"

XHOST_SCRIPT = 'xhost +local:docker >/dev/null 2>&1 || xhost +local:root >/dev/null 2>&1 || true'

STATS_FORMAT = "{{.Name}}|{{.CPUPerc}}|{{.MemUsage}}|{{.MemPerc}}"
STATS_SCRIPT = "docker stats --no-stream --format " + shlex.quote(STATS_FORMAT) + " 2>/dev/null || true"


def command_argv(node: Node, script: str, exec_local: bool = False) -> list:
    """argv para ejecutar un script bash en el nodo (local o vía SSH).

    En Windows NUNCA se invoca bash: el nodo local se maneja con procesos nativos
    (`_start_native`) y los nodos Edge con el cliente ssh de OpenSSH para Windows,
    que ejecuta el script en el bash de la Raspberry Pi.
    """
    if node.local:
        return ["bash", "-lc", ("exec " + script) if exec_local else script]
    return ["ssh", *SSH_OPTS, node.target, script]


def run_remote(node: Node, script: str, timeout: float = 20.0) -> tuple:
    """Ejecuta un script en el nodo y devuelve (returncode, salida combinada)."""
    try:
        proc = subprocess.run(
            command_argv(node, script),
            capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace",
        )
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, f"timeout tras {timeout:.0f}s en {node.address}"
    except FileNotFoundError:
        return 127, ("ssh no disponible en este equipo (Windows: activa el cliente "
                     "OpenSSH; Linux: apt install openssh-client)")
    except Exception as exc:  # pragma: no cover - defensivo
        return 1, f"{exc}"


def kill_process_tree(pid: int) -> None:
    """Termina el árbol de procesos en Windows (taskkill /T cierra también sus hijos)."""
    if not IS_WINDOWS:
        return
    try:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                       capture_output=True, timeout=5)
    except Exception:
        pass


def check_node(node: Node) -> dict:
    """Comprueba conectividad y presencia de dispositivos/docker en un nodo."""
    if node.local and IS_WINDOWS:
        return check_local_windows()
    state = {
        "reachable": False,
        "hailo0": False,
        "video0": False,
        "video2": False,
        "docker": False,
        "daemon": False,
    }
    rc, out = run_remote(node, CHECK_SCRIPT, 8.0)
    if "PING=1" not in out:
        return state
    state["reachable"] = True
    for line in out.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().lower()
        if key in state and key != "reachable":
            state[key] = value.strip() == "1"
    return state


_CAMERA_PROBE_TTL = 20.0
_camera_probe = {"index": None, "ok": False, "stamp": 0.0}


def local_camera_available(index: int = 0, ttl: float = _CAMERA_PROBE_TTL) -> bool:
    """Comprueba con OpenCV que exista una cámara local por índice (Windows).

    En Windows no hay /dev/video*: la cámara se valida abriéndola por índice con
    CAP_DSHOW / CAP_MSMF. El resultado se cachea `ttl` segundos y, si la cámara está
    ocupada por un publicador local, se conserva el último estado bueno en lugar de
    marcarla en rojo.
    """
    now = time.monotonic()
    cached = _camera_probe["index"] == index
    if cached and (now - _camera_probe["stamp"]) < ttl:
        return bool(_camera_probe["ok"])

    ok = False
    try:
        from utils.helper_functions import open_video_capture   # import diferido (cv2)
        cap = open_video_capture(index)
        if cap is not None:
            try:
                ret, frame = cap.read()
                ok = bool(ret) and frame is not None
            finally:
                cap.release()
    except Exception:  # noqa: BLE001 - una cámara ausente no debe romper el monitor
        ok = False

    if not ok and cached and _camera_probe["ok"] and (now - _camera_probe["stamp"]) < 120:
        return True      # cámara en uso por un publicador local: se mantiene el estado

    _camera_probe.update(index=index, ok=ok, stamp=now)
    return ok


def check_local_windows() -> dict:
    """Salud del nodo local en Windows: Python, cámara OpenCV y ssh nativo.

    Se omiten /dev/video0, /dev/hailo0 y `xhost` (no existen en Windows) y no se exige
    docker local: el HMI corre como proceso nativo y los contenedores viven en las
    Raspberry Pi, gobernados por ssh.
    """
    return {
        "reachable": True,
        "hailo0": False,
        "video0": local_camera_available(0),
        "video2": False,
        "docker": False,          # sin Docker local por diseño en Windows
        "daemon": False,
        "python": bool(sys.version_info >= (3, 10)),
        "ssh": shutil.which("ssh") is not None,
    }


def probe_devices(node: Node) -> Optional[set]:
    """Conjunto de /dev/video* y /dev/hailo* presentes en el nodo.

    Devuelve None si el nodo no responde: en ese caso no se filtra ningún job,
    para no ocultar el error real de docker/SSH.
    """
    if node.local and IS_WINDOWS:
        return None      # en Windows no hay /dev/video*: la cámara se valida con OpenCV
    rc, out = run_remote(node, DEVICES_SCRIPT, 8.0)
    if rc != 0:
        return None
    return {line.strip() for line in out.splitlines() if line.strip()}


def fetch_stats(node: Node) -> Optional[list]:
    """Devuelve filas (nodo, contenedor, cpu, cpu_txt, mem, mem_pct) o None si el nodo no responde."""
    if node.local and IS_WINDOWS:
        return []        # el HMI nativo de Windows no ejecuta contenedores adas_*
    rc, out = run_remote(node, STATS_SCRIPT, 14.0)
    if rc != 0:
        return None
    rows = []
    for line in out.splitlines():
        parts = [p.strip() for p in line.split("|")]
        if len(parts) != 4:
            continue
        name, cpu, mem, mem_pct = parts
        if not name.startswith("adas_"):
            continue
        try:
            cpu_value = float(cpu.rstrip("%")) if cpu else 0.0
        except ValueError:
            cpu_value = 0.0
        rows.append((node.label, name, cpu_value, cpu, mem, mem_pct))
    return rows


# ══════════════════════════════════════════════════════════════════════════════
#  Construcción de comandos docker
# ══════════════════════════════════════════════════════════════════════════════

def build_docker_run(spec: ContainerSpec, node: Node, nodes: tuple, display: str) -> str:
    """Compone el comando `docker run` remoto/local con los flags del clúster."""
    ips = {n.key: n.ip for n in nodes}
    host_map = (
        ("ADASCore", ips.get("core", "192.168.50.61")),
        ("giezinunezpc", ips.get("hmi", "192.168.50.63")),
        ("zorros-desk", ips.get("desk", "192.168.50.32")),
    )

    parts = ["docker", "run"]
    if spec.detached:
        parts.append("-d")
    parts += ["--rm", "--name", shlex.quote(spec.name)]
    parts += ["--network", "host", "--ipc", "host", "--pid", "host"]
    for host, ip in host_map:
        parts.append(f"--add-host={host}:{ip}")

    if spec.gui and node.local:
        parts += ["-e", shlex.quote(f"DISPLAY={display}"), "-e", "QT_X11_NO_MITSHM=1",
                  "-v", "/tmp/.X11-unix:/tmp/.X11-unix"]

    parts += ["-e", "PYTHONUNBUFFERED=1"]
    for item in spec.env:
        parts += ["-e", shlex.quote(item)]
    for device in spec.devices:
        parts += ["--device", shlex.quote(device)]

    if node.local:
        # as_posix(): un volumen docker siempre usa '/', también si el repo está en Windows.
        local_dir = PROJECT_ROOT.as_posix()
        parts += [
            "-v", shlex.quote(f"{local_dir}/ecal.ini:/app/ecal.ini:ro"),
            "-v", shlex.quote(f"{local_dir}/ecal.ini:/etc/ecal/ecal.ini:ro"),
        ]
        if spec.models:
            parts += ["-v", shlex.quote(f"{local_dir}/models:/app/models:ro")]
    else:
        # En remoto el comando lo vuelve a interpretar el shell del nodo (ssh ...
        # "docker run ..."), así que $HOME debe ir SIN comillas dentro del token:
        # con comillas llegaría literal a docker y el bind mount no existiría.
        remote_dir = f"$HOME/{PROJECT_DIR}"
        parts += [
            "-v", f"{remote_dir}/ecal.ini:/app/ecal.ini:ro",
            "-v", f"{remote_dir}/ecal.ini:/etc/ecal/ecal.ini:ro",
        ]
        if spec.models:
            parts += ["-v", f"{remote_dir}/models:/app/models:ro"]

    parts.append(spec.image)
    parts.extend(spec.args)
    return " ".join(parts)


# Estado + código de salida + error del contenedor en una sola consulta.
INSPECT_FORMAT = "{{.State.Status}}|{{.State.ExitCode}}|{{.State.Error}}"


def build_inspect_script(name: str) -> str:
    """Script que devuelve 'estado|exit_code|error' del contenedor."""
    return ("docker inspect --format " + shlex.quote(INSPECT_FORMAT)
            + " " + shlex.quote(name) + " 2>&1")


def parse_container_state(text: str) -> Optional[tuple]:
    """Interpreta la salida de docker inspect: (estado, exit_code, error).

    Devuelve ('eliminado', '', '') si el contenedor ya no existe (caso típico de
    un `--rm` que salió antes de la verificación) y None si no se pudo leer.
    """
    for line in reversed([l for l in (text or "").splitlines() if l.strip()]):
        low = line.lower()
        if "no such object" in low or "no such container" in low:
            return ("eliminado", "", "")
        fields = [f.strip() for f in line.split("|")]
        if len(fields) == 3 and fields[0] and not low.startswith("error"):
            return (fields[0], fields[1], fields[2])
    return None


def build_modes() -> list:
    """Los 5 escenarios del clúster (4 de despliegue + parada de emergencia)."""
    stream = lambda *values: ("--streams", *values)  # noqa: E731 - ayuda de legibilidad

    # ── Escenario 1: Edge Offloading puro (2 cámaras, solo metadatos) ─────────
    edge1 = ContainerSpec(
        name="adas_edge1", image="adas:pi5",
        args=("python3", "main_pub.py", "--stream-id", "1", "--camera", "0",
              "--edge-ai", "--no-video", "--backend", "hailo",
              "--model", "models/yolov8n_seg.hef", "--no-preview"),
        devices=("/dev/hailo0", "/dev/video0"),
        env=("ADAS_HAILO_SCORES=auto",),
        models=True,
    )
    edge2 = ContainerSpec(
        name="adas_edge2", image="adas:edge-cpu",
        args=("python3", "main_pub.py", "--stream-id", "2", "--camera", "0",
              "--edge-ai", "--no-video", "--backend", "cpu",
              "--model", "models/yolov8n-seg.pt", "--no-preview"),
        devices=("/dev/video0",),
        models=True,
    )
    central_b = ContainerSpec(
        name="adas_central_B", image="adas:edge-cpu",
        args=("python3", "main_sub_ai.py", *stream("1", "2"), "--backend", "none"),
        detached=False, gui=True,
    )

    # ── Escenario 5: Edge offloading + video (silueta en vivo) ───────────────
    # Igual que el escenario 1 pero SIN --no-video: el edge envía metadatos de
    # silueta Y el JPEG; el HMI solo compone (--backend none), sin inferir.
    edge1_video = ContainerSpec(
        name="adas_edge1", image="adas:pi5",
        args=("python3", "main_pub.py", "--stream-id", "1", "--camera", "0",
              "--edge-ai", "--backend", "hailo",
              "--model", "models/yolov8n_seg.hef", "--no-preview"),
        devices=("/dev/hailo0", "/dev/video0"),
        env=("ADAS_HAILO_SCORES=auto",),
        models=True,
    )
    edge2_video = ContainerSpec(
        name="adas_edge2", image="adas:edge-cpu",
        args=("python3", "main_pub.py", "--stream-id", "2", "--camera", "0",
              "--edge-ai", "--backend", "cpu",
              "--model", "models/yolov8n-seg.pt", "--no-preview"),
        devices=("/dev/video0",),
        models=True,
    )
    central_hybrid = ContainerSpec(
        name="adas_central_hybrid", image="adas:edge-cpu",
        args=("python3", "main_sub_ai.py", *stream("1", "2"), "--backend", "none"),
        detached=False, gui=True,
    )

    # ── Escenario 2: Clúster completo (4 cámaras, mixto) ─────────────────────
    # Ambas cámaras 2 (/dev/video2) son opcionales: el dashboard comprueba qué
    # /dev/video* existen antes de lanzar y omite el job si falta el device.
    edge3 = ContainerSpec(
        name="adas_edge3", image="adas:pi5",
        args=("python3", "main_pub.py", "--stream-id", "3", "--camera", "2", "--no-preview"),
        devices=("/dev/video2",),
        skip_if_missing=True,
    )
    edge4 = ContainerSpec(
        name="adas_edge4", image="adas:edge-cpu",
        args=("python3", "main_pub.py", "--stream-id", "4", "--camera", "2",
              "--edge-ai", "--no-video", "--backend", "cpu",
              "--model", "models/yolov8n-seg.pt", "--no-preview"),
        devices=("/dev/video2",),
        models=True, skip_if_missing=True,
    )
    central = ContainerSpec(
        name="adas_central", image="adas:edge-cpu",
        args=("python3", "main_sub_ai.py", *stream("1", "2", "3", "4"), "--backend", "none"),
        detached=False, gui=True,
    )

    # ── Escenario 3: Centralizado tradicional (video crudo + inferencia HMI) ──
    raw1 = ContainerSpec(
        name="adas_raw1", image="adas:pi5",
        args=("python3", "main_pub.py", "--stream-id", "1", "--camera", "0", "--no-preview"),
        devices=("/dev/video0",),
    )
    raw2 = ContainerSpec(
        name="adas_raw2", image="adas:edge-cpu",
        args=("python3", "main_pub.py", "--stream-id", "2", "--camera", "0", "--no-preview"),
        devices=("/dev/video0",),
    )
    central_a = ContainerSpec(
        name="adas_central_A", image="adas:edge-cpu",
        args=("python3", "main_sub_ai.py", *stream("1", "2"),
              "--backend", "cpu", "--model", "models/yolov8n-seg.pt"),
        models=True, detached=False, gui=True,
    )

    return [
        {
            "num": 1, "key": "m1", "title": "Edge Offloading Puro",
            "tagline": "2 CÁMARAS · SOLO METADATOS",
            "desc": "Hailo-8L (cam 1) y CPU (cam 2) infieren en el edge y envían solo "
                    "metadatos; el HMI no infiere.",
            "color": ACCENT, "btn_bg": "#102a44", "btn_hover": "#16395c", "btn_fg": BLUE,
            "jobs": (("core", edge1), ("desk", edge2), ("hmi", central_b)),
        },
        {
            "num": 2, "key": "m2", "title": "Clúster Completo",
            "tagline": "4 CÁMARAS · MIXTO HAILO + CPU + VIDEO",
            "desc": "edge1/edge2 envían metadatos y edge3/edge4 video crudo; el HMI "
                    "se suscribe a 4 streams (edge3/edge4 solo si hay /dev/video2).",
            "color": "#a78bfa", "btn_bg": "#221b3d", "btn_hover": "#2d2450", "btn_fg": "#c4b5fd",
            "jobs": (("core", edge1), ("core", edge3), ("desk", edge2),
                     ("desk", edge4), ("hmi", central)),
        },
        {
            "num": 3, "key": "m3", "title": "Centralizado Tradicional",
            "tagline": "2 CÁMARAS · VIDEO CRUDO → INFERENCIA HMI",
            "desc": "Video crudo desde los nodos; el HMI ejecuta yolov8n-seg en CPU "
                    "para ambos streams.",
            "color": "#f0a45c", "btn_bg": "#3a2a15", "btn_hover": "#4c371c", "btn_fg": "#f5c98a",
            "jobs": (("core", raw1), ("desk", raw2), ("hmi", central_a)),
        },
        {
            "num": 4, "key": "m4", "title": "Detener y Limpiar Clúster",
            "tagline": "PARADA DE EMERGENCIA",
            "desc": "docker rm -f adas_* en paralelo en ADASCore, zorros-desk y el "
                    "nodo local (cierra X11).",
            "color": RED, "btn_bg": "#3d1417", "btn_hover": "#521b20", "btn_fg": "#ff9b94",
            "jobs": (), "stop": True,
        },
        {
            "num": 5, "key": "m5", "wide": True,
            "title": "Edge Offloading + Video (Silueta en Vivo)",
            "tagline": "● EN VIVO",
            "desc": "Video JPEG + metadatos de silueta desde los edges (IA en Hailo / CPU); "
                    "el HMI solo compone los polígonos (--backend none, sin inferir).",
            "color": "#2dd4bf", "btn_bg": "#0d3330", "btn_hover": "#124743", "btn_fg": "#7ff0e0",
            "jobs": (("core", edge1_video), ("desk", edge2_video), ("hmi", central_hybrid)),
        },
    ]


MODES = build_modes()

# ══════════════════════════════════════════════════════════════════════════════
#  Aplicación
# ══════════════════════════════════════════════════════════════════════════════

class Dashboard:
    """HMI central: construye la GUI y coordina los hilos de orquestación."""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.q: "queue.Queue" = queue.Queue()
        self._shutdown_evt = threading.Event()
        self._closing = False
        self._destroyed = False
        self._op_lock = threading.Lock()
        self._proc_lock = threading.Lock()
        self._procs: list = []
        self._nodes_snapshot: tuple = ()
        self._last_stats = ([], [], "")
        self.active_mode: Optional[str] = None
        self._overlay: Optional[tk.Toplevel] = None

        self.node_cards = {}
        self.scenario_buttons = {}

        self._build_style()
        self._build_ui()
        self._refresh_nodes()
        self._start_workers()
        self.root.after(QUEUE_POLL_MS, self._pump)

        self._log("sys", "ADAS Cluster HMI iniciado · Nodo Central %s" % DEFAULT_NODES["hmi"]["ip"])
        self._log("info", "SSH en modo BatchMode (llaves sin contraseña) · docker sin sudo requerido")
        if IS_WINDOWS:
            self._log("info", "Windows: HMI nativo (sin X11/DISPLAY) · nodos Edge por ssh+Docker · "
                              "eCAL usa ecal_windows.ini vía ECAL_DATA")
        self._log("info", "Monitor de nodos cada 5 s · telemetría docker stats cada 3 s")

    # ──────────────────────────────────────────────────────────────────────
    #  Estilos
    # ──────────────────────────────────────────────────────────────────────
    def _build_style(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure(".", background=BG, foreground=FG, font=(FONT, 9))
        style.configure("TFrame", background=BG)
        style.configure("TLabel", background=BG, foreground=FG)

        style.configure(
            "Adas.TEntry",
            fieldbackground=PANEL_2, foreground=FG, insertcolor=FG,
            bordercolor=BORDER, lightcolor=PANEL_2, darkcolor=PANEL_2,
            padding=(6, 4), relief="flat",
        )
        style.map("Adas.TEntry", bordercolor=[("focus", ACCENT)])

        style.configure(
            "Adas.Treeview",
            background=PANEL, fieldbackground=PANEL, foreground=FG,
            rowheight=26, borderwidth=0, relief="flat", font=(MONO, 9),
        )
        style.map("Adas.Treeview",
                  background=[("selected", "#1f3a5f")],
                  foreground=[("selected", FG)])
        style.configure(
            "Adas.Treeview.Heading",
            background=PANEL_2, foreground=MUTED, relief="flat",
            font=(FONT, 8, "bold"), padding=(8, 6),
        )
        style.map("Adas.Treeview.Heading", background=[("active", PANEL_2)])

        style.configure(
            "Adas.Vertical.TScrollbar",
            background=PANEL_2, troughcolor=BG, bordercolor=BG,
            arrowcolor=MUTED, relief="flat", width=10,
        )
        style.configure(
            "Adas.Horizontal.TScrollbar",
            background=PANEL_2, troughcolor=BG, bordercolor=BG,
            arrowcolor=MUTED, relief="flat", width=10,
        )
        style.configure("Adas.Horizontal.TProgressbar",
                        background=ACCENT, troughcolor=PANEL_2, bordercolor=PANEL_2)

    # ──────────────────────────────────────────────────────────────────────
    #  Construcción de la interfaz
    # ──────────────────────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        root = self.root
        root.configure(bg=BG)
        root.title(APP_TITLE)
        width = min(1280, max(1040, root.winfo_screenwidth() - 40))
        height = min(840, max(680, root.winfo_screenheight() - 70))
        root.geometry(f"{width}x{height}")
        root.minsize(1040, 640)
        root.rowconfigure(2, weight=1)
        root.columnconfigure(0, weight=1)

        self._build_header()
        self._build_node_bar()
        self._build_middle()
        self._build_footer()
        root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_header(self) -> None:
        header = tk.Frame(self.root, bg=BG, padx=18, pady=9)
        header.grid(row=0, column=0, sticky="ew")

        title = tk.Frame(header, bg=BG)
        title.pack(side="left")
        tk.Label(title, text="ADAS CLUSTER · HMI", bg=BG, fg=FG,
                 font=(FONT, 17, "bold")).pack(anchor="w")
        if IS_WINDOWS:
            subtitle = ("Orquestador SSH + Docker en los nodos Edge · HMI nativo Windows "
                        "(Tkinter/OpenCV) · eCAL + Protobuf · subred 192.168.50.0/24")
        else:
            subtitle = ("Orquestador Docker + eCAL + Protobuf · Nodo Central 192.168.50.63 · "
                        "subred 192.168.50.0/24")
        tk.Label(
            title, text=subtitle,
            bg=BG, fg=MUTED, font=(FONT, 8),
        ).pack(anchor="w", pady=(2, 0))

        self.mode_badge = tk.Label(
            header, text="SIN ESCENARIO", bg=PANEL_2, fg=MUTED,
            font=(FONT, 9, "bold"), padx=14, pady=7,
        )
        self.mode_badge.pack(side="right")

    def _build_node_bar(self) -> None:
        bar = tk.Frame(self.root, bg=BG)
        bar.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 8))
        for col in range(3):
            bar.columnconfigure(col, weight=1, uniform="nodes")

        for col, (key, defaults) in enumerate(DEFAULT_NODES.items()):
            card = tk.Frame(
                bar, bg=PANEL, padx=12, pady=8,
                highlightthickness=1, highlightbackground=BORDER,
            )
            card.grid(row=0, column=col, sticky="nsew",
                      padx=(0 if col == 0 else 8, 0))

            top = tk.Frame(card, bg=PANEL)
            top.pack(fill="x")
            dot = tk.Label(top, text="●", bg=PANEL, fg=MUTED, font=(FONT, 13))
            dot.pack(side="left")
            tk.Label(top, text=defaults["label"], bg=PANEL, fg=FG,
                     font=(FONT, 10, "bold")).pack(side="left", padx=(2, 0))
            tk.Label(top, text=defaults["role"], bg=PANEL, fg=MUTED,
                     font=(FONT, 7, "bold")).pack(side="right")

            addr = tk.Label(card, text="", bg=PANEL, fg=MUTED,
                            font=(MONO, 8), anchor="w")
            addr.pack(fill="x", pady=(2, 6))

            chips_row = tk.Frame(card, bg=PANEL)
            chips_row.pack(fill="x")
            if defaults["local"] and IS_WINDOWS:
                # HMI nativo: sin /dev/* ni Docker local; se valida python, cámara y ssh.
                chip_defs = [("ssh", "ssh"), ("video0", "cam0"), ("python", "python")]
            else:
                chip_defs = [("ssh", "LOCAL" if defaults["local"] else "SSH")]
                chip_defs += [(dev, dev) for dev in defaults["requires"]]
                chip_defs.append(("docker", "docker"))

            chip_labels = {}
            for chip_key, chip_text in chip_defs:
                lbl = tk.Label(
                    chips_row, text=f"{chip_text} ·", bg="#1b2028", fg=MUTED,
                    font=(FONT, 7, "bold"), padx=5, pady=1,
                )
                lbl.pack(side="left", padx=(0, 4))
                chip_labels[chip_key] = (lbl, chip_text)

            self.node_cards[key] = {"dot": dot, "addr": addr, "chips": chip_labels}

    # ──────────────────────────────────────────────────────────────────────
    #  Zona central: configuración + escenarios | telemetría
    # ──────────────────────────────────────────────────────────────────────
    def _build_middle(self) -> None:
        middle = tk.Frame(self.root, bg=BG)
        middle.grid(row=2, column=0, sticky="nsew", padx=16)
        middle.rowconfigure(0, weight=1)
        middle.columnconfigure(0, weight=0, minsize=612)
        middle.columnconfigure(1, weight=1)

        left = tk.Frame(middle, bg=BG)
        left.grid(row=0, column=0, sticky="nsew")
        left.columnconfigure(0, weight=1)
        left.rowconfigure(2, weight=1)

        self._build_config(left)

        tk.Label(left, text="ESCENARIOS DE EJECUCIÓN", bg=BG, fg=MUTED,
                 font=(FONT, 8, "bold")).grid(row=1, column=0, sticky="w",
                                               pady=(8, 4))

        grid = tk.Frame(left, bg=BG)
        grid.grid(row=2, column=0, sticky="nsew")
        for col in range(2):
            grid.columnconfigure(col, weight=1, uniform="modes")
        for row in range(2):
            grid.rowconfigure(row, weight=1, uniform="modes")
        grid.rowconfigure(2, weight=0)   # franja ancha: EDGE OFFLOADING + VIDEO

        for index, mode in enumerate(MODES):
            card = self._build_mode_card(grid, mode)
            if mode.get("wide"):
                card.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(4, 0))
                continue
            card.grid(row=index // 2, column=index % 2, sticky="nsew",
                      padx=(0 if index % 2 == 0 else 4, 4 if index % 2 == 0 else 0),
                      pady=(0 if index < 2 else 2, 2 if index < 2 else 0))

        right = tk.Frame(middle, bg=BG)
        right.grid(row=0, column=1, sticky="nsew", padx=(12, 0))
        right.columnconfigure(0, weight=1)
        right.rowconfigure(0, weight=2)
        right.rowconfigure(1, weight=1)
        self._build_telemetry(right)
        self._build_console(right)

    def _build_config(self, parent: tk.Frame) -> None:
        cfg = tk.Frame(
            parent, bg=PANEL, padx=12, pady=6,
            highlightthickness=1, highlightbackground=BORDER,
        )
        cfg.grid(row=0, column=0, sticky="ew")
        cfg.columnconfigure(1, weight=1)

        head = tk.Frame(cfg, bg=PANEL)
        head.grid(row=0, column=0, columnspan=4, sticky="ew", pady=(0, 5))
        tk.Label(head, text="CONFIGURACIÓN DE NODOS", bg=PANEL, fg=MUTED,
                 font=(FONT, 8, "bold")).pack(side="left")
        tk.Label(head, text="se aplica al siguiente escenario", bg=PANEL, fg="#6a7684",
                 font=(FONT, 7)).pack(side="right")

        for col, text in ((2, "USUARIO"), (3, "IP")):
            tk.Label(cfg, text=text, bg=PANEL, fg="#6a7684",
                     font=(FONT, 7, "bold")).grid(row=1, column=col, sticky="w", padx=(8, 0))

        self.user_vars = {}
        self.ip_vars = {}
        for row, (key, defaults) in enumerate(DEFAULT_NODES.items(), start=2):
            tk.Label(cfg, text=defaults["label"], bg=PANEL, fg=FG,
                     font=(FONT, 8, "bold"), anchor="w").grid(
                row=row, column=0, sticky="w", pady=1)

            user_var = tk.StringVar(value=defaults["user"])
            ip_var = tk.StringVar(value=defaults["ip"])
            tk.Entry(cfg, textvariable=user_var, width=12, bg=PANEL_2, fg=FG,
                     insertbackground=FG, relief="flat", font=(MONO, 9),
                     highlightthickness=1, highlightbackground=BORDER,
                     highlightcolor=ACCENT).grid(row=row, column=2, sticky="w", padx=(8, 6), ipady=2)
            tk.Entry(cfg, textvariable=ip_var, width=16, bg=PANEL_2, fg=FG,
                     insertbackground=FG, relief="flat", font=(MONO, 9),
                     highlightthickness=1, highlightbackground=BORDER,
                     highlightcolor=ACCENT).grid(row=row, column=3, sticky="w", padx=(0, 0), ipady=2)

            self.user_vars[key] = user_var
            self.ip_vars[key] = ip_var
            user_var.trace_add("write", lambda *_: self._refresh_nodes())
            ip_var.trace_add("write", lambda *_: self._refresh_nodes())

    def _build_mode_card(self, parent: tk.Frame, mode: dict) -> tk.Frame:
        color = mode["color"]
        wide = bool(mode.get("wide"))
        card = tk.Frame(parent, bg=PANEL, highlightthickness=1, highlightbackground=BORDER)
        card.columnconfigure(0, weight=1)
        card.rowconfigure(1, weight=1)

        tk.Frame(card, bg=color, height=2).grid(row=0, column=0, sticky="ew")

        body = tk.Frame(card, bg=PANEL, padx=12, pady=5)
        body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.rowconfigure(2, weight=0 if wide else 1)

        head = tk.Frame(body, bg=PANEL)
        head.grid(row=0, column=0, sticky="ew")
        tk.Label(head, text=f"MODO {mode['num']}", bg=color, fg="#0b0f14",
                 font=(FONT, 7, "bold"), padx=6, pady=2).pack(side="left")
        tk.Label(head, text=mode["title"], bg=PANEL, fg=FG,
                 font=(FONT, 10, "bold"), anchor="w").pack(side="left", padx=(7, 0))

        if wide:
            # En la franja ancha la tagline va alineada con el título (ahorra altura).
            tk.Label(head, text=mode["tagline"], bg=PANEL, fg=color,
                     font=(FONT, 7, "bold")).pack(side="left", padx=(10, 0), pady=(2, 0))
        else:
            tk.Label(body, text=mode["tagline"], bg=PANEL, fg=color,
                     font=(FONT, 7, "bold"), anchor="w").grid(
                row=1, column=0, sticky="w", pady=(5, 2))

        desc = tk.Label(body, text=mode["desc"], bg=PANEL, fg=MUTED,
                        font=(FONT, 8), anchor="nw", justify="left",
                        wraplength=470 if wide else 285)
        desc.grid(row=1 if wide else 2, column=0, sticky="nw" if wide else "nsew")

        label = "■  Detener todo" if mode.get("stop") else "▶  Iniciar escenario"
        button = tk.Button(
            body, text=label, bg=mode["btn_bg"], fg=mode["btn_fg"],
            activebackground=mode["btn_hover"], activeforeground=mode["btn_fg"],
            disabledforeground="#5a6472", relief="flat", bd=0,
            font=(FONT, 9, "bold"), cursor="hand2", padx=8, pady=4,
            command=lambda m=mode: self._on_mode_click(m),
        )
        if wide:
            # Franja horizontal: el botón va a la derecha, centrado con el texto.
            button.grid(row=0, column=1, rowspan=2, sticky="e", padx=(14, 2))
        else:
            button.grid(row=3, column=0, sticky="ew", pady=(7, 0))
        button.bind("<Enter>", lambda e, b=button, h=mode["btn_hover"]:
                    b.configure(bg=h) if str(b["state"]) != "disabled" else None)
        button.bind("<Leave>", lambda e, b=button, n=mode["btn_bg"]:
                    b.configure(bg=n) if str(b["state"]) != "disabled" else None)

        self.scenario_buttons[mode["key"]] = button
        return card

    def _build_telemetry(self, parent: tk.Frame) -> None:
        panel = tk.Frame(parent, bg=PANEL, highlightthickness=1, highlightbackground=BORDER)
        panel.grid(row=0, column=0, sticky="nsew")
        panel.rowconfigure(2, weight=1)
        panel.columnconfigure(0, weight=1)

        head = tk.Frame(panel, bg=PANEL, padx=14, pady=10)
        head.grid(row=0, column=0, sticky="ew")
        titles = tk.Frame(head, bg=PANEL)
        titles.pack(side="left")
        tk.Label(titles, text="TELEMETRÍA · docker stats --no-stream", bg=PANEL, fg=FG,
                 font=(FONT, 10, "bold")).pack(anchor="w")
        tk.Label(titles, text="CPU y RAM por contenedor · muestreo cada 3 s en los 3 nodos",
                 bg=PANEL, fg=MUTED, font=(FONT, 8)).pack(anchor="w", pady=(2, 0))

        snapshot_btn = tk.Button(
            head, text="⭳  Guardar snapshot (.csv)", bg="#0f2a1d", fg="#7ee2a8",
            activebackground="#153a28", activeforeground="#7ee2a8",
            relief="flat", bd=0, font=(FONT, 8, "bold"), padx=12, pady=6,
            cursor="hand2", command=self._save_snapshot,
        )
        snapshot_btn.pack(side="right")
        snapshot_btn.bind("<Enter>", lambda e: snapshot_btn.configure(bg="#153a28"))
        snapshot_btn.bind("<Leave>", lambda e: snapshot_btn.configure(bg="#0f2a1d"))

        table = tk.Frame(panel, bg=PANEL)
        table.grid(row=2, column=0, sticky="nsew", padx=10, pady=(0, 8))
        table.rowconfigure(0, weight=1)
        table.columnconfigure(0, weight=1)

        columns = ("nodo", "contenedor", "cpu", "ram", "rampct")
        self.tree = ttk.Treeview(table, columns=columns, show="headings",
                                 style="Adas.Treeview", height=5)
        headings = {
            "nodo": "NODO", "contenedor": "CONTENEDOR", "cpu": "% CPU",
            "ram": "RAM USADA", "rampct": "RAM %",
        }
        widths = {"nodo": (140, "w"), "contenedor": (170, "w"),
                  "cpu": (80, "e"), "ram": (190, "e"), "rampct": (70, "e")}
        for col in columns:
            self.tree.heading(col, text=headings[col])
            width, anchor = widths[col]
            self.tree.column(col, width=width, anchor=anchor, stretch=(col in ("ram", "contenedor")))

        self.tree.tag_configure("high", foreground=RED)
        self.tree.tag_configure("mid", foreground=AMBER)
        self.tree.grid(row=0, column=0, sticky="nsew")

        scroll = ttk.Scrollbar(table, orient="vertical", style="Adas.Vertical.TScrollbar",
                               command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scroll.set)

        self.stats_info = tk.Label(panel, text="esperando métricas…", bg=PANEL, fg=MUTED,
                                   font=(FONT, 8), anchor="w", padx=14)
        self.stats_info.grid(row=3, column=0, sticky="ew", pady=(0, 8))

    # ──────────────────────────────────────────────────────────────────────
    #  Consola de logs
    # ──────────────────────────────────────────────────────────────────────
    def _build_console(self, parent: tk.Frame) -> None:
        panel = tk.Frame(parent, bg=PANEL, highlightthickness=1, highlightbackground=BORDER)
        panel.grid(row=1, column=0, sticky="nsew", pady=(8, 0))
        panel.rowconfigure(1, weight=1)
        panel.columnconfigure(0, weight=1)

        head = tk.Frame(panel, bg=PANEL, padx=12, pady=6)
        head.grid(row=0, column=0, sticky="ew")
        tk.Label(head, text="CONSOLA DE LOGS", bg=PANEL, fg=FG,
                 font=(FONT, 8, "bold")).pack(side="left")
        tk.Label(head, text="orquestación SSH · docker run · salida de contenedores",
                 bg=PANEL, fg="#6a7684", font=(FONT, 7)).pack(side="left", padx=(10, 0))

        clear_btn = tk.Button(
            head, text="Limpiar", bg=PANEL_2, fg=MUTED, activebackground=BORDER,
            activeforeground=FG, relief="flat", bd=0, font=(FONT, 8), padx=10, pady=3,
            cursor="hand2", command=self._clear_console,
        )
        clear_btn.pack(side="right")

        body = tk.Frame(panel, bg=CONSOLE_BG)
        body.grid(row=1, column=0, sticky="nsew", padx=1, pady=(0, 1))
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)

        self.console = tk.Text(
            body, bg=CONSOLE_BG, fg="#c9d1d9", insertbackground=FG, relief="flat",
            font=(MONO, 8), wrap="none", padx=10, pady=6, height=5,
            selectbackground="#1f3a5f", state="disabled",
        )
        self.console.grid(row=0, column=0, sticky="nsew")
        self.console.tag_configure("ts", foreground="#5b6673")
        self.console.tag_configure("info", foreground="#c9d1d9")
        self.console.tag_configure("ok", foreground=GREEN)
        self.console.tag_configure("warn", foreground=AMBER)
        self.console.tag_configure("err", foreground=RED)
        self.console.tag_configure("cmd", foreground=BLUE)
        self.console.tag_configure("sys", foreground=FG, font=(MONO, 8, "bold"))

        vscroll = ttk.Scrollbar(body, orient="vertical", style="Adas.Vertical.TScrollbar",
                                command=self.console.yview)
        vscroll.grid(row=0, column=1, sticky="ns")
        hscroll = ttk.Scrollbar(body, orient="horizontal", style="Adas.Horizontal.TScrollbar",
                                command=self.console.xview)
        hscroll.grid(row=1, column=0, sticky="ew")
        self.console.configure(yscrollcommand=vscroll.set, xscrollcommand=hscroll.set)

    def _build_footer(self) -> None:
        footer = tk.Frame(self.root, bg=BG, padx=18, pady=5)
        footer.grid(row=3, column=0, sticky="ew")
        tk.Label(
            footer,
            text="SSH sin contraseña (BatchMode) · docker sin sudo · estado cada 5 s · "
                 "telemetría cada 3 s · el cierre detiene el clúster",
            bg=BG, fg="#5b6673", font=(FONT, 7),
        ).pack(side="left")
        self.busy_label = tk.Label(footer, text="", bg=BG, fg=BLUE, font=(FONT, 8, "bold"))
        self.busy_label.pack(side="right")

    def _append_log(self, level: str, text: str) -> None:
        if level not in ("info", "ok", "warn", "err", "cmd", "sys"):
            level = "info"
        stamp = datetime.now().strftime("%H:%M:%S")
        self.console.configure(state="normal")
        self.console.insert("end", f"{stamp}  ", "ts")
        self.console.insert("end", text + "\n", level)
        lines = int(self.console.index("end-1c").split(".")[0])
        if lines > 2000:
            self.console.delete("1.0", f"{lines - 1500}.0")
        self.console.configure(state="disabled")
        self.console.see("end")

    def _clear_console(self) -> None:
        self.console.configure(state="normal")
        self.console.delete("1.0", "end")
        self.console.configure(state="disabled")

    def _log(self, level: str, text: str) -> None:
        """Seguro desde cualquier hilo: sólo encola."""
        self.q.put(("log", level, text))

    # ──────────────────────────────────────────────────────────────────────
    #  Configuración de nodos
    # ──────────────────────────────────────────────────────────────────────
    def _refresh_nodes(self) -> None:
        nodes = []
        for key, defaults in DEFAULT_NODES.items():
            user = self.user_vars[key].get().strip() or defaults["user"]
            ip = self.ip_vars[key].get().strip() or defaults["ip"]
            nodes.append(Node(key=key, label=defaults["label"], user=user, ip=ip,
                              local=defaults["local"], requires=tuple(defaults["requires"])))
        self._nodes_snapshot = tuple(nodes)
        for node in nodes:
            card = self.node_cards.get(node.key)
            if card:
                card["addr"].configure(text=node.address)

    def _valid_nodes(self) -> bool:
        seen = set()
        for node in self._nodes_snapshot:
            if not node.user:
                self._log("err", f"Configuración inválida: usuario vacío en {node.label}")
                return False
            match = IPV4_RE.match(node.ip)
            if not match or any(not 0 <= int(part) <= 255 for part in match.groups()):
                self._log("err", f"Configuración inválida: IP '{node.ip}' en {node.label}")
                return False
            if node.ip in seen:
                self._log("err", f"Configuración inválida: IP duplicada {node.ip}")
                return False
            seen.add(node.ip)
        return True

    # ──────────────────────────────────────────────────────────────────────
    #  Hilos de monitoreo
    # ──────────────────────────────────────────────────────────────────────
    def _start_workers(self) -> None:
        for target, name in ((self._status_loop, "adas-status"),
                             (self._stats_loop, "adas-stats")):
            threading.Thread(target=target, name=name, daemon=True).start()

    def _status_loop(self) -> None:
        first = True
        while not self._shutdown_evt.is_set():
            if not first and self._shutdown_evt.wait(STATUS_INTERVAL):
                break
            first = False
            nodes = self._nodes_snapshot
            if not nodes:
                continue
            try:
                with ThreadPoolExecutor(max_workers=len(nodes)) as pool:
                    futures = {pool.submit(check_node, node): node for node in nodes}
                    for future in as_completed(futures):
                        node = futures[future]
                        try:
                            self.q.put(("node_status", node.key, future.result()))
                        except Exception as exc:
                            self._log("warn", f"Estado de {node.label}: {exc}")
            except Exception as exc:
                self._log("warn", f"Monitor de nodos: {exc}")

    def _stats_loop(self) -> None:
        first = True
        while not self._shutdown_evt.is_set():
            if not first and self._shutdown_evt.wait(STATS_INTERVAL):
                break
            first = False
            nodes = self._nodes_snapshot
            if not nodes:
                continue
            rows, failed = [], []
            try:
                with ThreadPoolExecutor(max_workers=len(nodes)) as pool:
                    futures = {pool.submit(fetch_stats, node): node for node in nodes}
                    for future in as_completed(futures):
                        node = futures[future]
                        try:
                            data = future.result()
                        except Exception:
                            data = None
                        if data is None:
                            failed.append(node.label)
                        else:
                            rows.extend(data)
            except Exception as exc:
                self._log("warn", f"Telemetría: {exc}")
                continue

            order = {defaults["label"]: index for index, defaults in enumerate(DEFAULT_NODES.values())}
            rows.sort(key=lambda row: (order.get(row[0], 9), row[1]))
            stamp = datetime.now().strftime("%H:%M:%S")
            self.q.put(("stats", rows, failed, stamp))

    # ──────────────────────────────────────────────────────────────────────
    #  Bucle de eventos GUI (único punto que toca widgets)
    # ──────────────────────────────────────────────────────────────────────
    def _pump(self) -> None:
        if self._destroyed:
            return
        try:
            while True:
                event = self.q.get_nowait()
                self._handle_event(event)
                if self._destroyed:
                    return
        except queue.Empty:
            pass
        if not self._destroyed:
            try:
                self.root.after(QUEUE_POLL_MS, self._pump)
            except tk.TclError:
                pass

    def _handle_event(self, event: tuple) -> None:
        kind = event[0]
        try:
            if kind == "log":
                self._append_log(event[1], event[2])
            elif kind == "node_status":
                self._update_node_card(event[1], event[2])
            elif kind == "stats":
                self._update_stats(event[1], event[2], event[3])
            elif kind == "busy":
                self._set_busy(event[1])
            elif kind == "active_mode":
                self._set_active_mode(event[1])
            elif kind == "closing_done":
                self._finish_close()
        except tk.TclError:
            pass
        except Exception as exc:  # pragma: no cover - defensivo
            traceback.print_exc()
            self._append_log("warn", f"Evento {kind} ignorado: {exc}")

    def _update_node_card(self, key: str, state: dict) -> None:
        card = self.node_cards.get(key)
        if not card:
            return
        node = next((n for n in self._nodes_snapshot if n.key == key), None)
        if node is None:
            return

        reachable = bool(state.get("reachable"))
        devices_ok = all(state.get(dev) for dev in node.requires)
        if node.local and IS_WINDOWS:
            # Sin docker local: la salud depende de Python y de la cámara local.
            healthy = reachable and bool(state.get("python")) and devices_ok
        else:
            healthy = reachable and devices_ok and state.get("docker") and state.get("daemon", True)
        card["dot"].configure(fg=GREEN if healthy else RED)

        for chip_key, (label, text) in card["chips"].items():
            if chip_key == "ssh":
                ok = reachable and state.get("ssh", True)
                unknown = False
            elif chip_key == "docker":
                ok = reachable and state.get("docker") and state.get("daemon", True)
                unknown = not reachable
            else:
                ok = reachable and state.get(chip_key)
                unknown = not reachable

            if unknown:
                label.configure(text=f"{text} ?", bg="#1b2028", fg="#6a7684")
            elif ok:
                label.configure(text=f"{text} ✓", bg="#0f2a1d", fg=GREEN)
            else:
                label.configure(text=f"{text} ✗", bg="#2c1416", fg=RED)

    def _update_stats(self, rows: list, failed: list, stamp: str) -> None:
        self._last_stats = (list(rows), list(failed), stamp)
        self.tree.delete(*self.tree.get_children())
        for node_label, name, cpu_value, cpu_txt, mem, mem_pct in rows:
            tag = "high" if cpu_value >= 80 else "mid" if cpu_value >= 50 else ""
            self.tree.insert("", "end",
                             values=(node_label, name, cpu_txt, mem, mem_pct),
                             tags=(tag,) if tag else ())

        detail = f"{len(rows)} contenedores adas_* · actualizado {stamp}"
        if failed:
            detail += f" · sin respuesta: {', '.join(failed)}"
            self.stats_info.configure(text=detail, fg=RED)
        else:
            self.stats_info.configure(text=detail, fg=MUTED)

    def _set_active_mode(self, label: Optional[str]) -> None:
        if not label:
            self.active_mode = None
            self.mode_badge.configure(text="SIN ESCENARIO", bg=PANEL_2, fg=MUTED)
        else:
            self.active_mode = label
            self.mode_badge.configure(text=label.upper(), bg="#0f2a1d", fg=GREEN)

    def _set_busy(self, busy: bool) -> None:
        state = "disabled" if (busy or self._closing) else "normal"
        for key, button in self.scenario_buttons.items():
            if key == "m4":
                continue
            button.configure(state=state)
        self.busy_label.configure(text="operación en curso…" if busy else "")

    # ──────────────────────────────────────────────────────────────────────
    #  Acciones de escenario
    # ──────────────────────────────────────────────────────────────────────
    def _on_mode_click(self, mode: dict) -> None:
        if self._closing:
            return
        if not self._valid_nodes():
            return
        nodes = self._nodes_snapshot

        if mode.get("stop"):
            threading.Thread(target=self._stop_worker, args=(nodes, "parada de emergencia"),
                             name="adas-stop", daemon=True).start()
            return

        if IS_WINDOWS:
            display = ""          # HMI nativo: sin X11 ni DISPLAY
        else:
            display = os.environ.get("DISPLAY") or ":0"
            if not os.environ.get("DISPLAY"):
                self._log("warn", "DISPLAY no definido en el entorno; se usará :0 para el contenedor central")
        threading.Thread(target=self._launch_worker, args=(mode, nodes, display),
                         name=f"adas-{mode['key']}", daemon=True).start()

    def _launch_worker(self, mode: dict, nodes: tuple, display: str) -> None:
        if not self._op_lock.acquire(blocking=False):
            self._log("warn", "Otra operación está en curso; espere a que termine.")
            return
        try:
            self.q.put(("busy", True))
            if self._closing:
                return
            self._log("sys", f"▶ MODO {mode['num']} · {mode['title']} — desplegando clúster")
            if self.active_mode:
                self._log("info", f"Escenario previo activo: {self.active_mode}. Se limpia antes de continuar.")

            self._cleanup(nodes, "limpieza previa al escenario")
            if self._closing:
                return

            by_key = {node.key: node for node in nodes}
            hmi = by_key.get("hmi")
            if (not IS_WINDOWS) and any(spec.gui for _, spec in mode["jobs"]) and hmi is not None:
                rc, out = run_remote(hmi, XHOST_SCRIPT, 10)
                if rc == 0:
                    self._log("info", "X11: acceso del contenedor al servidor gráfico habilitado (xhost +local:docker)")
                else:
                    self._log("warn", f"X11: no se pudo ejecutar xhost (rc={rc}) {out.strip()[:160]}")
            elif IS_WINDOWS:
                self._log("info", "Windows: el HMI se lanza nativo (sin xhost ni DISPLAY)")

            started = []
            device_cache: dict = {}
            skipped = []
            for node_key, spec in mode["jobs"]:
                if self._closing:
                    return
                if not spec.detached:
                    continue
                node = by_key[node_key]
                if node.local and IS_WINDOWS:
                    continue      # el nodo local de Windows no usa Docker (se lanza nativo)
                missing = self._missing_devices(node, spec, device_cache)
                if missing:
                    self._log("warn", f"{node.label}: se omite {spec.name} — no existe "
                                       f"{', '.join(missing)} en el nodo (cámara no conectada)")
                    skipped.append(spec.name)
                    continue
                command = build_docker_run(spec, node, nodes, display)
                self._log("cmd", f"{node.label} → docker run --rm --name {spec.name} "
                                  f"{spec.image} {' '.join(spec.args)}")
                rc, out = run_remote(node, command, 30)
                if rc == 0:
                    self._log("ok", f"{node.label}: contenedor {spec.name} lanzado")
                    # Se guarda la salida de docker run para poder enseñarla si el
                    # contenedor muere al instante y --rm ya lo ha borrado.
                    started.append((node, spec, out))
                else:
                    detail = " ".join(out.strip().splitlines()[-3:])
                    self._log("err", f"{node.label}: fallo al lanzar {spec.name} (rc={rc}) {detail[:300]}")

            if skipped:
                self._log("info", f"Contenedores omitidos: {', '.join(skipped)}. "
                                   f"El suscriptor del HMI mostrará esas cámaras inactivas.")

            if started:
                time.sleep(1.6)
                for node, spec, launch_out in started:
                    if self._closing:
                        return
                    self._verify_container(node, spec, launch_out)

            for node_key, spec in mode["jobs"]:
                if self._closing:
                    return
                node = by_key[node_key]
                if node.local and IS_WINDOWS:
                    # main_sub_ai.py / main_pub.py con sys.executable: sin Docker ni X11.
                    self._start_native(spec)
                    time.sleep(0.8)
                    continue
                if spec.detached:
                    continue
                self._start_attached(node, spec, nodes, display)
                time.sleep(0.8)

            label = f"MODO {mode['num']} · {mode['title']}"
            self.active_mode = label
            self.q.put(("active_mode", label))
            self._log("sys", f"Escenario activo: {label}")

        except Exception as exc:
            self._log("err", f"Error inesperado al desplegar el escenario: {exc}")
            traceback.print_exc()
        finally:
            self._op_lock.release()
            self.q.put(("busy", False))

    def _missing_devices(self, node: Node, spec: ContainerSpec, cache: dict) -> list:
        """Devices declarados por el job que no existen en el nodo.

        Solo se aplica a specs con `skip_if_missing` (cámaras opcionales): si el
        nodo no responde devuelve [] y el lanzamiento se intenta igual.
        """
        if not spec.skip_if_missing or not spec.devices:
            return []
        if node.key not in cache:
            cache[node.key] = probe_devices(node)
        present = cache[node.key]
        if present is None:
            self._log("warn", f"{node.label}: no se pudo leer /dev/video* "
                               f"(se intenta lanzar {spec.name} igual)")
            return []
        return [device for device in spec.devices if device not in present]

    def _verify_container(self, node: Node, spec: ContainerSpec, launch_output: str = "") -> None:
        """Comprueba el estado real del contenedor; si murió, explica por qué."""
        script = build_inspect_script(spec.name)
        state = None
        raw = ""
        for _attempt in range(3):
            _rc, raw = run_remote(node, script, 10)
            state = parse_container_state(raw)
            if state is not None:
                break
            time.sleep(0.6)

        if state is None:
            detail = " ".join(raw.strip().splitlines()[-2:])[:200]
            self._log("warn", f"{node.label}: {spec.name} en estado desconocido "
                               f"(docker inspect no respondió) {detail}")
            self._report_launch_failure(node, spec, launch_output)
            return

        status, exit_code, state_error = state
        if status == "running":
            self._log("ok", f"{node.label}: {spec.name} en ejecución")
            self._start_follower(node, spec.name)
            return

        reason = f"estado: {status}"
        if exit_code and exit_code != "0":
            reason += f", código de salida {exit_code}"
        if state_error and state_error not in ("<no value>", "0"):
            reason += f", error: {state_error}"
        self._log("err", f"{node.label}: {spec.name} NO está en ejecución ({reason})")
        self._report_launch_failure(node, spec, launch_output)

    def _report_launch_failure(self, node: Node, spec: ContainerSpec,
                               launch_output: str = "") -> None:
        """Muestra los logs del contenedor o, si --rm ya lo borró, la salida de docker run."""
        _rc, logs = run_remote(
            node, "docker logs --tail 20 " + shlex.quote(spec.name) + " 2>&1", 12)
        lines = [line.rstrip() for line in logs.strip().splitlines() if line.strip()]
        gone = (not lines) or any(
            ("no such container" in line.lower()) or ("no such object" in line.lower())
            for line in lines
        )
        if lines and not gone:
            self._log("warn", f"{node.label}: últimos logs de {spec.name}")
            for line in lines[-20:]:
                self._log(self._classify(line), f"[{spec.name}] {line[:400]}")
        else:
            self._log("warn", f"{node.label}: {spec.name} ya no existe "
                               f"(--rm lo eliminó al salir); no quedan logs en el nodo")

        # La salida de `docker run -d` normal es solo el ID: se filtra.
        extra = [line for line in (launch_output or "").strip().splitlines()
                 if line.strip() and not re.fullmatch(r"[0-9a-f]{12,64}", line.strip())]
        for line in extra[:10]:
            self._log("err", f"[{spec.name}] docker run: {line[:400]}")
        if not extra:
            self._log("err", f"{node.label}: {spec.name} terminó antes de la verificación. "
                              f"Comando sugerido en el nodo: docker run sin -d para ver el error"
                              f" o docker ps -a --filter name={spec.name}")

    def _native_argv(self, spec: ContainerSpec) -> list:
        """argv nativo para Windows: sys.executable + los args del job (sin 'python3').

        Las rutas de modelo se resuelven contra la raíz del proyecto (que es el cwd del
        subproceso): `--model models/yolov8n-seg.pt` se sustituye por `yolov8n-seg.pt`
        solo cuando el archivo no existe pero el modelo sí está en la raíz.
        """
        args = list(spec.args)
        if args and args[0] in ("python", "python3", "py"):
            args = args[1:]
        for index, value in enumerate(args[:-1]):
            if value != "--model":
                continue
            candidate = PROJECT_ROOT / args[index + 1]
            fallback = PROJECT_ROOT / Path(args[index + 1]).name
            if not candidate.exists() and fallback.exists():
                args[index + 1] = Path(args[index + 1]).name
        return [sys.executable, *args]

    def _start_native(self, spec: ContainerSpec) -> None:
        """Lanza main_sub_ai.py / main_pub.py como proceso nativo de Windows.

        Sin `docker run`, sin `xhost` y sin DISPLAY: Tkinter y OpenCV dibujan
        directamente en la pantalla del PC. La salida se refleja en la consola del HMI.
        """
        argv = self._native_argv(spec)
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        # UTF-8 en las tuberías de log (los main_*.py imprimen emojis).
        env.setdefault("PYTHONIOENCODING", "utf-8")
        env.setdefault("PYTHONUTF8", "1")
        self._log("cmd", f"HMI local (Windows) → {' '.join(argv[1:])}")
        try:
            process = subprocess.Popen(
                argv, cwd=str(PROJECT_ROOT), env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
            )
        except Exception as exc:
            self._log("err", f"Nodo local: no se pudo iniciar {spec.name}: {exc}")
            return

        with self._proc_lock:
            self._procs.append(process)
        self._log("ok", f"Nodo local: {spec.name} iniciado como proceso nativo (PID {process.pid})")
        threading.Thread(target=self._stream_process, args=(process, spec.name),
                         name=f"log-{spec.name}", daemon=True).start()
        threading.Thread(target=self._verify_native, args=(process, spec.name),
                         name=f"check-{spec.name}", daemon=True).start()

    def _verify_native(self, process: subprocess.Popen, name: str) -> None:
        """Avisa en la consola si el proceso nativo murió al arrancar (import/modelo/cámara)."""
        time.sleep(2.0)
        code = process.poll()
        if code is None:
            self._log("ok", f"Nodo local: {name} en ejecución")
        else:
            self._log("err", f"Nodo local: {name} terminó al instante (código {code}); "
                              f"revisa la consola (dependencias, modelo o cámara)")

    def _start_attached(self, node: Node, spec: ContainerSpec, nodes: tuple, display: str) -> None:
        command = build_docker_run(spec, node, nodes, display)
        self._log("cmd", f"{node.label} → docker run --rm --name {spec.name} "
                          f"{spec.image} {' '.join(spec.args)}")
        try:
            process = subprocess.Popen(
                command_argv(node, command, exec_local=True),
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
            )
        except Exception as exc:
            self._log("err", f"{node.label}: no se pudo iniciar {spec.name}: {exc}")
            return

        with self._proc_lock:
            self._procs.append(process)
        self._log("ok", f"{node.label}: contenedor {spec.name} iniciado (X11 · salida en consola)")
        threading.Thread(target=self._stream_process, args=(process, spec.name),
                         name=f"log-{spec.name}", daemon=True).start()

    def _start_follower(self, node: Node, container: str) -> None:
        script = "docker logs -f --tail 4 " + shlex.quote(container) + " 2>&1"
        try:
            process = subprocess.Popen(
                command_argv(node, script),
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
            )
        except Exception as exc:
            self._log("warn", f"{node.label}: no se pudo seguir el log de {container}: {exc}")
            return
        with self._proc_lock:
            self._procs.append(process)
        threading.Thread(target=self._stream_process, args=(process, container),
                         name=f"tail-{container}", daemon=True).start()

    @staticmethod
    def _classify(line: str) -> str:
        low = line.lower()
        if any(token in line for token in ("❌", "Traceback")) or "error" in low or "failed" in low:
            return "err"
        if "⚠" in line or "warn" in low:
            return "warn"
        if "✅" in line:
            return "ok"
        return "info"

    def _stream_process(self, process: subprocess.Popen, origin: str) -> None:
        try:
            if process.stdout is not None:
                for line in process.stdout:
                    text = line.rstrip("\n")
                    if text.strip():
                        self._log(self._classify(text), f"[{origin}] {text}")
        except Exception:
            pass
        finally:
            rc = process.poll()
            if rc is None:
                try:
                    rc = process.wait(timeout=1)
                except Exception:
                    rc = None
            if rc not in (None, 0, -15, 143):
                self._log("warn", f"[{origin}] proceso finalizado con código {rc}")
            else:
                self._log("info", f"[{origin}] proceso finalizado")

    def _terminate_processes(self) -> None:
        with self._proc_lock:
            processes = list(self._procs)
            self._procs.clear()
        for process in processes:
            try:
                if process.poll() is None:
                    process.terminate()
            except Exception:
                pass
        for process in processes:
            try:
                process.wait(timeout=2)
            except Exception:
                if IS_WINDOWS:
                    kill_process_tree(process.pid)
                try:
                    process.kill()
                except Exception:
                    pass

    def _cleanup(self, nodes: tuple, reason: str) -> None:
        self._log("sys", f"🧹 Limpieza de contenedores adas_* · {reason}")
        targets = nodes
        if IS_WINDOWS:
            # El nodo local de Windows no tiene Docker: se cierran sus procesos nativos.
            self._terminate_processes()
            targets = tuple(node for node in nodes if not node.local)
            self._log("ok", "Nodo local (Windows): procesos nativos finalizados (terminate/taskkill)")
        if not targets:
            return
        try:
            with ThreadPoolExecutor(max_workers=len(targets)) as pool:
                futures = {pool.submit(run_remote, node, CLEAN_SCRIPT, 20.0): node for node in targets}
                for future in as_completed(futures):
                    node = futures[future]
                    try:
                        rc, out = future.result()
                    except Exception as exc:
                        self._log("warn", f"{node.label}: limpieza fallida: {exc}")
                        continue
                    if rc == 0:
                        self._log("ok", f"{node.label}: contenedores adas_* eliminados")
                    else:
                        detail = out.strip().splitlines()[-1] if out.strip() else ""
                        self._log("warn", f"{node.label}: limpieza con rc={rc} {detail[:200]}")
        except Exception as exc:
            self._log("err", f"Limpieza del clúster: {exc}")

    def _stop_worker(self, nodes: tuple, reason: str) -> None:
        if not self._op_lock.acquire(timeout=1.0):
            self._log("warn", "Otra operación está en curso; reintente la parada en unos segundos.")
            return
        self.q.put(("busy", True))
        try:
            self._log("sys", f"⏹ Deteniendo clúster · {reason}")
            self._terminate_processes()
            self._cleanup(nodes, reason)
            self.active_mode = None
            self.q.put(("active_mode", None))
            self._log("ok", "Clúster detenido: no quedan contenedores adas_* activos.")
        except Exception as exc:
            self._log("err", f"Error deteniendo el clúster: {exc}")
        finally:
            self._op_lock.release()
            self.q.put(("busy", False))

    # ──────────────────────────────────────────────────────────────────────
    #  Snapshot de métricas (CSV)
    # ──────────────────────────────────────────────────────────────────────
    def _save_snapshot(self) -> None:
        rows, failed, stamp = self._last_stats
        if not rows:
            self._append_log("warn", "No hay métricas para exportar: no se detectan contenedores adas_* activos.")
            return

        file_stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = filedialog.asksaveasfilename(
            title="Guardar snapshot de métricas",
            initialdir=os.path.expanduser("~"),
            initialfile=f"adas_metricas_{file_stamp}.csv",
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv"), ("Todos los archivos", "*.*")],
        )
        if not path:
            self._append_log("info", "Snapshot cancelado por el usuario.")
            return

        mode = self.active_mode or "SIN ESCENARIO"
        threading.Thread(target=self._write_csv, args=(path, list(rows), mode),
                         name="adas-csv", daemon=True).start()

    def _write_csv(self, path: str, rows: list, mode: str) -> None:
        try:
            stamp = datetime.now().astimezone().isoformat(timespec="seconds")
            with open(path, "w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(["marca_tiempo", "nodo", "contenedor", "cpu_pct",
                                 "ram_usada", "ram_pct", "modo_activo"])
                for node_label, name, cpu_value, _cpu_txt, mem, mem_pct in rows:
                    writer.writerow([stamp, node_label, name, f"{cpu_value:.2f}",
                                     mem, mem_pct, mode])
            self._log("ok", f"Snapshot guardado: {path} ({len(rows)} filas · {mode})")
        except OSError as exc:
            self._log("err", f"No se pudo escribir el CSV: {exc}")

    # ──────────────────────────────────────────────────────────────────────
    #  Cierre seguro
    # ──────────────────────────────────────────────────────────────────────
    def _on_close(self) -> None:
        if self._closing:
            return
        confirmed = messagebox.askokcancel(
            "Cerrar ADAS HMI",
            "Se detendrán todos los contenedores adas_* en ADASCore, zorros-desk "
            "y el nodo local antes de salir.\n\n¿Detener el clúster y cerrar el panel?",
            icon="warning",
        )
        if not confirmed:
            return

        self._closing = True
        self._shutdown_evt.set()
        self._set_busy(True)
        self._append_log("sys", "Cerrando el panel: deteniendo el clúster en los 3 nodos…")

        overlay = tk.Toplevel(self.root)
        overlay.title("Cerrando…")
        overlay.configure(bg=PANEL)
        overlay.resizable(False, False)
        overlay.transient(self.root)
        overlay.protocol("WM_DELETE_WINDOW", lambda: None)
        overlay.geometry("460x150+%d+%d" % (
            self.root.winfo_rootx() + self.root.winfo_width() // 2 - 230,
            self.root.winfo_rooty() + self.root.winfo_height() // 2 - 75,
        ))
        tk.Label(overlay, text="Deteniendo el clúster…", bg=PANEL, fg=FG,
                 font=(FONT, 12, "bold")).pack(pady=(26, 4))
        tk.Label(overlay,
                 text=("docker rm -f adas_* por SSH + procesos nativos de Windows"
                       if IS_WINDOWS else
                       "docker rm -f adas_* en ADASCore, zorros-desk y HMI"),
                 bg=PANEL, fg=MUTED, font=(FONT, 8)).pack()
        progress = ttk.Progressbar(overlay, mode="indeterminate", length=320,
                                   style="Adas.Horizontal.TProgressbar")
        progress.pack(pady=16)
        progress.start(12)
        self._overlay = overlay

        threading.Thread(target=self._shutdown_worker, args=(self._nodes_snapshot,),
                         name="adas-shutdown", daemon=True).start()

    def _shutdown_worker(self, nodes: tuple) -> None:
        acquired = False
        try:
            acquired = self._op_lock.acquire(timeout=60)
            self._terminate_processes()
            self._cleanup(nodes, "cierre de la aplicación")
        except Exception as exc:
            self._log("err", f"Error durante el cierre: {exc}")
        finally:
            if acquired:
                self._op_lock.release()
            self.q.put(("closing_done", None))

    def _finish_close(self) -> None:
        self._destroyed = True
        self._shutdown_evt.set()
        try:
            if self._overlay is not None and self._overlay.winfo_exists():
                self._overlay.destroy()
        except tk.TclError:
            pass
        try:
            self.root.destroy()
        except tk.TclError:
            pass


# ══════════════════════════════════════════════════════════════════════════════
#  Punto de entrada
# ══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        if IS_WINDOWS:
            raise SystemExit(
                "No se pudo abrir la ventana Tk en Windows: {}\n"
                "Comprueba que tu Python incluye Tkinter (python -m tkinter) y vuelve a "
                "ejecutar iniciar_windows.ps1.".format(exc)
            )
        raise SystemExit(
            "No se pudo abrir la ventana Tk (¿falta DISPLAY / servidor X11?): {}\n"
            "Ejecute el panel dentro de la sesión gráfica de Ubuntu (DISPLAY=:0).".format(exc)
        )
    Dashboard(root)
    root.mainloop()


if __name__ == "__main__":
    main()
