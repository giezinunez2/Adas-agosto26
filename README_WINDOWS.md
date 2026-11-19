# Soporte Docker en Windows y Arquitectura Multiplataforma (ADASCore)

Este documento detalla la configuración implementada para ejecutar, visualizar y desarrollar el sistema de percepción vehicular **ADASCore** en **Windows (Docker Desktop + WSL2)** sin alterar el flujo nativo de **Linux / Raspberry Pi**.

## 1. Arquitectura Multiplataforma

- **Linux / Raspberry Pi (`docker-compose.yml`):** Utiliza acceso directo a dispositivos V4L2 (`/dev/video0`), servidor gráfico X11 nativo y comunicación de red eCAL para nodos distribuidos.
- **Windows (`docker-compose.windows.yml`):** La captura de cámara se realiza en Windows mediante un servidor HTTP MJPEG (`mjpeg_server.py`) y se consume dentro del contenedor (`adas_dev_pub`) a través de `[http://host.docker.internal:18080/video](http://host.docker.internal:18080/video)`.
- **Comunicación Inter-Contenedor (`SHM`):** Ambos contenedores (`adas_dev_pub` y `adas_dev_dashboard`) comparten el espacio de memoria (`ipc: host`) y ejecutan eCAL en modo local (`network_enabled = false`, `use_shm = 1`), transfiriendo cuadros por memoria compartida.
- **Visualización GUI:** La interfaz PyQt del receptor se renderiza en Windows mediante **VcXsrv / XLaunch** sobre `DISPLAY=127.0.0.1:0.0`.

## 2. Archivos Creados y Modificados

| Archivo | Estado | Descripción |
| :--- | :--- | :--- |
| `Dockerfile` | Nuevo | Imagen de desarrollo (`adas:dev`) con Python, Eclipse eCAL **v5.12.7**, `opencv-python-headless`, `ultralytics` y dependencias Qt/X11. |
| `.dockerignore` | Nuevo | Excluye entornos virtuales locales, caché de Python e historial de Git al construir la imagen. |
| `docker-compose.windows.yml` | Nuevo | Orquesta `adas_dev_dashboard` y `adas_dev_pub` en Windows forzando memoria compartida (`SHM`), espera al endpoint `/health` y recarga con `watchmedo`. |
| `mjpeg_server.py` | Nuevo | Servidor HTTP multihilo en Windows que captura la cámara local con OpenCV y expone `/video` y `/health`. |
| `utils/args_conf.py` | Modificado | Permite que `--camera` acepte URLs (`http://`, `https://`, `rtsp://`) manteniendo la validación de enteros (`--camera 0`) en Linux. |
| `user_interface/pub_visualizer.py` | Modificado | Adapta `CameraManager` para abrir streams HTTP con `cv2.CAP_FFMPEG`, incluye reconexión automática y protege las funciones de ventana en entornos *headless*. |
| `main_pub.py` | Modificado | Activa `--no-preview` automáticamente cuando OpenCV no dispone de backend gráfico. |

## 3. Guía de Ejecución en Windows

1. **Iniciar XLaunch:** Abre **XLaunch** -> *Multiple windows* (`Display number: 0`) -> *Start no client* -> marca **Disable access control** (`-ac`).
2. **Levantar el servidor MJPEG en Windows:**
   ```powershell
   cd E:\Adas-agosto26
   E:\venv_adas\Scripts\python.exe mjpeg_server.py --camera 0 --port 18080
   ```
3. **Levantar los contenedores Docker (en otra terminal):**
   ```powershell
   cd E:\Adas-agosto26
   docker compose -f docker-compose.windows.yml --profile dev up
   ```
