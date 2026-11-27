# ADAS Cluster — Edge Offloading sobre 3 Raspberry Pi

Sistema distribuido de percepción vehicular (ADAS) construido íntegramente sobre **tres Raspberry Pi con Ubuntu ARM64** conectadas en la subred `192.168.50.0/24`. Cada nodo periférico ejecuta segmentación semántica **YOLOv8n-seg** en el borde —con aceleración NPU **Hailo-8L** o CPU— y publica el resultado por **eCAL** usando **Protocol Buffers**. El nodo central solo recibe, fusiona y visualiza: nunca satura su CPU con video RGB crudo.

---

## Índice

1. [Descripción general del proyecto](#1-descripción-general-del-proyecto)
2. [Arquitectura del clúster Raspberry Pi](#2-arquitectura-del-clúster-raspberry-pi)
3. [Estructura del repositorio](#3-estructura-del-repositorio)
4. [Manual de instalación paso a paso](#4-manual-de-instalación-paso-a-paso-en-cada-raspberry-pi)
5. [Qué tomar en cuenta antes de ejecutar](#5-qué-tomar-en-cuenta-antes-de-ejecutar)
6. [Guía de ejecución y los 5 modos](#6-guía-de-ejecución-y-manejo-de-los-5-modos-del-sistema)
7. [Solución de problemas](#7-solución-de-problemas-troubleshooting)

---

## 1. Descripción general del proyecto

### El problema

Un ADAS centralizado tradicional envía **video RGB crudo** desde cada cámara al nodo de cómputo, donde se ejecuta la red neuronal. En una Raspberry Pi eso implica:

- Decodificar 2–4 streams MJPEG a 640×480 y 30 FPS en la CPU central.
- Ejecutar YOLOv8-seg sobre cada frame: cientos de milisegundos por frame en ARM.
- Consumir ancho de banda de red y RAM proporcionalmente al número de cámaras.

El resultado es una unidad central saturada, latencia alta y FPS inservibles para seguridad vehicular.

### La solución: Edge Offloading

La inferencia se mueve **al nodo que tiene la cámara**:

1. Cada nodo Edge captura su cámara USB (`/dev/video0`, `/dev/video2`).
2. Ejecuta **YOLOv8n-seg** localmente:
   - **ADASCore** usa la NPU **Hailo-8L** (HailoRT 4.23, modelo `yolov8n_seg.hef`).
   - **zorros-desk** usa CPU ARM64 (modelo `yolov8n-seg.pt` vía Ultralytics/PyTorch).
3. El resultado se serializa en un mensaje **Protobuf** `VideoFrame` que contiene, por objeto detectado: caja delimitadora, clase, confianza, **polígono de la máscara de segmentación**, distancia sobre el eje óptico (`z_m`), desplazamiento lateral (`lateral_m`) y distancia total al parachoques (`dist_m`).
4. En modo **solo metadatos** (`--no-video`) `frame_data` viaja vacío: cada frame pesa **~0.1–0.3 KB** en lugar de decenas de KB de JPEG.
5. El middleware **eCAL** distribuye los topics `webcam_stream_1..4` por TCP (payload) y multicast UDP (descubrimiento).
6. El **nodo central** (`main_sub_ai.py --backend none`) no importa PyTorch, no infiere: compone la vista 2×2, proyecta los objetos en un plano **Bird's-Eye View (BEV)** centrado en el vehículo, calcula severidad por distancia y dispara el banner de alerta crítica a menos de **1.50 m**.

### Componentes tecnológicos

| Componente | Rol en el sistema |
|---|---|
| **YOLOv8n-seg** | Segmentación de instancias: caja + máscara poligonal + clase (80 clases COCO). |
| **Hailo-8L (AI HAT+)** | NPU de 13 TOPS en la Raspberry Pi 5; ejecuta el `.hef` compilado a 640×640. |
| **eCAL 5.12** | Middleware pub/sub de baja latencia con descubrimiento automático y transporte TCP para el payload. |
| **Protocol Buffers** | Esquema binario compacto (`protos/imagen.proto`) para frames y metadatos. |
| **OpenCV** | Captura V4L2, codificación JPEG/LZ4/PNG, dibujo de overlays y ventanas del HMI. |
| **BEV** | Proyección cartográfica de las detecciones al plano del suelo con rejilla de 0.5 m. |
| **Tkinter** | Panel de control y orquestación Docker del nodo central (`adas_dashboard.py`). |

---

## 2. Arquitectura del clúster Raspberry Pi

### Topología

```
                        SUBNET 192.168.50.0/24  ·  eCAL 239.0.0.1:14000 (UDP) + TCP payload
                        ────────────────────────────────────────────────────────────────────

   ┌───────────────────────────────┐        ┌───────────────────────────────┐
   │  ADASCore · 192.168.50.61     │        │  zorros-desk · 192.168.50.32  │
   │  Raspberry Pi 5 + AI HAT+     │        │  Raspberry Pi ARM64 (CPU)     │
   │  Hailo-8L · adas:pi5          │        │  adas:edge-cpu                │
   │                               │        │                               │
   │  /dev/hailo0                  │        │                               │
   │  /dev/video0  → stream 1      │        │  /dev/video0  → stream 2      │
   │  /dev/video2  → stream 3      │        │  /dev/video2  → stream 4      │
   │                               │        │                               │
   │  main_pub.py --edge-ai        │        │  main_pub.py --edge-ai        │
   │  --backend hailo              │        │  --backend cpu                │
   └───────────────┬───────────────┘        └───────────────┬───────────────┘
                   │                                        │
                   │        Protobuf VideoFrame             │
                   │        (detecciones + JPEG opcional)   │
                   └───────────────────┬────────────────────┘
                                       │
                        ┌──────────────▼────────────────────────┐
                        │  adas-central · 192.168.50.63         │
                        │  Raspberry Pi · Unidad Central / HMI  │
                        │  Pantalla local · X11 :0              │
                        │  imagen adas:edge-cpu                 │
                        │                                       │
                        │  adas_dashboard.py  (Tkinter)         │
                        │  main_sub_ai.py --streams 1 2         │
                        │      --backend none  (sin inferencia) │
                        │                                       │
                        │  Vista 2×2 + BEV + alertas + CSV      │
                        └───────────────────────────────────────┘
```

### Nodos

| Nodo | IP | Hardware | Usuario | Imagen Docker | Dispositivos | Modelo | Rol |
|---|---|---|---|---|---|---|---|
| **ADASCore** | `192.168.50.61` | Raspberry Pi 5 + AI HAT+ Hailo-8L | `core` | `adas:pi5` | `/dev/hailo0`, `/dev/video0`, `/dev/video2` | `models/yolov8n_seg.hef` | Nodo intermedio 1 · Edge AI con NPU |
| **zorros-desk** | `192.168.50.32` | Raspberry Pi ARM64 (CPU) | `zorro` | `adas:edge-cpu` | `/dev/video0` (+ `/dev/video2` opcional) | `models/yolov8n-seg.pt` | Nodo intermedio 2 · Edge AI en CPU |
| **adas-central** | `192.168.50.63` | Raspberry Pi con pantalla local | usuario local | `adas:edge-cpu` | — (solo X11) | — | Unidad central de visualización y orquestación |

### Puertos y multicast eCAL

| Parámetro | Valor | Archivo |
|---|---|---|
| Grupo multicast de registro | `239.0.0.1` | `ecal.ini` → `[network] multicast_group` |
| Máscara | `0.0.0.15` | `multicast_mask` |
| Puerto | `14000` (UDP) | `multicast_port` |
| TTL | `2` | `multicast_ttl` |
| Transporte del payload | TCP (`use_tcp = 1`) | `[publisher]` |
| Topics | `webcam_stream_1` … `webcam_stream_4` | `utils/connection_conf.py` |

> El descubrimiento es multicast; los frames viajan por TCP porque los nodos son hosts distintos (la memoria compartida de eCAL solo sirve dentro del mismo host).

---

## 3. Estructura del repositorio

### Directorios

| Ruta | Función |
|---|---|
| `core/` | Compatibilidad histórica del esquema Protobuf. `core/imagen_pb2.py` es el respaldo del esquema; el canónico es `protos/imagen_pb2.py` y `interfaces/middleware_factory.py` los unifica para evitar el error de descriptor duplicado. |
| `data_stream/` | Gestores de flujo. `pubs_stream_manager.py` serializa y publica con métricas; `subs_stream_manager.py` recibe video crudo multi-stream; `ai_subs_stream_manager.py` es el receptor híbrido del HMI (frame + detecciones, solo metadatos, o video sin IA) con FPS, latencia y pérdidas. |
| `img_processing/` | Núcleo de percepción. `adas_detector.py` (detector asíncrono Hailo/CPU), `adas_geometry.py` (distancias, severidad, posición EGO), `video_serializer.py` (frame ↔ Protobuf, JPEG/LZ4/PNG) y `yolo_processor.py` (YOLO legacy opcional). |
| `interfaces/` | Abstracción de middleware. `middleware_factory.py` elige eCAL o RabbitMQ; `ecal_interface.py` implementa `ProtoPublisher`/`ProtoSubscriber`; `rabbitmq_interface.py` es la alternativa AMQP. |
| `mapeo_cartesiano/` | Carpeta del diseño inicial del mapeo al plano cartesiano. En esta versión su módulo está vacío; la implementación vigente vive en `img_processing/adas_geometry.py` y `user_interface/adas_renderer.py` (`BEVRenderer`). |
| `metrica_bev_banner/` | Carpeta del diseño inicial de métricas, BEV y banner. Su módulo está vacío; el dashboard y el banner actuales están en `user_interface/ai_grid_visualizer.py` y `user_interface/adas_renderer.py`. |
| `excluyente/` | Carpeta de experimentos excluyentes (geometría alternativa). Su módulo está vacío; se conserva por trazabilidad del diseño. |
| `protos/` | Esquema de comunicación. `imagen.proto` (fuente), `imagen_pb2.py` (generado, canónico) e `imagen.desc` (descriptor serializado). |
| `user_interface/` | Todo lo visual. `pub_visualizer.py` (captura + preview del emisor), `ai_grid_visualizer.py` (dashboard 1280×720: 2×2 + BEV + banner), `adas_renderer.py` (máscaras, cuboides 3D, rejilla BEV), `sub_test_visualizer.py` (visor de video crudo sin IA) y `text_labels_overlay.py`. |
| `utils/` | Configuración central. `pub_sub_conf.py` (resolución, FPS, umbrales ADAS, severidad, BEV), `connection_conf.py` (middleware y topic prefix), `args_conf.py` (CLI del publisher), `helper_functions.py`. |
| `models/` | Modelos locales: `yolov8n_seg.hef` y `yolov8n-seg.pt`. **No se versiona** (ver `.gitignore`). |
| `hailo_pkg/` | Artefactos HailoRT para construir `adas:pi5`: `libhailort.so*` y `hailort-4.23.0-cp312-cp312-linux_aarch64.whl`. **No se versiona.** |

### Archivos principales

| Archivo | Función |
|---|---|
| `adas_dashboard.py` | HMI central en Tkinter (tema oscuro). Orquesta por SSH + Docker los 3 nodos, muestra estado cada 5 s, telemetría `docker stats --no-stream` cada 3 s, consola con colores, exportación CSV y los 5 escenarios. Todo el I/O corre en hilos con `queue.Queue`. |
| `main_pub.py` | Emisor. Captura cámara, opcionalmente infiere en el borde (`--edge-ai`) y publica `VideoFrame`. Con `--no-video` publica únicamente metadatos. |
| `main_sub_ai.py` | Receptor/agregador del HMI. Se suscribe a 1–4 streams, fusiona detecciones en BEV, dispara alertas y dibuja el dashboard. `--backend none` evita importar PyTorch. |
| `main_sub.py` | Visor alternativo de video crudo multi-stream (sin IA). |
| `ecal.ini` | Configuración eCAL común a los 3 nodos: multicast `239.0.0.1:14000`, TCP para payload, SHM/UDP desactivados en el publisher. Se monta en `/app/ecal.ini` y `/etc/ecal/ecal.ini`. |
| `Dockerfile` | Imagen multi-perfil ARM64 sobre `ubuntu:24.04`. `WITH_TORCH=0` → `adas:pi5` (HailoRT 4.23, sin PyTorch). `WITH_TORCH=1` → `adas:edge-cpu` (torch + torchvision + ultralytics). Instala eCAL 5.12 desde el PPA y habilita `network_enabled = true`. |
| `docker-compose.yml` | Lanzamiento por perfiles (`pi5`, `pi5-standalone`, `dev`, `pi4`) para pruebas rápidas y hot-reload. |
| `iniciar_camaras.sh` | Script auxiliar que detecta cámaras V4L2 reales (índice 0) y levanta un publicador por cada una más el visor. |
| `.env` | Define `COMPOSE_PROFILES` para `docker compose`. |
| `.gitignore` / `.dockerignore` | Exclusión de modelos pesados, `hailo_pkg/`, `venv/`, `__pycache__/`, CSV y logs. |

### Esquema Protobuf

`protos/imagen.proto` define:

```
VideoFrame
 ├── frame_data, width, height, channels, encoding, frame_number, timestamp
 ├── compression_quality, is_keyframe
 ├── is_metadata_only      # true = sin video (Edge Offloading puro)
 ├── inference_ms, ai_fps  # métricas del nodo Edge
 └── objects[] : DetectedObject
       ├── x1, y1, x2, y2, cls_id, label, conf
       ├── z_m, lateral_m, dist_m, real_width_m
       └── polygon[] : Point2D   # máscara de segmentación
```

---

## 4. Manual de instalación paso a paso en cada Raspberry Pi

### Paso A — Preparación común en las 3 Raspberry Pi (Ubuntu ARM64)

**A.1 — Docker Engine y permisos sin `sudo`**

```bash
sudo apt update
sudo apt install -y ca-certificates curl gnupg git python3 python3-pip python3-tk
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg

echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker "$USER"
newgrp docker          # o cerrar sesión y volver a entrar
docker run --rm hello-world
```

**A.2 — Kernel preparado para multicast eCAL**

Cada nodo se une a varios grupos multicast; el límite por defecto puede quedarse corto y eCAL deja de descubrir topics.

```bash
echo 'net.ipv4.igmp_max_memberships=128' | sudo tee /etc/sysctl.d/99-adas-ecal.conf
sudo sysctl --system
sysctl net.ipv4.igmp_max_memberships     # debe devolver 128
```

**A.3 — Repositorio y estructura de modelos**

```bash
cd ~
git clone https://github.com/giezinunez2/Adas-agosto26.git
cd Adas-agosto26
mkdir -p models hailo_pkg
```

> La carpeta del proyecto **debe** llamarse `Adas-agosto26` y estar en el `$HOME` de cada nodo: el dashboard monta los volúmenes como `$HOME/Adas-agosto26/ecal.ini` en los nodos remotos.

**A.4 — Configuración de red**

Cada Raspberry Pi debe tener IP fija dentro de `192.168.50.0/24` y ver a las otras dos:

```bash
ip -4 addr show | grep 192.168.50
ping -c 3 192.168.50.61
ping -c 3 192.168.50.32
ping -c 3 192.168.50.63
```

---

### Paso B — Configuración en `ADASCore` (`192.168.50.61`, Raspberry Pi 5 + AI HAT+ Hailo-8L)

**B.1 — Driver HailoRT y verificación de la NPU**

Instala el driver HailoRT 4.23.0 para ARM64 desde el paquete oficial de Hailo (incluido con el AI HAT+ / HailoRT PCIe driver). Después verifica:

```bash
hailortcli fw-control identify
ls -l /dev/hailo0
```

Salida esperada: el firmware del Hailo-8L y el nodo de dispositivo `/dev/hailo0` presente. Si no aparece, revisa que el HAT esté bien asentado y que el driver PCIe esté cargado (`dmesg | grep -i hailo`).

**B.2 — Cámara USB**

```bash
v4l2-ctl --list-devices
ls -l /dev/video0 /dev/video2      # /dev/video2 es opcional
```

**B.3 — Modelo compilado `yolov8n_seg.hef`**

Descarga el modelo `yolov8n-seg` desde el **Hailo Model Zoo** y compílalo con el Hailo Dataflow Compiler 4.23 para Hailo-8L, entrada **640×640 RGB UINT8**:

```bash
# en una máquina de compilación con Hailo DFC (no se ejecuta en la Pi)
hailomz compile yolov8n-seg --hw-arch hailo8l --calib-path <imágenes> --classes 80
```

Copia el resultado a la Pi:

```bash
scp yolov8n_seg.hef core@192.168.50.61:~/Adas-agosto26/models/
```

**B.4 — Artefactos HailoRT para la imagen**

En `~/Adas-agosto26/hailo_pkg/` deben estar:

```
libhailort.so
libhailort.so.4.23.0
hailort-4.23.0-cp312-cp312-linux_aarch64.whl
```

**B.5 — Compilar la imagen `adas:pi5`**

```bash
cd ~/Adas-agosto26
docker build --build-arg WITH_TORCH=0 -t adas:pi5 .
```

`WITH_TORCH=0` produce la imagen ligera: HailoRT instalado, **sin PyTorch**, suficiente para publicar metadatos y para el visor con `--backend none`.

**B.6 — Prueba local de la NPU**

```bash
docker run --rm -it --network host --ipc host --pid host \
  --device /dev/hailo0 --device /dev/video0 \
  -v "$HOME/Adas-agosto26/ecal.ini:/app/ecal.ini:ro" \
  -v "$HOME/Adas-agosto26/ecal.ini:/etc/ecal/ecal.ini:ro" \
  -v "$HOME/Adas-agosto26/models:/app/models:ro" \
  adas:pi5 \
  python3 main_pub.py --stream-id 1 --camera 0 --edge-ai --no-video \
    --backend hailo --model models/yolov8n_seg.hef --no-preview
```

---

### Paso C — Configuración en `zorros-desk` (`192.168.50.32`, Raspberry Pi CPU)

**C.1 — Cámara**

```bash
v4l2-ctl --list-devices
ls -l /dev/video0
```

**C.2 — Modelo `yolov8n-seg.pt`**

```bash
cd ~/Adas-agosto26/models
# opción 1: descarga directa de Ultralytics
python3 -c "from ultralytics import YOLO; YOLO('yolov8n-seg.pt')"
# opción 2: copiarlo desde otro nodo
scp yolov8n-seg.pt zorro@192.168.50.32:~/Adas-agosto26/models/
```

**C.3 — Compilar la imagen `adas:edge-cpu`**

```bash
cd ~/Adas-agosto26
docker build --build-arg WITH_TORCH=1 -t adas:edge-cpu .
```

`WITH_TORCH=1` instala `torch`, `torchvision`, `ultralytics` y OpenCV con backend Qt/X11. Es la imagen que ejecuta la inferencia en CPU y también la que usa el nodo central para el visor.

**C.4 — Prueba local**

```bash
docker run --rm -it --network host --ipc host --pid host \
  --device /dev/video0 \
  -v "$HOME/Adas-agosto26/ecal.ini:/app/ecal.ini:ro" \
  -v "$HOME/Adas-agosto26/ecal.ini:/etc/ecal/ecal.ini:ro" \
  -v "$HOME/Adas-agosto26/models:/app/models:ro" \
  adas:edge-cpu \
  python3 main_pub.py --stream-id 2 --camera 0 --edge-ai --no-video \
    --backend cpu --model models/yolov8n-seg.pt --no-preview
```

---

### Paso D — Configuración en la Raspberry Pi Central / HMI (`192.168.50.63`)

**D.1 — Llaves SSH sin contraseña**

El dashboard usa `ssh -o BatchMode=yes`: si pide contraseña, el nodo se marca como caído.

```bash
ssh-keygen -t ed25519 -N "" -f ~/.ssh/id_ed25519      # si aún no existe
ssh-copy-id core@192.168.50.61
ssh-copy-id zorro@192.168.50.32

ssh -o BatchMode=yes core@192.168.50.61 'echo ok'
ssh -o BatchMode=yes zorro@192.168.50.32 'echo ok'
```

**D.2 — Entorno gráfico X11**

El contenedor del visualizador necesita permiso para abrir la ventana en el servidor X local:

```bash
export DISPLAY=:0
xhost +local:docker
```

El dashboard ejecuta `xhost +local:docker` automáticamente antes de lanzar escenarios con GUI, pero conviene comprobarlo a mano.

**D.3 — Imagen Docker local**

```bash
cd ~/Adas-agosto26
mkdir -p models
# yolov8n-seg.pt solo si se va a usar MODO 3 (inferencia local en la central)
docker build --build-arg WITH_TORCH=1 -t adas:edge-cpu .
```

**D.4 — Dependencias del panel**

```bash
sudo apt install -y python3-tk
python3 -c "import tkinter; print('Tk OK')"
docker ps       # debe funcionar sin sudo
```

---

## 5. Qué tomar en cuenta antes de ejecutar

### 5.1 — `ecal.ini` correcto en los 3 nodos

El archivo debe tener el descubrimiento de red activo:

```ini
[network]
network_enabled = true
multicast_group = 239.0.0.1
multicast_port  = 14000
```

Y el publisher debe usar TCP para el payload:

```ini
[publisher]
use_tcp = 1
use_shm = 0
use_udp_mc = 0
```

Sin `network_enabled = true`, cada contenedor se descubre solo a sí mismo: el HMI muestra `Cams 0/4`.

### 5.2 — Flags de red obligatorios

Todo `docker run` del clúster usa:

```bash
--network host --ipc host --pid host
```

- `--network host` → mismo dominio multicast que el host; imprescindible para eCAL.
- `--ipc host` → permite la memoria compartida cuando emisor y receptor coinciden en el mismo nodo.
- `--pid host` → los procesos del contenedor son visibles y terminables desde el host.

Además, los tres `--add-host` resuelven los nombres del clúster dentro del contenedor:

```
--add-host=ADASCore:192.168.50.61
--add-host=giezinunezpc:192.168.50.63
--add-host=zorros-desk:192.168.50.32
```

### 5.3 — Dispositivos `/dev/video*` reales

```bash
v4l2-ctl --list-devices
fuser -v /dev/video0
```

Verifica que la cámara exista **antes** de lanzar: un `--device /dev/video2` inexistente hace que `docker run` falle al instante. El dashboard ya sonda los dispositivos de los nodos y omite los contenedores opcionales (`edge3`/`edge4`) cuando falta la cámara.

### 5.4 — Firewall

El multicast de eCAL necesita UDP 14000 y el payload viaja por TCP en puertos efímeros. En una red de laboratorio aislada:

```bash
sudo ufw status
sudo ufw disable        # solo en la subred de pruebas
```

Si debes mantener el firewall activo, permite al menos `239.0.0.0/8` y el rango TCP de eCAL.

### 5.5 — Modelos y directorios montados

- `models/` debe existir en cada nodo (aunque esté vacío para el HMI).
- `ecal.ini` debe existir en `~/Adas-agosto26/` de cada nodo.
- El dashboard monta `ecal.ini` en `/app/ecal.ini` y `/etc/ecal/ecal.ini` en modo lectura.

### 5.6 — Configuración de severidad

En `utils/pub_sub_conf.py` (`AdasConfig`):

| Parámetro | Valor por defecto | Significado |
|---|---|---|
| `danger_m` | `1.50` | Distancia por debajo de la cual se dispara la alerta roja. |
| `warning_m` | `3.00` | Zona amarilla. |
| `stream_timeout_s` | `2.0` | Sin frames durante este tiempo → cámara inactiva. |
| `focal_length` | `700.0` | Focal calibrada para 640 px de ancho. |

---

## 6. Guía de ejecución y manejo de los 5 modos del sistema

### 6.1 — Arrancar el panel en la Raspberry Pi Central

```bash
cd ~/Adas-agosto26
export DISPLAY=:0
python3 adas_dashboard.py
```

La ventana muestra:

- **Cabecera** con el escenario activo.
- **Tres tarjetas de nodo** con LEDs y chips `SSH · hailo0 · video0 · docker`, refrescadas cada **5 s**.
- **Configuración de nodos** (usuario e IP editables; se aplica al siguiente escenario).
- **Cinco tarjetas de escenario** (MODO 1–4 en cuadrícula 2×2 y MODO 5 en franja ancha).
- **Telemetría** `docker stats --no-stream` cada **3 s** con CPU y RAM por contenedor `adas_*`.
- **Consola de logs** con marcas de tiempo y colores (`ok`, `warn`, `err`, `cmd`, `sys`).

Al iniciar cualquier escenario, el dashboard **limpia primero** los contenedores `adas_*` de los 3 nodos (`docker rm -f $(docker ps -aq --filter name=adas_)`), habilita X11 y luego lanza los jobs en paralelo. Tras 1.6 s verifica cada contenedor con `docker inspect`; si murió, muestra `docker logs --tail 20` o la salida de `docker run` si `--rm` ya lo eliminó.

### 6.2 — MODO 1 · Edge Offloading Puro (Solo Metadatos)

- **Etiqueta:** `2 CÁMARAS · SOLO METADATOS`
- **Qué hace:** Hailo-8L (cámara 1) y CPU (cámara 2) infieren en el borde y envían **solo metadatos**; el HMI no infiere.

| Nodo | Contenedor | Comando |
|---|---|---|
| ADASCore | `adas_edge1` | `python3 main_pub.py --stream-id 1 --camera 0 --edge-ai --no-video --backend hailo --model models/yolov8n_seg.hef --no-preview` |
| zorros-desk | `adas_edge2` | `python3 main_pub.py --stream-id 2 --camera 0 --edge-ai --no-video --backend cpu --model models/yolov8n-seg.pt --no-preview` |
| HMI | `adas_central_B` | `python3 main_sub_ai.py --streams 1 2 --backend none` |

- **Tráfico:** Protobuf sin `frame_data` (`~0.1–0.3 KB/frame`).
- **HMI:** cada celda muestra un canvas HUD calibrado (`META`) con polígonos y cuboides; CPU de la central casi plana.
- **Ideal para:** medir el coste real de la inferencia distribuida y el ahorro de ancho de banda.

### 6.3 — MODO 2 · Clúster Completo (Multi-cámara)

- **Etiqueta:** `4 CÁMARAS · MIXTO HAILO + CPU + VIDEO`
- **Qué hace:** `edge1`/`edge2` envían metadatos y `edge3`/`edge4` video crudo; el HMI se suscribe a 4 streams.

| Nodo | Contenedor | Comando |
|---|---|---|
| ADASCore | `adas_edge1` | `python3 main_pub.py --stream-id 1 --camera 0 --edge-ai --no-video --backend hailo --model models/yolov8n_seg.hef --no-preview` |
| ADASCore | `adas_edge3` | `python3 main_pub.py --stream-id 3 --camera 2 --no-preview` |
| zorros-desk | `adas_edge2` | `python3 main_pub.py --stream-id 2 --camera 0 --edge-ai --no-video --backend cpu --model models/yolov8n-seg.pt --no-preview` |
| zorros-desk | `adas_edge4` | `python3 main_pub.py --stream-id 4 --camera 2 --edge-ai --no-video --backend cpu --model models/yolov8n-seg.pt --no-preview` |
| HMI | `adas_central` | `python3 main_sub_ai.py --streams 1 2 3 4 --backend none` |

- `edge3` y `edge4` son **opcionales**: el dashboard consulta `/dev/video*` en cada nodo y los omite si no existe `/dev/video2`, dejando esas celdas como cámaras inactivas en lugar de fallar.

### 6.4 — MODO 3 · Centralizado Tradicional (Benchmark)

- **Etiqueta:** `2 CÁMARAS · VIDEO CRUDO → INFERENCIA HMI`
- **Qué hace:** video crudo desde los nodos; el HMI ejecuta `yolov8n-seg.pt` en su propia CPU para ambos streams. Es la línea base contra la que se compara el Edge Offloading.

| Nodo | Contenedor | Comando |
|---|---|---|
| ADASCore | `adas_raw1` | `python3 main_pub.py --stream-id 1 --camera 0 --no-preview` |
| zorros-desk | `adas_raw2` | `python3 main_pub.py --stream-id 2 --camera 0 --no-preview` |
| HMI | `adas_central_A` | `python3 main_sub_ai.py --streams 1 2 --backend cpu --model models/yolov8n-seg.pt` |

- Con este modo la CPU de la Pi central se dispara: es justamente el comportamiento que el proyecto busca evitar.

### 6.5 — MODO 4 · Detener y Limpiar Clúster (Parada de Emergencia)

- **Etiqueta:** `PARADA DE EMERGENCIA`
- **Qué hace:** `docker rm -f` sobre todos los contenedores `adas_*` en paralelo en ADASCore, zorros-desk y el nodo local (cierra también la ventana X11 del visualizador).

No lanza contenedores nuevos. Es también lo que ejecuta el cierre de la ventana (`WM_DELETE_WINDOW`) antes de salir.

### 6.6 — MODO 5 · Edge Offloading + Video (Silueta en Vivo)

- **Etiqueta:** `● EN VIVO`
- **Qué hace:** igual que el MODO 1, pero **sin** `--no-video`: los edges envían metadatos de segmentación **y** el frame JPEG; el HMI solo compone la silueta roja sobre la imagen real, sin inferir (`--backend none`).

| Nodo | Contenedor | Comando |
|---|---|---|
| ADASCore | `adas_edge1` | `python3 main_pub.py --stream-id 1 --camera 0 --edge-ai --backend hailo --model models/yolov8n_seg.hef --no-preview` |
| zorros-desk | `adas_edge2` | `python3 main_pub.py --stream-id 2 --camera 0 --edge-ai --backend cpu --model models/yolov8n-seg.pt --no-preview` |
| HMI | `adas_central_hybrid` | `python3 main_sub_ai.py --streams 1 2 --backend none` |

- **Diferencia clave:** en el MODO 1 la celda es un HUD (`META`); aquí es video real con la máscara poligonal superpuesta.
- **Coste:** el JPEG viaja comprimido desde el edge; la CPU de la central solo decodifica y dibuja.

### 6.7 — Lectura del visualizador BEV

Cada celda de la cuadrícula 2×2 muestra:

```
CAM 1 FRONTAL [EDGE]            ← stream, orientación y origen de las detecciones
NODO IA 27.4 fps | inf 36.5 ms  ← solo si el nodo Edge publica métricas
FPS 24.1 | LAT 42ms | LOST 0 | JPEG
```

En la franja inferior y el panel derecho:

- **Banner rojo destellante** (`ALERTA CRITICA: PEATON A 1.23m (CAM 1)`) cuando un objeto entra por debajo de **1.50 m**; parpadea a 2 Hz.
- **BEV**: plano cartesiano con el EGO CAR al centro, rejilla de 0.5 m y 80 px/m; cada objeto se ubica según su distancia y el yaw de su cámara (`CAMERA_YAWS_DEG`: frente 0°, atrás 180°, izquierda 90°, derecha −90°).
- **Colores por severidad**: rojo `< 1.50 m`, amarillo `< 3.00 m`, verde `≥ 3.00 m`.
- **Footer**: `IA EDGE: 27.4 | Cams 2/2 | q: salir | d: stats`.
- **Controles**: `q` o `Esc` para salir; `d` imprime estadísticas finales en la terminal del contenedor.

### 6.8 — Exportación de métricas a CSV

En el panel de telemetría, el botón **«Guardar snapshot (.csv)»** vuelca la última muestra de los 3 nodos:

```
marca_tiempo,nodo,contenedor,cpu_pct,ram_usada,ram_pct,modo_activo
2026-11-26T18:42:07-05:00,ADASCore,adas_edge1,38.20,412MiB / 7.86GiB,5.12,MODO 5 · Edge Offloading + Video (Silueta en Vivo)
```

El archivo se llama `adas_metricas_AAAAMMDD_HHMMSS.csv` por defecto y se escribe en un hilo aparte, sin bloquear la GUI.

---

## 7. Solución de problemas (Troubleshooting)

### `Cams 0/4` o celdas `SIN SENAL`

El HMI no está recibiendo ningún topic.

1. **Comprueba el descubrimiento multicast** — debe ser el mismo grupo y puerto en los 3 nodos:
   ```bash
   grep -E "multicast_group|multicast_port|network_enabled" ~/Adas-agosto26/ecal.ini
   ```
2. **Verifica que el tráfico multicast llega al HMI**:
   ```bash
   ip maddr show | grep 239.0.0
   sudo tcpdump -i any -n udp port 14000
   ```
3. **Confirma `--network host`** en el `docker run` del emisor y del receptor. Sin él, el contenedor queda en su propia red y eCAL no se descubre.
4. **Revisa el límite de grupos multicast**:
   ```bash
   sysctl net.ipv4.igmp_max_memberships      # debe ser 128
   ```
5. **Comprueba que el topic coincide**: `webcam_stream_<stream-id>` en emisor y `--streams` en el receptor.
6. **Mismo `ecal.ini` en `/app/ecal.ini` y `/etc/ecal/ecal.ini`** dentro del contenedor; si el montaje falla, eCAL arranca con la configuración por defecto (red desactivada).

### Cámara ocupada o `Device or resource busy`

```bash
fuser -v /dev/video0
sudo fuser -k /dev/video0        # libera el descriptor
```

Después vuelve a lanzar el escenario. Si el problema persiste, desconecta y reconecta la cámara USB y verifica `v4l2-ctl --list-devices`.

### El contenedor muere al instante

El dashboard lo detecta en la verificación y muestra los últimos logs. Manualmente:

```bash
docker ps -a --filter name=adas_
docker logs --tail 50 adas_edge1
```

Causas habituales:

- `--device /dev/video2` inexistente → conectar la cámara o usar un modo que no la requiera.
- Modelo ausente en `models/` → el proceso termina con `FileNotFoundError`.
- `/dev/hailo0` no presente o driver HailoRT no cargado en ADASCore.
- `ADAS_HAILO_SCORES` mal forzado: el HEF puede entregar logits o sigmoides. Déjalo en `auto` o prueba:
  ```bash
  docker run ... -e ADAS_HAILO_SCORES=sigmoid adas:pi5 python3 main_pub.py ...
  ```

### El nodo aparece en rojo en el dashboard

| Chip | Significado |
|---|---|
| `SSH ✗` | No hay conectividad o falta la llave (`ssh-copy-id`). El dashboard usa `BatchMode=yes` y nunca pide contraseña. |
| `hailo0 ✗` | Falta `/dev/hailo0` en ADASCore (driver o HAT). |
| `video0 ✗` | Cámara no detectada en ese nodo. |
| `docker ✗` | Docker no está instalado o el usuario no está en el grupo `docker`. |

Verificación manual:

```bash
ssh -o BatchMode=yes core@192.168.50.61 'ls -l /dev/hailo0 /dev/video0; docker info >/dev/null && echo docker-ok'
```

### Error de protobuf: `duplicate file name imagen.proto`

Ocurre si se importan a la vez `core/imagen_pb2.py` y `protos/imagen_pb2.py`. El proyecto lo resuelve en `interfaces/middleware_factory.py`, que unifica ambos módulos. Si aparece, comprueba que ese factory se importa **antes** que cualquier interfaz y que la imagen se reconstruyó (el Dockerfile regenera el esquema con `protoc`).

### No se abre la ventana del visualizador

```bash
echo $DISPLAY            # debe ser :0
xhost +local:docker
```

Si el contenedor no tiene acceso al servidor X, el dashboard registra el aviso `X11: no se pudo ejecutar xhost`. En una sesión SSH sin `DISPLAY`, expórtalo antes de arrancar el panel.

### CPU de la Raspberry Pi Central al 100 %

Es el comportamiento esperado en **MODO 3** (inferencia local sobre video crudo) y la razón de ser del proyecto. Para operación normal usa **MODO 1** o **MODO 5**, que mantienen la central como mero visualizador (`--backend none`).

### Limpieza manual total

```bash
for host in core@192.168.50.61 zorro@192.168.50.32; do
  ssh "$host" "docker rm -f \$(docker ps -aq --filter name=adas_) 2>/dev/null || true"
done
docker rm -f $(docker ps -aq --filter name=adas_) 2>/dev/null || true
```

---

## Licencia y contexto académico

Proyecto de percepción ADAS distribuida sobre hardware embebido Raspberry Pi. Todo el procesamiento de inferencia ocurre en los nodos Edge; la unidad central solo orquesta, recibe y visualiza.
