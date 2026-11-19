# Soporte Docker en Windows (ADASCore)

Configuracion para ejecutar ADASCore en Windows (Docker Desktop + WSL2) mediante puente HTTP MJPEG y memoria compartida (SHM) en eCAL.

## Archivos principales
- docker-compose.windows.yml: Orquesta adas_dev_dashboard y adas_dev_pub en modo local (ipc: host, use_shm = 1).
- mjpeg_server.py: Servidor HTTP MJPEG en Windows (http://127.0.0.1:18080/video) para transmitir la camara local al contenedor.
- utils/args_conf.py, user_interface/pub_visualizer.py, main_pub.py: Soporte para URLs en --camera, reconexion automatica y desactivacion de GUI en headless.

## Ejecucion en Windows
1. Abrir XLaunch (Multiple windows, Display 0, Start no client, marcar Disable access control).
2. Levantar servidor MJPEG: E:\venv_adas\Scripts\python.exe mjpeg_server.py --camera 0 --port 18080
3. Levantar contenedores: docker compose -f docker-compose.windows.yml --profile dev up
