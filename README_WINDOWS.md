# ADAS Cluster — Guía para Windows 10/11 (HMI nativo)

Esta guía permite **clonar el repositorio en cualquier PC con Windows 10/11**, instalar las
dependencias y trabajar de inmediato con el clúster **sin modificar nada de lo que corre en
las Raspberry Pi**.

La arquitectura es **híbrida**:

* El **HMI / nodo central** (`adas_dashboard.py` + `main_sub_ai.py`) corre **nativo en
  Windows**: Tkinter y OpenCV dibujan directamente en la pantalla del PC, sin `xhost` y sin
  `DISPLAY=:0`.
* Los **nodos Edge** siguen siendo las Raspberry Pi Ubuntu ARM64. El dashboard las gobierna
  por **SSH (`BatchMode=yes`) + Docker**, exactamente como antes, con sus rutas
  `$HOME/Adas-agosto26/...`.

```text
        PC WINDOWS 10/11 (este repositorio)                    SUBNET 192.168.50.0/24
   ┌──────────────────────────────────────────┐   eCAL 239.0.0.1:14000 (UDP) + TCP
   │ .venv\Scripts\python.exe                 │   ───────────────────────────────────
   │   adas_dashboard.py   (Tkinter, nativo)  │
   │   main_sub_ai.py      (OpenCV, nativo)   │        ┌────────────────────────────┐
   │   main_pub.py         (webcam local)     │  SSH   │ ADASCore 192.168.50.61     │
   │                                          │ ─────► │ Pi 5 + Hailo-8L (adas:pi5) │
   │ eCAL 5.12 (msi) + ecal_windows.ini       │        └────────────────────────────┘
   │ ssh de OpenSSH para Windows              │  SSH   ┌────────────────────────────┐
   └──────────────────────────────────────────┘ ─────► │ zorros-desk 192.168.50.32  │
                                                       │ ARM64 CPU (adas:edge-cpu)  │
                                                       └────────────────────────────┘
```

> **Por qué no Docker en Windows:** Docker Desktop (WSL2) aísla el multicast UDP
> `239.0.0.1:14000` de eCAL, así que un contenedor Linux en el PC no descubre a las
> Raspberry Pi. Por eso los procesos del HMI se ejecutan con Python nativo y solo los
> contenedores de las Pi se lanzan por SSH.

---

## Índice

1. [Requisitos](#1-requisitos)
2. [Clonar el repositorio](#2-clonar-el-repositorio)
3. [Python y entorno virtual](#3-python-y-entorno-virtual)
4. [eCAL 5.12 en Windows](#4-ecal-512-en-windows)
5. [Dependencias Python](#5-dependencias-python)
6. [Llaves SSH para los nodos](#6-llaves-ssh-para-los-nodos)
7. [Firewall de Windows](#7-firewall-de-windows)
8. [Arrancar el panel](#8-arrancar-el-panel)
9. [Los 5 modos en Windows](#9-los-5-modos-en-windows)
10. [Publicador local con webcam](#10-publicador-local-con-webcam-opcional)
11. [Rutas y configuración](#11-rutas-y-configuración)
12. [Solución de problemas](#12-solución-de-problemas)
13. [Qué NO cambia en las Raspberry Pi](#13-qué-no-cambia-en-las-raspberry-pi)

---

## 1. Requisitos

| Componente | Versión / notas |
|---|---|
| Windows | 10 (21H2 o superior) u 11, 64 bits |
| Python | **3.10 – 3.12, 64 bits** (python.org, marcar *Add python.exe to PATH*) |
| eCAL | **5.12** (`.msi` oficial) + wheel del binding `ecal5` para tu Python |
| Git | Git for Windows (incluye Git Bash, opcional) |
| OpenSSH Client | Característica opcional de Windows, ya viene activa en Windows 11 |
| Red | El PC y las Pi en la **misma subred** (`192.168.50.0/24`) |
| Cámara | Webcam USB o integrada (se abre por índice: `0`, `1`, `2`…) |

Comprueba las versiones:

```powershell
python --version
git --version
ssh -V
```

---

## 2. Clonar el repositorio

```powershell
cd $env:USERPROFILE\Documents
git clone <URL-DEL-REPOSITORIO> Adas-agosto26
cd Adas-agosto26
```

Puede estar en **cualquier disco o carpeta** (`C:\`, `D:\`, `E:\`, OneDrive, etc.): todos los
scripts resuelven sus rutas con `pathlib.Path(__file__).resolve().parent` (Python) y
`$PSScriptRoot` (PowerShell). No hay rutas absolutas ni usuarios fijos en el código.

---

## 3. Python y entorno virtual

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1        # opcional, para trabajar en la terminal
python -m pip install --upgrade pip
```

`venv/`, `.venv/`, `env/` están en `.gitignore`: el entorno nunca se sube al repositorio.

> Si PowerShell bloquea la activación:
> `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`

---

## 4. eCAL 5.12 en Windows

El binding de Python **no** se instala con `pip install ecal`: el paquete de eCAL 5.12 se llama
`ecal5` y se distribuye como wheel. Pasos:

1. Descarga e instala el **`.msi` de eCAL 5.12** desde
   <https://github.com/eclipse-ecal/ecal/releases> (elige la versión 5.12 para Windows x64).
2. Descarga el **wheel del binding** de la misma página
   (`ecal5-<version>-<cp310|cp311|cp312>-win_amd64.whl`) e instálalo en el venv:

   ```powershell
   .\.venv\Scripts\python -m pip install C:\Users\<tu-usuario>\Downloads\ecal5-*-win_amd64.whl
   ```

3. Verifica:

   ```powershell
   .\.venv\Scripts\python -c "import ecal.core.core as e; print(e.getversion())"
   ```

### `ecal_windows.ini` y `ECAL_DATA`

eCAL 5.12 lee su configuración, por orden, de `%ECAL_DATA%\ecal.ini` y de
`%ProgramData%\eCAL\ecal.ini`. El repositorio incluye **`ecal_windows.ini`** con los valores
del clúster:

```ini
[network]
network_enabled   = true
multicast_group   = 239.0.0.1
multicast_port    = 14000
multicast_ttl     = 2

[publisher]
use_tcp           = 1
use_shm           = 0
use_udp_mc        = 0
```

`utils/helper_functions.py::prepare_ecal_config()` lo copia a
`%LOCALAPPDATA%\ADAS\ecal\ecal.ini` y exporta `ECAL_DATA` **antes** de importar `ecal`, así que
cualquier `main_*.py` (o el dashboard) lo carga automáticamente sin permisos de administrador.
`iniciar_windows.ps1` hace lo mismo al arrancar, para que los subprocesos hereden la variable.

> Si ya tienes una configuración propia en `%ProgramData%\eCAL\ecal.ini`, exporta
> `ECAL_DATA` a tu carpeta: el código respeta la variable existente y no la sobrescribe.

---

## 5. Dependencias Python

```powershell
.\.venv\Scripts\python -m pip install -r requirements-windows.txt
```

`requirements-windows.txt` incluye `numpy`, `opencv-python`, `protobuf`, `lz4`,
`ultralytics`, `torch`, `torchvision` y `pika` (solo si usas RabbitMQ). Usa las ruedas **CPU**
de PyTorch (`--extra-index-url .../whl/cpu`), que es lo que necesita `main_pub.py` con
`--backend cpu` y `main_sub_ai.py --backend cpu` (MODO 3).

Verificación rápida:

```powershell
.\.venv\Scripts\python -c "import cv2, numpy, torch, ultralytics; print('deps OK')"
```

---

## 6. Llaves SSH para los nodos

El dashboard usa `ssh -o BatchMode=yes`: **nunca** pide contraseña, así que la llave debe
estar autorizada en las Pi.

```powershell
# 1) Llave (si aún no existe)
ssh-keygen -t ed25519 -C "adas-windows"

# 2) Copiar la llave pública a cada nodo (pide la contraseña una sola vez)
type $env:USERPROFILE\.ssh\id_ed25519.pub | ssh core@192.168.50.61 "mkdir -p ~/.ssh && chmod 700 ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"
type $env:USERPROFILE\.ssh\id_ed25519.pub | ssh zorro@192.168.50.32 "mkdir -p ~/.ssh && chmod 700 ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"

# 3) Probar sin contraseña
ssh -o BatchMode=yes core@192.168.50.61 "echo PING=1; docker info >/dev/null && echo DOCKER=1"
ssh -o BatchMode=yes zorro@192.168.50.32 "echo PING=1; docker info >/dev/null && echo DOCKER=1"
```

Si `ssh` no existe: **Configuración → Aplicaciones → Características opcionales → Cliente
OpenSSH**.

Usuario e IP de cada nodo siguen siendo **editables en el dashboard** (sección
*CONFIGURACIÓN DE NODOS*); los valores de arriba son solo los predeterminados.

---

## 7. Firewall de Windows

eCAL necesita:

* **UDP 14000** para el descubrimiento multicast `239.0.0.1`.
* **TCP 14000** (y el resto de puertos efímeros de eCAL) para el payload.

`iniciar_windows.ps1` comprueba las reglas y, si se ejecuta **como administrador**, las crea:

```powershell
New-NetFirewallRule -DisplayName "ADAS eCAL UDP 14000 (in)"  -Direction Inbound  -Action Allow -Protocol UDP -LocalPort 14000 -Profile Private,Domain
New-NetFirewallRule -DisplayName "ADAS eCAL UDP 14000 (out)" -Direction Outbound -Action Allow -Protocol UDP -LocalPort 14000 -Profile Private,Domain
New-NetFirewallRule -DisplayName "ADAS eCAL TCP 14000 (in)"  -Direction Inbound  -Action Allow -Protocol TCP -LocalPort 14000 -Profile Private,Domain
New-NetFirewallRule -DisplayName "ADAS eCAL TCP 14000 (out)" -Direction Outbound -Action Allow -Protocol TCP -LocalPort 14000 -Profile Private,Domain
```

Además, marca la red del laboratorio como **Privada** (no Pública) para que las reglas
apliquen. En una red aislada de pruebas también puedes desactivar el firewall temporalmente.

---

## 8. Arrancar el panel

La forma recomendada:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\iniciar_windows.ps1
```

El script:

1. comprueba Python 3.10+ y crea/reutiliza `.venv`;
2. instala `requirements-windows.txt`;
3. verifica el binding de eCAL y prepara `ECAL_DATA` con `ecal_windows.ini`;
4. revisa/crea las reglas de firewall (UDP/TCP 14000);
5. prueba SSH con ADASCore y zorros-desk;
6. arranca `adas_dashboard.py` con el Python del venv.

Opciones disponibles:

```powershell
.\iniciar_windows.ps1 -SkipInstall -SkipFirewall
.\iniciar_windows.ps1 -CoreUser core -CoreIp 192.168.50.61 -DeskUser zorro -DeskIp 192.168.50.32
.\iniciar_windows.ps1 -SkipSshCheck -SkipEcalCheck
```

Arranque manual equivalente:

```powershell
.\.venv\Scripts\python adas_dashboard.py
```

En la ventana verás las tres tarjetas de nodo. En Windows la tarjeta **local** muestra los
chips `ssh`, `cam0` y `python` (no `docker` ni `/dev/video*`): valida el cliente OpenSSH, abre
la cámara con OpenCV y comprueba el intérprete. Las tarjetas de ADASCore y zorros-desk siguen
mostrando `SSH`, `hailo0`/`video0` y `docker` como en Linux.

---

## 9. Los 5 modos en Windows

| Modo | Qué lanza en las Raspberry Pi (SSH + Docker) | Qué lanza en Windows (nativo) |
|---|---|---|
| **1 · Edge Offloading Puro** | `adas_edge1` en ADASCore (`--edge-ai --no-video --backend hailo`) y `adas_edge2` en zorros-desk (`--backend cpu`) | `main_sub_ai.py --streams 1 2 --backend none` |
| **2 · Clúster Completo** | `edge1`/`edge3` en ADASCore y `edge2`/`edge4` en zorros-desk (estos dos últimos solo si existe `/dev/video2`) | `main_sub_ai.py --streams 1 2 3 4 --backend none` |
| **3 · Centralizado Tradicional** | `adas_raw1` y `adas_raw2` (solo video crudo) | `main_sub_ai.py --streams 1 2 --backend cpu --model yolov8n-seg.pt` |
| **4 · Detener y Limpiar** | `docker rm -f` de todos los `adas_*` por SSH | cierra los procesos nativos (`terminate` / `taskkill /T`) |
| **5 · Edge Offloading + Video** | `edge1` y `edge2` con video JPEG + metadatos de silueta | `main_sub_ai.py --streams 1 2 --backend none` |

Detalles útiles:

* **Cada escenario limpia primero**: en Windows termina los procesos locales y después hace
  `docker rm -f $(docker ps -aq --filter name=adas_)` en las dos Pi.
* **MODO 3** necesita `yolov8n-seg.pt`. Si no está en `models/`, el dashboard reescribe la ruta
  a `yolov8n-seg.pt` de la raíz del proyecto cuando ese archivo existe (descárgalo una vez:
  `python -c "from ultralytics import YOLO; YOLO('yolov8n-seg.pt')"`).
* **Cerrar la ventana** ejecuta la misma parada que el MODO 4 (contenedores remotos +
  procesos locales). Si un proceso se resiste, se usa `taskkill /F /T`.
* La **telemetría** (`docker stats`) sigue siendo de las Pi; el nodo local no aporta filas
  porque no ejecuta contenedores.

---

## 10. Publicador local con webcam (opcional)

Para probar el pipeline sin las Pi, abre **dos terminales** en el proyecto:

```powershell
# Terminal 1 — publicador con la webcam del PC (índice 0, 1, 2...)
.\.venv\Scripts\python main_pub.py --stream-id 1 --camera 0 --edge-ai --backend cpu --model yolov8n-seg.pt

# Terminal 2 — HMI nativo suscrito a ese stream
.\.venv\Scripts\python main_sub_ai.py --streams 1 --backend none
```

* La cámara se abre **por índice** con `cv2.CAP_DSHOW` y, si falla, `cv2.CAP_MSMF`.
* No se usan `/dev/video*` ni `v4l2-ctl`: pasar una ruta POSIX produce un error claro.
* `--no-preview` oculta la ventana del publicador; `q` la cierra; `p` pausa.
* Opcionalmente (`--enable-yolo`) se puede usar el YOLO legacy de preview en CPU.

---

## 11. Rutas y configuración

* `adas_dashboard.py` resuelve la raíz con `Path(__file__).resolve().parent` y lanza los
  procesos locales con `sys.executable` (el Python del venv), con `cwd` en la raíz del repo.
* Los volúmenes Docker de los nodos remotos siguen siendo `$HOME/Adas-agosto26/...`
  (calculados en la propia Pi, nunca rutas de Windows).
* Usuarios e IPs de ADASCore, zorros-desk y el nodo local son **editables en la interfaz**.
* Topics y prefijos: `utils/connection_conf.py` (`webcam_stream_1..4`).
* Umbrales de severidad: `utils/pub_sub_conf.py` (`danger_m`, `warning_m`, etc.).

---

## 12. Solución de problemas

### `No module named ecal`

Falta el wheel del binding. Instala eCAL 5.12 (`.msi`) y luego
`pip install ecal5-*-win_amd64.whl` **dentro del venv** (paso 4). Comprueba con
`.\.venv\Scripts\python -c "import ecal.core.core"`.

### El HMI muestra `Cams 0/2` o celdas sin señal

1. `ecal_windows.ini` debe tener `network_enabled = true`, `multicast_group = 239.0.0.1`,
   `multicast_port = 14000` y `use_tcp = 1` (el script lo verifica).
2. Permite **UDP 14000** y **TCP 14000** en el firewall y usa perfil de red **Privada**.
3. El PC y las Pi deben estar en la misma subred y sin VPN que bloquee multicast.
4. La Pi debe tener `--network host` en sus contenedores (ya lo hace el dashboard).
5. `%ECAL_DATA%` debe apuntar a la carpeta que contiene `ecal.ini`:
   `echo $env:ECAL_DATA` (por defecto `%LOCALAPPDATA%\ADAS\ecal`).

### `ssh: command not found` / los nodos aparecen en rojo

Activa el **Cliente OpenSSH** y comprueba `ssh -o BatchMode=yes core@192.168.50.61 "echo ok"`.
Si pide contraseña, la llave no está en `authorized_keys` del nodo (paso 6).

### La cámara no abre en Windows

* Usa índices: `--camera 0`, `--camera 1`… (nunca `/dev/video0`).
* Cierra Teams/Zoom/Cámara de Windows: una webcam solo admite un proceso.
* Prueba el otro backend: si DirectShow falla, el código ya reintenta con Media Foundation.
* Comprueba el índice real:
  `.\.venv\Scripts\python -c "import cv2; print([i for i in range(5) if cv2.VideoCapture(i, cv2.CAP_DSHOW).isOpened()])"`

### `UnicodeEncodeError` o acentos/emojis raros en la consola

El dashboard y `iniciar_windows.ps1` exportan `PYTHONIOENCODING=utf-8` y `PYTHONUTF8=1`, y los
subprocesos se leen como UTF-8. Si arrancas a mano en una consola antigua:

```powershell
chcp 65001
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
```

### El proceso local muere al instante

El dashboard lo detecta a los 2 s y muestra el código de salida y la salida del proceso en la
consola. Causas típicas: falta `torch`/`ultralytics`, modelo inexistente, cámara ocupada o
falta el binding de eCAL.

### `duplicate file name imagen.proto`

No regeneres `core/imagen_pb2.py` con `protoc` en Windows: es un puente a
`protos/imagen_pb2.py`. El proyecto unifica ambos módulos en
`interfaces/middleware_factory.py`.

### `torch` ocupa mucho / la instalación tarda

`requirements-windows.txt` ya usa las ruedas CPU. Si tienes GPU NVIDIA y la quieres, instala
`torch`/`torchvision` aparte con el índice CUDA de pytorch.org.

### El panel abre pero no hay ventana de video del HMI

En Windows no hay X11: el visualizador `main_sub_ai.py` abre su propia ventana de OpenCV. Si no
aparece, revisa la consola del panel (proceso nativo finalizado) o ejecútalo a mano:

```powershell
.\.venv\Scripts\python main_sub_ai.py --streams 1 2 --backend none
```

---

## 13. Qué NO cambia en las Raspberry Pi

* `ecal.ini`, `Dockerfile`, `docker-compose.yml` y las imágenes `adas:pi5` / `adas:edge-cpu`
  siguen igual.
* El dashboard continúa lanzando los contenedores remotos con
  `--network host --ipc host --pid host`, `$HOME/Adas-agosto26/ecal.ini` y
  `$HOME/Adas-agosto26/models`.
* El HMI sobre **Ubuntu ARM64** sigue funcionando como antes: si `os.name != "nt"`, se usa
  `docker run` local con X11 y `xhost`; las ramas de Windows no se ejecutan.
* `ecal_windows.ini` solo se lee en Windows; en Linux la configuración efectiva es `ecal.ini`.
