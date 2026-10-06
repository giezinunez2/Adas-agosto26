# ADASCore - Multi-Camera Ingestion & Processing System

Sistema de ingesta de video multi-cámara en tiempo real, procesamiento de inferencia ADAS con YOLO y transporte de mensajes de baja latencia mediante eCAL y Protobuf.

---

## 🚀 Mejoras y Refactorización del Código

### 1. Estabilización de la Interfaz eCAL (`interfaces/ecal_interface.py`)
- **Corrección de Sintaxis:** Refactorización de la clase `EcalPublisher` corrigiendo sangrías y errores de indentación en el método `send()`.
- **Serialización Protobuf:** Implementación robusta del método `msg.SerializeToString()` para asegurar la conversión correcta de estructuras de datos a bytes.
- **Métricas de Diagnóstico:** Adición de telemetría en tiempo real (`len(serialized_data)`, `sent_count`, `total_bytes`) para monitoreo del tráfico en la red eCAL.
- **Manejo de Contexto:** Corrección de métodos especiales (`__enter__`) para garantizar la gestión y cierre seguro de conexiones del middleware.

### 2. Optimización Multi-Cámara y Bus USB (`main_pub.py`)
- **Solución a Errores V4L2 / `EBUSY`:** Configuración de captura forzada en compresión **MJPEG** a **640x480 @ 15 FPS**, resolviendo el bloqueo por saturación de ancho de banda del bus USB al conectar 4 cámaras simultáneamente (Logitech C920 / HP TrueVision).
- **Control por CLI:** Implementación de argumentos dinámicos para definir dispositivos (`/dev/video0,2,4,6`), formatos de codificación y parámetros de confianza para YOLO (`--adas-conf`, `--adas-imgsz`).

### 3. Modularización de la Arquitectura
El sistema se dividió en módulos independientes y desacoplados:
- `interfaces/`: Capa de abstracción para transportes de comunicación (`ecal_interface.py`, `rabbitmq_interface.py`, `middleware_factory.py`).
- `data_stream/`: Administradores dedicados para la lógica de publicación y suscripción (`pubs_stream_manager.py`, `subs_stream_manager.py`, `ai_subs_stream_manager.py`).
- `img_processing/`: Algoritmos de inferencia YOLO, procesamiento geométrico ADAS y serialización de video (`yolo_processor.py`, `adas_detector.py`, `video_serializer.py`).
- `user_interface/` & `utils/`: Renderizado visual en tiempo real (Bird's Eye View / BEV, overlays) y manejo de configuraciones.

### 4. Optimización de Control de Versiones (Git)
- Inclusión de un `.gitignore` estricto para ignorar el entorno virtual (`venv/`), temporales de Python (`__pycache__/`) y pesos de modelos (`*.pt`, `*.onnx`), reduciendo el peso del repositorio de 2.9 GB a menos de 20 MB.

---
## 🚀 Modo de Uso / Ejecución

Instrucciones para iniciar los nodos emisores (*publishers*) y receptores (*subscribers*) del sistema.

### 1. Nodos Emisores (Publishers)

Ejecuta cada flujo de cámara en una terminal independiente especificando el ID de la transmisión y el índice del dispositivo de video:

```bash
# Emisor 1: Cámara frontal (Terminal 1)
python main_pub.py --stream-id 1 --camera 0 --adas

# Emisor 2: Cámara trasera (Terminal 2)
python main_pub.py --stream-id 2 --camera 2 --adas

# Cámaras 3 y 4 (Terminales 3 y 4)
python main_pub.py --stream-id 3 --camera 3 --adas
python main_pub.py --stream-id 4 --camera 4 --adas

## 📂 Estructura del Proyecto

```text
ADASCore/
├── core/                   # Archivos Protobuf compilados (*_pb2.py)
├── data_stream/            # Gestores de flujo de publicación y suscripción
├── img_processing/         # Detección YOLO, geometría y serialización
├── interfaces/             # Adaptadores de comunicación (eCAL / RabbitMQ)
├── user_interface/         # Dashboard visual, Overlays y vista BEV
├── utils/                  # Parámetros y parseo de argumentos
├── main_pub.py             # Script principal de captura e ingesta multi-cámara
├── main_sub.py             # Suscriptor base
└── main_sub_ai.py          # Dashboard de visualización con inferencia IA
