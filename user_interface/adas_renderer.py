"""
Renderizado ADAS: máscaras de segmentación poligonales, cuboides 3D, overlay por cámara,
canvas HUD para nodos Edge que solo envían metadatos, grilla métrica tipo ajedrez (BEV),
helpers de celdas y banner de alerta crítica.
"""

import math
import time
from typing import List, Optional, Sequence, Tuple

import cv2
import numpy as np

from img_processing.adas_geometry import (Detection, format_distance, scaled_focal_length,
                                          severity_color)
from utils import pub_sub_conf as cfg


# ----------------------------------------------------------------------------
# Primitivas de dibujo
# ----------------------------------------------------------------------------
def draw_3d_cuboid(img, x1, y1, x2, y2, color=(0, 255, 0), scale=0.20, label=""):
    """Dibuja un cuboide pseudo-3D (cara frontal + cara trasera desplazada) con etiqueta."""
    w, h = x2 - x1, y2 - y1
    dx, dy = int(w * scale), int(h * scale)

    f_tl, f_tr = (x1, y1), (x2, y1)
    f_bl, f_br = (x1, y2), (x2, y2)

    b_tl, b_tr = (x1 + dx, y1 - dy), (x2 - dx, y1 - dy)
    b_bl, b_br = (x1 + dx, y2 - dy), (x2 - dx, y2 - dy)

    dark_color = (int(color[0] * 0.5), int(color[1] * 0.5), int(color[2] * 0.5))
    cv2.rectangle(img, b_tl, b_br, dark_color, 1)

    for front_pt, back_pt in zip([f_tl, f_tr, f_bl, f_br], [b_tl, b_tr, b_bl, b_br]):
        cv2.line(img, front_pt, back_pt, color, 1, cv2.LINE_AA)

    cv2.rectangle(img, f_tl, f_br, color, 2, cv2.LINE_AA)

    if label:
        text_size, _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)
        text_w, text_h = text_size
        top_y = max(y1 - dy - 5, 15)
        cv2.rectangle(img, (x1, top_y - text_h - 4), (x1 + text_w + 6, top_y + 2), (0, 0, 0), -1)
        cv2.putText(img, label, (x1 + 3, top_y - 2), cv2.FONT_HERSHEY_SIMPLEX, 0.45,
                    (255, 255, 255), 1, cv2.LINE_AA)


def draw_segmentation_masks(frame: np.ndarray, detections: Sequence[Detection],
                            alpha: float = 0.40, outline: int = 2) -> np.ndarray:
    """
    Máscaras poligonales semitransparentes coloreadas por severidad (+ contorno).
    Se rellenan todos los polígonos sobre una sola copia y se mezcla una única vez.
    Modifica `frame` in situ. Las detecciones sin polígono se ignoran.
    """
    polys = [(det, np.asarray(det.polygon, dtype=np.int32).reshape(-1, 1, 2))
             for det in detections if det.polygon and len(det.polygon) >= 3]
    if not polys:
        return frame

    overlay = frame.copy()
    for det, pts in polys:
        cv2.fillPoly(overlay, [pts], severity_color(det.dist_m))
    cv2.addWeighted(overlay, alpha, frame, 1.0 - alpha, 0, dst=frame)
    for det, pts in polys:
        cv2.polylines(frame, [pts], True, severity_color(det.dist_m), outline, cv2.LINE_AA)
    return frame


def draw_detection_overlay(frame: np.ndarray, detections: Sequence[Detection],
                           ai_fps: Optional[float] = None) -> np.ndarray:
    """Máscaras de segmentación + cuboides coloreados por severidad con 'Clase: +x.xxm'. Modifica `frame` in situ."""
    draw_segmentation_masks(frame, detections)

    for det in detections:
        color = severity_color(det.dist_m)
        label = f"{det.label}: {format_distance(det.dist_m)}"
        draw_3d_cuboid(frame, det.x1, det.y1, det.x2, det.y2, color=color, label=label)

    if ai_fps is not None:
        cv2.putText(frame, f"ADAS IA: {ai_fps:.1f} fps", (10, frame.shape[0] - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1, cv2.LINE_AA)
    return frame


def make_edge_telemetry_canvas(src_w: int, src_h: int, detections: Sequence[Detection],
                               ai_fps: Optional[float] = None,
                               inference_ms: Optional[float] = None) -> np.ndarray:
    """
    Canvas HUD calibrado para un nodo Edge que envía SOLO metadatos (sin video crudo).

    Tiene el tamaño del frame original de la cámara (src_w x src_h) para que los polígonos y
    cajas recibidos se dibujen en sus coordenadas reales. Incluye:
      - rejilla del plano imagen cada src_w/8 px y cruz del eje óptico,
      - regla angular calibrada con la focal de la cámara (x = cx + f * tan(theta)),
      - máscaras poligonales, cuboides 3D y distancias (mismo estilo que con video).
    El llamador lo ajusta a la celda con fit_to_cell().
    """
    src_w, src_h = max(64, int(src_w)), max(64, int(src_h))
    canvas = np.empty((src_h, src_w, 3), dtype=np.uint8)
    canvas[:] = (24, 28, 38)

    step = max(20, src_w // 8)
    for x in range(0, src_w, step):
        cv2.line(canvas, (x, 0), (x, src_h), (40, 46, 60), 1)
    for y in range(0, src_h, step):
        cv2.line(canvas, (0, y), (src_w, y), (40, 46, 60), 1)

    cx, cy = src_w // 2, src_h // 2
    cv2.line(canvas, (cx, 0), (cx, src_h), (0, 140, 255), 1, cv2.LINE_AA)
    cv2.line(canvas, (0, cy), (src_w, cy), (0, 140, 255), 1, cv2.LINE_AA)

    focal = scaled_focal_length(src_w)
    hfov = 2.0 * math.degrees(math.atan(src_w / (2.0 * focal)))
    for deg in range(-80, 81, 10):
        x = int(round(cx + focal * math.tan(math.radians(deg))))
        if 0 <= x < src_w:
            cv2.line(canvas, (x, src_h - 14), (x, src_h), (170, 170, 170), 1)
            cv2.putText(canvas, f"{deg:+d}", (x - 12, src_h - 18), cv2.FONT_HERSHEY_SIMPLEX,
                        0.35, (170, 170, 170), 1, cv2.LINE_AA)

    draw_detection_overlay(canvas, detections)

    cv2.putText(canvas, f"EDGE AI - SOLO METADATOS | {src_w}x{src_h} | HFOV {hfov:.0f} deg",
                (8, src_h - 36), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 255), 1, cv2.LINE_AA)
    parts = []
    if ai_fps is not None:
        parts.append(f"IA {ai_fps:.1f} fps")
    if inference_ms is not None:
        parts.append(f"inf {inference_ms:.1f} ms")
    if parts:
        cv2.putText(canvas, " | ".join(parts), (8, 18), cv2.FONT_HERSHEY_SIMPLEX,
                    0.5, (0, 255, 0), 1, cv2.LINE_AA)
    return canvas


def fit_to_cell(frame: np.ndarray, cell_w: int, cell_h: int) -> np.ndarray:
    """Escala el frame manteniendo la relación de aspecto (letterbox negro)."""
    h, w = frame.shape[:2]
    scale = min(cell_w / float(w), cell_h / float(h))
    new_w, new_h = max(1, int(w * scale)), max(1, int(h * scale))
    resized = cv2.resize(frame, (new_w, new_h))
    canvas = np.zeros((cell_h, cell_w, 3), dtype=np.uint8)
    x0, y0 = (cell_w - new_w) // 2, (cell_h - new_h) // 2
    canvas[y0:y0 + new_h, x0:x0 + new_w] = resized
    return canvas


def draw_alert_banner(canvas: np.ndarray, text: str, height: int = 48,
                      blink_hz: float = cfg.ADAS_ALERT_BLINK_HZ) -> np.ndarray:
    """Banner rojo destellante en el borde superior."""
    width = canvas.shape[1]
    on = int(time.time() * blink_hz * 2) % 2 == 0
    bg = (0, 0, 200) if on else (0, 0, 90)
    cv2.rectangle(canvas, (0, 0), (width, height), bg, -1)

    font, scale, thick = cv2.FONT_HERSHEY_SIMPLEX, 0.8, 2
    (tw, th), _ = cv2.getTextSize(text, font, scale, thick)
    cv2.putText(canvas, text, ((width - tw) // 2, (height + th) // 2),
                font, scale, (255, 255, 255), thick, cv2.LINE_AA)
    return canvas


# ----------------------------------------------------------------------------
# Plano cartesiano / BEV
# ----------------------------------------------------------------------------
def draw_metric_grid(panel: np.ndarray, cx: int, cy: int, ppm: int, cell_m: float):
    """
    Dibuja la cuadrícula métrica tipo tablero de ajedrez centrada en (cx, cy) = EGO CAR.
    Cada celda mide cell_m metros; líneas más claras cada metro; ejes naranja;
    anillos de severidad en danger_m y warning_m.
    """
    h, w = panel.shape[:2]
    cell_px = max(4, int(round(ppm * cell_m)))
    color_a, color_b = (32, 32, 32), (52, 52, 52)

    col_min, col_max = -(cx // cell_px) - 1, (w - cx) // cell_px + 1
    row_min, row_max = -(cy // cell_px) - 1, (h - cy) // cell_px + 1
    for idx_row in range(row_min, row_max + 1):
        for idx_col in range(col_min, col_max + 1):
            x1, y1 = cx + idx_col * cell_px, cy + idx_row * cell_px
            color = color_a if (idx_col + idx_row) % 2 == 0 else color_b
            cv2.rectangle(panel, (x1, y1), (x1 + cell_px - 1, y1 + cell_px - 1), color, -1)

    # Líneas cada metro
    k_x_max, k_y_max = w // ppm + 1, h // ppm + 1
    for k in range(-k_x_max, k_x_max + 1):
        x = cx + k * ppm
        if 0 <= x < w:
            cv2.line(panel, (x, 0), (x, h), (70, 70, 70), 1)
    for k in range(-k_y_max, k_y_max + 1):
        y = cy + k * ppm
        if 0 <= y < h:
            cv2.line(panel, (0, y), (w, y), (70, 70, 70), 1)

    # Anillos de severidad
    cv2.circle(panel, (cx, cy), int(cfg.ADAS_DANGER_M * ppm), (0, 0, 160), 1, cv2.LINE_AA)
    cv2.circle(panel, (cx, cy), int(cfg.ADAS_WARNING_M * ppm), (0, 160, 160), 1, cv2.LINE_AA)

    # Ejes principales
    cv2.line(panel, (cx, 0), (cx, h), (0, 140, 255), 1, cv2.LINE_AA)
    cv2.line(panel, (0, cy), (w, cy), (0, 140, 255), 1, cv2.LINE_AA)

    # Etiquetas métricas
    for k in range(-k_y_max, k_y_max + 1):
        y = cy - k * ppm
        if k != 0 and 12 < y < h - 4:
            cv2.putText(panel, f"{k:+d}m", (cx + 6, y - 4), cv2.FONT_HERSHEY_SIMPLEX,
                        0.35, (170, 170, 170), 1, cv2.LINE_AA)
    for k in range(-k_x_max, k_x_max + 1):
        x = cx + k * ppm
        if k != 0 and 4 < x < w - 24:
            cv2.putText(panel, f"{k:+d}m", (x + 3, cy + 14), cv2.FONT_HERSHEY_SIMPLEX,
                        0.35, (170, 170, 170), 1, cv2.LINE_AA)


class BEVRenderer:
    """Vista de pájaro (plano cartesiano) con EGO CAR al centro. El fondo se renderiza una sola vez."""

    def __init__(self, width: int, height: int,
                 ppm: int = cfg.ADAS_BEV_PPM,
                 cell_m: float = cfg.ADAS_BEV_CELL_M,
                 ego_width_m: float = cfg.ADAS_EGO_WIDTH_M,
                 ego_length_m: float = cfg.ADAS_EGO_LENGTH_M):
        self.width, self.height = width, height
        self.cx, self.cy = width // 2, height // 2
        self.ppm = ppm
        self.cell_m = cell_m
        self.ego_w_px = max(12, int(ego_width_m * ppm))
        self.ego_l_px = max(20, int(ego_length_m * ppm))
        self._background = self._build_background()

    def _build_background(self) -> np.ndarray:
        panel = np.zeros((self.height, self.width, 3), dtype=np.uint8)
        draw_metric_grid(panel, self.cx, self.cy, self.ppm, self.cell_m)
        cv2.putText(panel, "BEV - PLANO CARTESIANO (X,Y)", (10, 22),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1, cv2.LINE_AA)
        cv2.putText(panel, f"1 celda = {self.cell_m:g} m", (10, self.height - 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (170, 170, 170), 1, cv2.LINE_AA)
        return panel

    def render(self, objects: Sequence[Tuple[Detection, float, float]]) -> np.ndarray:
        """
        objects: lista de (Detection, ego_x_m, ego_y_m).
        Los objetos fuera de rango se fijan al borde con una marca '>>'.
        """
        panel = self._background.copy()

        # EGO CAR
        draw_3d_cuboid(panel,
                       self.cx - self.ego_w_px // 2, self.cy - self.ego_l_px // 2,
                       self.cx + self.ego_w_px // 2, self.cy + self.ego_l_px // 2,
                       color=(255, 140, 0), scale=0.3, label="EGO CAR")

        margin = 26
        for det, ex, ey in objects:
            px = int(self.cx + ex * self.ppm)
            py = int(self.cy - ey * self.ppm)
            out_of_range = not (margin <= px <= self.width - margin and margin <= py <= self.height - margin)
            px = max(margin, min(self.width - margin, px))
            py = max(margin, min(self.height - margin, py))

            color = severity_color(det.dist_m)
            bw = max(16, min(60, int(det.real_width_m * self.ppm)))
            bh = max(16, int(bw * 0.8))
            label = f"{det.label} {format_distance(det.dist_m)}"
            if out_of_range:
                label = ">> " + label

            cv2.line(panel, (self.cx, self.cy), (px, py), (90, 90, 90), 1, cv2.LINE_AA)
            draw_3d_cuboid(panel, px - bw // 2, py - bh // 2, px + bw // 2, py + bh // 2,
                           color=color, scale=0.25, label=label)
        return panel
