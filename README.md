Markdown# 🚘 ADASCore - Multi-Camera Ingestion & Processing System

Sistema de ingesta de video multi-cámara en tiempo real, procesamiento de inferencia ADAS con YOLO y transporte de mensajes de baja latencia mediante **eCAL** y **Protobuf**.

---

## ⚡ Comandos Rápidos de Ejecución

Bloque de comandos rápidos listo para copiar y ejecutar en las terminales correspondientes:

```bash
# Emisor 1: cámara frontal (terminal 1)
python main_pub.py --stream-id 1 --camera 0 --adas

# Emisor 2: cámara trasera (terminal 2)
python main_pub.py --stream-id 2 --camera 2 --adas

# Cámaras 3 y 4: igual con --stream-id 3 / 4.
# Si el nodo solo debe transmitir, usa --no-preview (desactiva la IA local).

# Receptor AI (terminal aparte)
python main_sub_ai.py --streams 1 2 3 4

# Receptor ligero sin IA (sin cambios)
python main_sub.py
📋 Tabla de ContenidosComandos RápidosCaracterísticas PrincipalesEstructura del ProyectoMejoras y RefactorizaciónRequisitos Previos e InstalaciónGuía de Ejecución DetalladaParámetros de Línea de Comandos (CLI)Licencia✨ Características PrincipalesTransporte de Baja Latencia: Integración con middleware eCAL y serialización con Protobuf para minimización de overhead en red.Gestión Multi-Cámara Simultánea: Control del bus USB mediante compresión MJPEG para soportar hasta 4 cámaras en paralelo sin saturación de ancho de banda (EBUSY).Inferencia ADAS Centralizada/Local: Integración con YOLO para detección de objetos, cálculo geométrico y renderizado de vista cenital (Bird's Eye View / BEV).Arquitectura Desacoplada: Separación clara entre capas de ingesta, transporte, procesamiento de imagen y visualización.Modo Headless / Bajo Consumo: Soporte para ejecución en dispositivos embebidos sin interfaz gráfica local (--no-preview).📂 Estructura del ProyectoPlaintextADASCore/
├── core/                # Archivos Protobuf compilados (*_pb2.py)
├── data_stream/         # Gestores de flujo de publicación y suscripción
│   ├── pubs_stream_manager.py
│   ├── subs_stream_manager.py
│   └── ai_subs_stream_manager.py
├── img_processing/      # Detección YOLO, geometría ADAS y serialización
│   ├── yolo_processor.py
│   ├── adas_detector.py
│   └── video_serializer.py
├── interfaces/          # Adaptadores de comunicación (eCAL / RabbitMQ)
│   ├── ecal_interface.py
│   ├── rabbitmq_interface.py
│   └── middleware_factory.py
├── user_interface/      # Dashboard visual, Overlays y vista BEV
├── utils/               # Parámetros globales y parseo de argumentos
├── main_pub.py          # Script principal de captura e ingesta multi-cámara
├── main_sub.py          # Suscriptor ligero (sin IA)
└── main_sub_ai.py       # Dashboard de visualización con inferencia IA
🛠️ Mejoras y Refactorización1. Estabilización de la Interfaz eCAL (interfaces/ecal_interface.py)Corrección de Sintaxis: Refactorización de la clase EcalPublisher corrigiendo indentaciones y bloques en el método send().Serialización Protobuf: Implementación del método msg.SerializeToString() para asegurar la conversión correcta de estructuras de datos a bytes.Métricas de Diagnóstico: Adición de telemetría en tiempo real (len(serialized_data), sent_count, total_bytes) para monitoreo del tráfico en la red.Manejo de Contexto: Soporte para métodos especiales (__enter__ / __exit__) garantizando el cierre seguro de conexiones.2. Optimización Multi-Cámara y Bus USB (main_pub.py)Solución a Errores V4L2 / EBUSY: Captura force a compresión MJPEG a 640x480 @ 15 FPS, resolviendo el bloqueo por saturación de ancho de banda del bus USB al conectar hasta 4 cámaras en paralelo (Logitech C920 / HP TrueVision).Control por CLI: Implementación de argumentos dinámicos para definir dispositivos (/dev/video0,2,4,6), formatos de codificación y parámetros para YOLO.3. Modularización de la ArquitecturaEl sistema se dividió en módulos independientes y desacoplados:interfaces/: Capa de abstracción para transportes de comunicación.data_stream/: Administradores dedicados para la lógica Pub/Sub.img_processing/: Algoritmos de inferencia y procesamiento geométrico.user_interface/ & utils/: Renderizado visual en tiempo real y configuraciones.4. Optimización de Control de Versiones (Git).gitignore estricto para ignorar entornos virtuales (venv/), temporales de Python (__pycache__/) y pesos de modelos (*.pt, *.onnx), reduciendo el peso del repositorio de 2.9 GB a menos de 20 MB.📦 Requisitos Previos e InstalaciónRequisitosLinux (Ubuntu 20.04/22.04 recomendado) / Windows 10+Python 3.8+eCAL Middleware instalado en el sistema (sudo add-apt-repository ppa:ecal/ecal en Ubuntu)Pasos de InstalaciónClonar el repositorio:Bashgit clone [https://github.com/tu-usuario/ADASCore.git](https://github.com/tu-usuario/ADASCore.git)
cd ADASCore
Crear y activar entorno virtual:Bashpython3 -m venv venv
source venv/bin/activate
Instalar dependencias:Bashpip install --upgrade pip
pip install -r requirements.txt
🚀 Guía de Ejecución Detallada1. Nodos Emisores (Publishers)Ejecuta cada flujo de cámara en una terminal independiente especificando el ID de la transmisión y el índice/dispositivo de la cámara:Bash# Emisor 1: Cámara frontal (Terminal 1)
python main_pub.py --stream-id 1 --camera 0 --adas

# Emisor 2: Cámara trasera (Terminal 2)
python main_pub.py --stream-id 2 --camera 2 --adas

# Emisor 3: Cámara lateral izquierda (Terminal 3)
python main_pub.py --stream-id 3 --camera 4 --adas

# Emisor 4: Cámara lateral derecha (Terminal 4)
python main_pub.py --stream-id 4 --camera 6 --adas
💡 Optimización para nodos emisores: Si el nodo solo debe transmitir sin renderizar interfaz gráfica local, añade la bandera --no-preview para desactivar el GUI y liberar procesamiento:Bashpython main_pub.py --stream-id 1 --camera 0 --adas --no-preview
2. Nodos Receptores (Subscribers)Inicia el receptor correspondiente desde una terminal dedicada según el tipo de procesamiento requerido:Receptor Centralizado con Inferencia IA:Procesa, analiza y visualiza de manera centralizada múltiples transmisiones simultáneas.Bashpython main_sub_ai.py --streams 1 2 3 4
Receptor Ligero (Sin IA):Recibe y visualiza los flujos de video directos sin carga computacional de IA.Bashpython main_sub.py
⚙️ Parámetros de Línea de Comandos (CLI)Opciones de main_pub.pyArgumentoTipoDescripciónDefault--stream-idintIdentificador único de la transmisión (ej. 1, 2).1--camerastr/intÍndice de dispositivo (0, 2) o ruta /dev/videoX.0--adasflagActiva el pipeline de procesamiento geométrico y asistencia ADAS.False--adas-conffloatUmbral de confianza mínimo para las detecciones YOLO.0.25--adas-imgszintTamaño de la imagen de entrada para el modelo YOLO.640--no-previewflagDesactiva la vista previa e interfaz gráfica local en el emisor.False
