"""
Geometría ADAS: estimación de distancia, severidad por color y mapeo cartesiano.

Sistema de coordenadas del EGO CAR (vista desde arriba):
    X -> derecha del vehículo
    Y -> frente del vehículo
"""

import math
import unicodedata
from dataclasses import dataclass
from typing import Optional, Tuple

from utils import pub_sub_conf as cfg

# Colores BGR por severidad
COLOR_DANGER = (0, 0, 255)      # Rojo     (< danger_m)
COLOR_WARNING = (0, 255, 255)   # Amarillo (danger_m - warning_m)
COLOR_SAFE = (0, 255, 0)        # Verde    (> warning_m)

SEVERITY_DANGER = "danger"
SEVERITY_WARNING = "warning"
SEVERITY_SAFE = "safe"


@dataclass(frozen=True)
class Detection:
    """Objeto detectado con su distancia estimada."""
    x1: int
    y1: int
    x2: int
    y2: int
    cls_id: int
    label: str            # Nombre en español, sin acentos (OpenCV no los dibuja)
    conf: float
    z_m: float            # Distancia sobre el eje óptico (sin offset de parachoques)
    lateral_m: float      # Desplazamiento lateral respecto al eje óptico (+ = derecha de la imagen)
    dist_m: float         # Distancia total al parachoques (z_m + offset)
    real_width_m: float   # Ancho físico asumido para la clase


def strip_accents(text: str) -> str:
    """Quita acentos: cv2.putText (Hershey) no puede dibujar 'ó', 'ñ', etc."""
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(c for c in normalized if not unicodedata.combining(c))


def scaled_focal_length(frame_width: int) -> float:
    """Escala la focal calibrada (a reference_width_px) al ancho real del frame."""
    return cfg.FOCAL_LENGTH * (frame_width / float(cfg.ADAS_REFERENCE_WIDTH_PX))


def build_detection(x1: int, y1: int, x2: int, y2: int, cls_id: int, conf: float,
                    coco_name: str, frame_width: int) -> Optional[Detection]:
    """
    Convierte una caja de YOLO en Detection con distancia en metros.

        Z = (ancho_real * FOCAL_LENGTH) / ancho_en_pixeles
        lateral = (centro_x - W/2) * Z / FOCAL_LENGTH

    Retorna None si la clase no está en CLASS_REAL_WIDTHS o la caja es inválida.
    """
    real_w = cfg.CLASS_REAL_WIDTHS.get(cls_id)
    width_px = x2 - x1
    if real_w is None or width_px <= 0:
        return None

    focal = scaled_focal_length(frame_width)
    z_m = max((real_w * focal) / width_px, 0.05)
    center_x = (x1 + x2) / 2.0
    lateral_m = ((center_x - frame_width / 2.0) * z_m) / focal
    label = strip_accents(cfg.CLASS_NAMES_ES.get(cls_id, coco_name))

    return Detection(
        x1=x1, y1=y1, x2=x2, y2=y2,
        cls_id=cls_id, label=label, conf=conf,
        z_m=z_m, lateral_m=lateral_m,
        dist_m=z_m + cfg.ADAS_BUMPER_OFFSET_M,
        real_width_m=real_w,
    )


def severity_level(dist_m: float) -> str:
    if dist_m < cfg.ADAS_DANGER_M:
        return SEVERITY_DANGER
    if dist_m < cfg.ADAS_WARNING_M:
        return SEVERITY_WARNING
    return SEVERITY_SAFE


def severity_color(dist_m: float) -> Tuple[int, int, int]:
    level = severity_level(dist_m)
    if level == SEVERITY_DANGER:
        return COLOR_DANGER
    if level == SEVERITY_WARNING:
        return COLOR_WARNING
    return COLOR_SAFE


def format_distance(dist_m: float) -> str:
    """Formato de overlay: '+1.20m'."""
    return f"+{dist_m:.2f}m"


def camera_to_ego(lateral_m: float, forward_m: float, yaw_deg: float) -> Tuple[float, float]:
    """
    Rota un punto del marco de la cámara (lateral, adelante) al marco del EGO CAR (X, Y).

    yaw_deg: orientación de la cámara respecto al frente del vehículo
             (0 = frente, 180 = atrás, 90 = izquierda, -90 = derecha).
    """
    theta = math.radians(yaw_deg)
    ego_x = -forward_m * math.sin(theta) + lateral_m * math.cos(theta)
    ego_y = forward_m * math.cos(theta) + lateral_m * math.sin(theta)
    return ego_x, ego_y


def ego_position(det: Detection, stream_id: int) -> Tuple[float, float]:
    """Posición cartesiana (X, Y) en metros de una detección, según la cámara que la vio."""
    yaw = cfg.CAMERA_YAWS_DEG.get(stream_id, 0.0)
    return camera_to_ego(det.lateral_m, det.dist_m, yaw)