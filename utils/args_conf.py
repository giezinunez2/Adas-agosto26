import argparse
from utils import pub_sub_conf, connection_conf
from utils.helper_functions import validate_camera

# -------------------------------------------------------------------------------
# Functions to validate arguments via CLI in addition to the configuration file
# -------------------------------------------------------------------------------
def validate_range(value: str | int, min_val: int, max_val: int, name: str) -> int:
    ivalue = int(value)
    if ivalue < min_val or ivalue > max_val:
        raise argparse.ArgumentTypeError(
            f"{name} {ivalue} not available\n"
            f"Try with: --{name.lower()} between {min_val}-{max_val}"
        )
    return ivalue

def stream_type(value: str | int) -> int:
    return validate_range(value, 1, 4, "Stream-id")

def quality_type(value: str | int) -> int:
    return validate_range(value, 1, 100, "Quality")

def width_type(value: str | int) -> int:
    return validate_range(value, 100, 1000, "Width")

def height_type(value: str | int) -> int:
    return validate_range(value, 100, 1000, "Height")

def fps_type(value: str | int) -> int:
    return validate_range(value, 1, 50, "Fps")

def camera_type(value: str) -> int:
    try:
        ivalue = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"Camera '{value}' is not a numeric index\n"
            f"Use indices:  --camera 0, --camera 1 or --camera 2 "
            f"(POSIX paths like /dev/video0 do not exist on Windows)"
        ) from None
    if ivalue < 0:
        raise argparse.ArgumentTypeError(f"Camera index cannot be negative: {ivalue}")
    if not validate_camera(ivalue):
        raise argparse.ArgumentTypeError(f"Camera {ivalue} not available\n"
                                         f"Try with:  --camera 0, --camera 1 or --camera 2")
    return ivalue

# -------------------------------------------------------------------------------
#       Function to pass arguments via command line
# -------------------------------------------------------------------------------
"""Parse the command-line arguments used to configure the video publisher"""
def parse_arguments():
    parser = argparse.ArgumentParser(description='Video publisher for multi-camera grid')

    # Validate configuration file parameters (pub_sub_conf)
    try:
        stream = stream_type(pub_sub_conf.DEFAULT_STREAM_ID)
    except argparse.ArgumentTypeError as e:
        raise ValueError(f"Invalid default stream-id in config file: {e}")

    try:
        quality = quality_type(pub_sub_conf.DEFAULT_QUALITY)
    except argparse.ArgumentTypeError as e:
        raise ValueError(f"Invalid quality in config file: {e}")

    try:
        width = width_type(pub_sub_conf.FRAME_WIDTH)
    except argparse.ArgumentTypeError as e:
        raise ValueError(f"Invalid width size in config file: {e}")

    try:
        height = height_type(pub_sub_conf.FRAME_HEIGHT)
    except argparse.ArgumentTypeError as e:
        raise ValueError(f"Invalid height size in config file: {e}")

    try:
        fps = fps_type(pub_sub_conf.FPS)
    except argparse.ArgumentTypeError as e:
        raise ValueError(f"Invalid fps in config file: {e}")


    # Pass arguments by default, already validated
    parser.add_argument('--stream-id', type=stream_type, default=stream,
                            help=f'Stream ID (1-4). If not specified, uses config value ({stream}) or auto-detected')
    parser.add_argument('--encoding', '-e', type=str, default=pub_sub_conf.DEFAULT_ENCODING,
                        choices=['JPEG', 'LZ4', 'NONE', 'PNG'],
                        help='Encoding type (JPEG, LZ4, NONE, PNG)')
    parser.add_argument('--quality', '-q', type=quality_type, default=quality,
                        help='Compression quality (1-100) for JPEG')
    parser.add_argument('--camera', '-c', type=camera_type, default=pub_sub_conf.CAMERA_INDEX,
                        help='Camera index')
    parser.add_argument('--width', type=int, default=width,
                        help='Frame width')
    parser.add_argument('--height', type=int, default=height,
                        help='Frame height')
    parser.add_argument('--fps', type=int, default=fps,
                        help='Frames per second')
    parser.add_argument('--no-preview', action='store_true',
                        help='Hide local preview for better performance')
    parser.add_argument('--preview-size', type=str, default='640x480',
                        help='Preview window size (format: WIDTHxHEIGHT)')
    parser.add_argument('--keyframe-interval', type=int, default=pub_sub_conf.KEYFRAME_INTERVAL,
                        help='Keyframe interval (every 30 frames)')
    parser.add_argument('--topic-prefix', type=str, default=connection_conf.TOPIC_PREFIX,
                        help='Topic name prefix for eCAL publishing')
    parser.add_argument('--enable-yolo', type=bool, default=pub_sub_conf.YOLO_DETECTION,
                        help='Enable YOLO person detection (True/False)')
    parser.add_argument('--yolo-mode', type=str, default=pub_sub_conf.YOLO_MODE,
                        choices=['detection', 'segmentation'],
                        help='YOLO mode: detection or segmentation')
    parser.add_argument('--yolo-conf', type=float, default=pub_sub_conf.YOLO_CONFIDENCE,
                        help='YOLO confidence threshold (0.0 to 1.0)')
    parser.add_argument('--yolo-interval', type=int, default=pub_sub_conf.YOLO_PROCESSING_INTERVAL,
                        help='YOLO processing interval (process every N frames)')

    # --- BYD ADAS (preview local con cuboides 3D y distancias) ---
    parser.add_argument('--adas', action=argparse.BooleanOptionalAction,
                        default=pub_sub_conf.ADAS_ENABLED,
                        help='Enable BYD ADAS local preview (async YOLOv8 + 3D cuboids). Use --no-adas to disable')
    parser.add_argument('--adas-conf', type=float, default=pub_sub_conf.ADAS_CONFIDENCE,
                        help='ADAS detection confidence threshold (0.0 to 1.0)')
    parser.add_argument('--adas-imgsz', type=int, default=pub_sub_conf.ADAS_IMGSZ,
                        help='ADAS YOLO inference size (e.g. 320, 416, 640). El backend hailo usa 640 fijo')

    # --- Edge AI offloading (nodo periférico con AI HAT / Hailo-8L) ---
    parser.add_argument('--edge-ai', action='store_true',
                        help='Infiere en ESTE nodo (yolov8n-seg) y publica las detecciones '
                             '(caja + polígono + distancias) dentro del Protobuf')
    parser.add_argument('--no-video', action='store_true',
                        help='Con --edge-ai: no codifica ni envía video; publica solo metadatos '
                             '(is_metadata_only=True, frame_data vacío)')
    parser.add_argument('--backend', type=str, choices=['hailo', 'cpu'], default=None,
                        help='Backend de inferencia: hailo (/dev/hailo0, HEF) o cpu (ultralytics). '
                             'Por defecto: hailo con --edge-ai, cpu con --adas')
    parser.add_argument('--model', type=str, default=None,
                        help='Ruta del modelo: .hef para hailo (def. models/yolov8n_seg.hef) '
                             'o .pt para cpu (def. yolov8n-seg.pt)')

    args = parser.parse_args()
    if args.no_video and not args.edge_ai:
        parser.error("--no-video requiere --edge-ai (sin IA en el nodo no habría nada que publicar)")
    return args
