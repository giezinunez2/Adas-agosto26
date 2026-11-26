"""
Dashboard 1280x720 del receptor AI:
    Izquierda (800 px): cuadrícula 2x2 con las cámaras, cuboides 3D y distancias.
    Derecha  (480 px): BEV / plano cartesiano centrado en el EGO CAR.
    Arriba: banner rojo destellante si hay un objeto a menos de 1.50 m.

Recepción híbrida por cámara:
    - Edge AI con video     -> overlay (máscaras + cuboides) sobre el video recibido.
    - Edge AI solo metadatos-> canvas HUD calibrado (make_edge_telemetry_canvas).
    - Video crudo           -> inferencia local si hay detector (--backend cpu|hailo);
                               sin detector (--backend none) se muestra el video sin IA.
El detector es OPCIONAL: con --backend none no se importa PyTorch ni se carga ningún modelo.
"""

from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

import cv2
import numpy as np

from data_stream.ai_subs_stream_manager import AIStreamManagerSub
from img_processing.adas_geometry import Detection, SEVERITY_DANGER, ego_position, severity_level
from user_interface.adas_renderer import (BEVRenderer, draw_alert_banner,
                                          draw_detection_overlay, fit_to_cell,
                                          make_edge_telemetry_canvas)
from user_interface.text_labels_overlay import get_encoding_name
from utils import pub_sub_conf as cfg

if TYPE_CHECKING:  # solo para anotaciones; no importa torch ni hailo en tiempo de ejecución
    from img_processing.adas_detector import AsyncADASDetector

BG_COLOR = (20, 24, 33)
MAX_SLOTS = 4


class ADASGridVisualizer:
    WINDOW_NAME = "BYD ADAS 3D Simulator - Receptor AI"

    def __init__(self, stream_manager: AIStreamManagerSub,
                 detector: Optional["AsyncADASDetector"],
                 stream_ids: List[int]):
        self.sm = stream_manager
        self.detector = detector                  # None con --backend none
        self._edge_fps: Dict[int, float] = {}     # ai_fps reportado por cada nodo Edge activo
        self.stream_ids = list(stream_ids)[:MAX_SLOTS]

        self.canvas_w = cfg.ADAS_DASHBOARD_WIDTH
        self.canvas_h = cfg.ADAS_DASHBOARD_HEIGHT
        self.left_w = cfg.ADAS_LEFT_PANEL_WIDTH
        self.cell_w = self.left_w // 2
        self.cell_h = self.canvas_h // 2
        self.bev = BEVRenderer(self.canvas_w - self.left_w, self.canvas_h)
        self.running = False

    # ------------------------------------------------------------------ celdas
    def _cell_origin(self, slot: int) -> Tuple[int, int]:
        row, col = divmod(slot, 2)
        return col * self.cell_w, row * self.cell_h

    @staticmethod
    def _text_bar(img, text: str, org: Tuple[int, int], color=(0, 255, 255)):
        (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)
        x, y = org
        cv2.rectangle(img, (x - 3, y - th - 4), (x + tw + 3, y + 4), (0, 0, 0), -1)
        cv2.putText(img, text, (x, y), cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1, cv2.LINE_AA)

    def _resolve_detections(self, stream_id: int, frame: Optional[np.ndarray],
                            remote_dets: List[Detection], info: dict) -> Tuple[List[Detection], str]:
        """Elige la fuente de detecciones de la cámara: nodo Edge, inferencia local o ninguna."""
        if info['edge_ai']:
            if self.detector is not None:
                self.detector.forget(stream_id)
            dets = remote_dets if info['age'] <= cfg.ADAS_MAX_DETECTION_AGE_S else []
            return dets, "EDGE"

        if frame is not None and self.detector is not None:
            if info['new']:
                self.detector.submit(stream_id, frame)
            dets, age = self.detector.get(stream_id)
            if age > cfg.ADAS_MAX_DETECTION_AGE_S:
                dets = []
            return dets, self.detector.backend.upper()

        return [], "SIN IA"

    def _render_cell(self, canvas: np.ndarray, slot: int, stream_id: Optional[int]
                     ) -> Tuple[bool, List[Tuple[Detection, float, float, int]]]:
        x0, y0 = self._cell_origin(slot)
        cell = np.zeros((self.cell_h, self.cell_w, 3), dtype=np.uint8)
        objects: List[Tuple[Detection, float, float, int]] = []
        active = False

        if stream_id is None:
            cv2.putText(cell, "CAMARA NO CONFIGURADA", (20, self.cell_h // 2),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (90, 90, 90), 1, cv2.LINE_AA)
        else:
            frame, remote_dets, info = self.sm.get_stream(stream_id)
            label = cfg.CAMERA_LABELS.get(stream_id, "")

            if info['active']:
                active = True
                detections, source = self._resolve_detections(stream_id, frame, remote_dets, info)

                if frame is not None:
                    view = draw_detection_overlay(frame.copy(), detections)
                else:   # nodo Edge sin video: HUD calibrado con polígonos y cuboides
                    view = make_edge_telemetry_canvas(
                        info['width'] or cfg.FRAME_WIDTH, info['height'] or cfg.FRAME_HEIGHT,
                        detections, ai_fps=info['ai_fps'], inference_ms=info['inference_ms'])
                cell = fit_to_cell(view, self.cell_w, self.cell_h)

                for det in detections:
                    ex, ey = ego_position(det, stream_id)
                    objects.append((det, ex, ey, stream_id))

                enc = "META" if info['metadata_only'] else get_encoding_name(info['encoding'])
                stats = (f"FPS {info['fps']:.1f} | LAT {info['latency'] * 1000:.0f}ms | "
                         f"LOST {info['lost']} | {enc}")
                self._text_bar(cell, stats, (8, self.cell_h - 10), (200, 200, 255))
                if info['edge_ai']:
                    self._edge_fps[stream_id] = info['ai_fps']
                    self._text_bar(cell, f"NODO IA {info['ai_fps']:.1f} fps | inf {info['inference_ms']:.1f} ms",
                                   (8, self.cell_h - 32), (0, 255, 0))
                self._text_bar(cell, f"CAM {stream_id} {label} [{source}]", (8, 20), (0, 255, 255))
            else:
                if self.detector is not None:
                    self.detector.forget(stream_id)
                self._text_bar(cell, f"CAM {stream_id} {label}", (8, 20), (0, 255, 255))
                cv2.putText(cell, "SIN SENAL", (self.cell_w // 2 - 60, self.cell_h // 2),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2, cv2.LINE_AA)

        cv2.rectangle(cell, (0, 0), (self.cell_w - 1, self.cell_h - 1), (70, 85, 110), 1)
        canvas[y0:y0 + self.cell_h, x0:x0 + self.cell_w] = cell
        return active, objects

    # ------------------------------------------------------------------ loop
    def run_visualization(self):
        cv2.namedWindow(self.WINDOW_NAME, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(self.WINDOW_NAME, self.canvas_w, self.canvas_h)
        self.running = True
        shown = False

        while self.running:
            canvas = np.empty((self.canvas_h, self.canvas_w, 3), dtype=np.uint8)
            canvas[:] = BG_COLOR

            bev_objects: List[Tuple[Detection, float, float, int]] = []
            active_count = 0
            self._edge_fps.clear()

            for slot in range(MAX_SLOTS):
                sid = self.stream_ids[slot] if slot < len(self.stream_ids) else None
                active, objs = self._render_cell(canvas, slot, sid)
                active_count += int(active)
                bev_objects.extend(objs)

            canvas[:, self.left_w:] = self.bev.render([(d, ex, ey) for d, ex, ey, _ in bev_objects])

            # Banner crítico: el objeto más cercano en rango de peligro
            critical = [o for o in bev_objects if severity_level(o[0].dist_m) == SEVERITY_DANGER]
            if critical:
                det, _, _, sid = min(critical, key=lambda o: o[0].dist_m)
                draw_alert_banner(canvas, f"ALERTA CRITICA: {det.label.upper()} A "
                                          f"{det.dist_m:.2f}m (CAM {sid})")

            ia_parts = []
            if self.detector is not None:
                ia_parts.append(f"IA {self.detector.backend}: {self.detector.ai_fps:.1f}")
            if self._edge_fps:
                ia_parts.append(f"IA EDGE: {sum(self._edge_fps.values()) / len(self._edge_fps):.1f}")
            ia_text = " | ".join(ia_parts) if ia_parts else "IA: sin inferencia"
            footer = (f"{ia_text} | Cams {active_count}/{len(self.stream_ids)} | "
                      f"q: salir | d: stats")
            cv2.putText(canvas, footer, (self.left_w + 10, self.canvas_h - 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.38, (0, 255, 0), 1, cv2.LINE_AA)

            cv2.imshow(self.WINDOW_NAME, canvas)
            shown = True
            key = cv2.waitKey(5) & 0xFF
            if key in (ord('q'), 27):
                self.running = False
            elif key == ord('d'):
                self.sm.print_statistics()

            if shown and cv2.getWindowProperty(self.WINDOW_NAME, cv2.WND_PROP_VISIBLE) < 1:
                self.running = False

    def stop(self):
        self.running = False
        cv2.destroyAllWindows()
