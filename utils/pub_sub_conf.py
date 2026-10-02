from dataclasses import dataclass

#=============================================================================
#                            MAIN_PUB CONFIGURATIONS
#=============================================================================

# Monitor Configurations
@dataclass
class MonitorConfig:
    window_size: int = 100                  # Maximum number of measurements to keep
    print_intervals: float = 2.0            # Seconds between impressions

# Initialize Camera Configurations
@dataclass
class CameraConfig:
    index: int = 0                         # Camera index
    width: int = 640                       # Preview window: width
    height: int = 480                      # Preview window: height
    fps: int = 30                          # Frames per second

# Encoding Configurations
@dataclass
class EncodingConfig:
    encoding: str = 'JPEG'                 # Options: 'JPEG', 'LZ4', 'NONE', 'PNG'
    quality: int = 80                      # Quality of frames
    keyframe_interval: int = 30            # Full resolution frame for others' reference (every 30 frames)

# Configuration for stream
@dataclass
class StreamConfig:
    stream_id: int = 1                   # None = auto-detect, 1-4 = manual

# Configuration for detections
@dataclass
class YoloConfig:
    yolo: bool = False                  # Use yolo processing: True or False
    mode: str = 'segmentation'             # 'detection' or 'segmentation'
    conf: float = 0.5                   # YOLO confidence threshold
    interval: int = 2                   # YOLO processing interval (process every N frames)

STREAM_CONFIG = StreamConfig()
MONITOR_CONF = MonitorConfig()
CAMERA_CONFIG = CameraConfig()
ENCODING_CONFIG = EncodingConfig()
YOLO_CONFIG = YoloConfig()

WINDOW_SIZE = MONITOR_CONF.window_size
PRINT_INTERVALS = MONITOR_CONF.print_intervals
CAMERA_INDEX = CAMERA_CONFIG.index
FRAME_WIDTH = CAMERA_CONFIG.width
FRAME_HEIGHT = CAMERA_CONFIG.height
FPS = CAMERA_CONFIG.fps
DEFAULT_STREAM_ID = STREAM_CONFIG.stream_id
DEFAULT_ENCODING = ENCODING_CONFIG.encoding
DEFAULT_QUALITY = ENCODING_CONFIG.quality
KEYFRAME_INTERVAL = ENCODING_CONFIG.keyframe_interval

YOLO_DETECTION = YOLO_CONFIG.yolo
YOLO_MODE = YOLO_CONFIG.mode
YOLO_CONFIDENCE = YOLO_CONFIG.conf
YOLO_PROCESSING_INTERVAL = YOLO_CONFIG.interval


#=============================================================================
#                            MAIN_SUB CONFIGURATIONS
#=============================================================================

@dataclass
class GridConfig:
    num_streams: int = 4                    # Number of streams (cameras)
    grid_cols: int = 2                      # Number of grid columns
    cell_width: int = 640                   # Viewport width (per camera)
    cell_height: int = 480                  # Viewport eight (per camera)
    padding: int = 2                        # Margin between viewport

GRID_CONFIG = GridConfig()

NUM_STREAMS = GRID_CONFIG.num_streams
GRID_COLS = GRID_CONFIG.grid_cols
CELL_WIDTH = GRID_CONFIG.cell_width
CELL_HEIGHT = GRID_CONFIG.cell_height
PADDING = GRID_CONFIG.padding


# =============================================================================
#                              STREAMS CONFIGURATION
# =============================================================================
@dataclass
class StreamsManagerConfig:
    publisher_name: str = "Publisher"           # Publisher or process name
    subscriber_name: str = "Subscriber"         # Subscriber or process name

STREAMMANAGER_CONFIG = StreamsManagerConfig()

PUBLISHER_NAME = STREAMMANAGER_CONFIG.publisher_name
SUBSCRIBER_NAME = STREAMMANAGER_CONFIG.subscriber_name


# =============================================================================
#                              BYD ADAS CONFIGURATION
# =============================================================================
@dataclass
class AdasConfig:
    # --- Detección / IA ---
    enabled: bool = False                   # Activa ADAS en el emisor (--adas)
    model_path: str = "yolov8n.pt"          # Modelo YOLOv8 (se auto-descarga si no existe)
    imgsz: int = 320                        # Tamaño de inferencia (menor = más rápido en CPU)
    conf: float = 0.35                      # Confianza mínima

    # --- Geometría de la cámara ---
    focal_length: float = 700.0             # Focal estimada (calibrada para reference_width_px)
    reference_width_px: int = 640           # Ancho de frame con el que se calibró focal_length
    bumper_offset_m: float = 0.05           # Distancia cámara -> parachoques

    # --- Severidad (metros) ---
    danger_m: float = 1.50                  # < danger_m  -> ROJO / alerta crítica
    warning_m: float = 3.00                 # < warning_m -> AMARILLO ; >= warning_m -> VERDE

    # --- Vigencia y alertas ---
    max_detection_age_s: float = 0.75       # Detecciones más viejas se descartan
    stream_timeout_s: float = 2.0           # Sin frames por este tiempo = cámara inactiva
    alert_blink_hz: float = 2.0             # Destellos por segundo del banner

    # --- Dashboard del receptor AI ---
    dashboard_width: int = 1280
    dashboard_height: int = 720
    left_panel_width: int = 800             # Cuadrícula 2x2 (el resto es el BEV)

    # --- BEV / plano cartesiano ---
    bev_ppm: int = 80                       # Píxeles por metro en el BEV
    bev_cell_m: float = 0.5                 # Lado de cada celda del tablero (metros)
    ego_width_m: float = 0.25               # Ancho del EGO CAR (vehículo a escala)
    ego_length_m: float = 0.45              # Largo del EGO CAR

ADAS_CONFIG = AdasConfig()

ADAS_ENABLED = ADAS_CONFIG.enabled
ADAS_MODEL_PATH = ADAS_CONFIG.model_path
ADAS_IMGSZ = ADAS_CONFIG.imgsz
ADAS_CONFIDENCE = ADAS_CONFIG.conf
FOCAL_LENGTH = ADAS_CONFIG.focal_length
ADAS_REFERENCE_WIDTH_PX = ADAS_CONFIG.reference_width_px
ADAS_BUMPER_OFFSET_M = ADAS_CONFIG.bumper_offset_m
ADAS_DANGER_M = ADAS_CONFIG.danger_m
ADAS_WARNING_M = ADAS_CONFIG.warning_m
ADAS_MAX_DETECTION_AGE_S = ADAS_CONFIG.max_detection_age_s
ADAS_STREAM_TIMEOUT_S = ADAS_CONFIG.stream_timeout_s
ADAS_ALERT_BLINK_HZ = ADAS_CONFIG.alert_blink_hz
ADAS_DASHBOARD_WIDTH = ADAS_CONFIG.dashboard_width
ADAS_DASHBOARD_HEIGHT = ADAS_CONFIG.dashboard_height
ADAS_LEFT_PANEL_WIDTH = ADAS_CONFIG.left_panel_width
ADAS_BEV_PPM = ADAS_CONFIG.bev_ppm
ADAS_BEV_CELL_M = ADAS_CONFIG.bev_cell_m
ADAS_EGO_WIDTH_M = ADAS_CONFIG.ego_width_m
ADAS_EGO_LENGTH_M = ADAS_CONFIG.ego_length_m

# Ancho físico estimado promedio (metros) por clase COCO conocida
CLASS_REAL_WIDTHS = {
    0: 0.45,   # Persona
    2: 1.80,   # Auto
    3: 0.80,   # Moto
    5: 2.50,   # Autobús
    7: 2.50,   # Camión
    15: 0.25,  # Gato
    16: 0.35,  # Perro
    39: 0.07,  # Botella
}

# Nombres en español para las etiquetas (el renderizado quita acentos, ver adas_geometry)
CLASS_NAMES_ES = {
    0: "Peatón",
    2: "Auto",
    3: "Moto",
    5: "Autobús",
    7: "Camión",
    15: "Gato",
    16: "Perro",
    39: "Botella",
}

# Orientación (yaw, grados, antihorario visto desde arriba) de cada stream respecto al EGO CAR.
# 0 = mira al frente, 180 = atrás, 90 = izquierda, -90 = derecha. Ajustar al montaje real.
CAMERA_YAWS_DEG = {1: 0.0, 2: 180.0, 3: 90.0, 4: -90.0}
CAMERA_LABELS = {1: "FRONTAL", 2: "TRASERA", 3: "IZQUIERDA", 4: "DERECHA"}