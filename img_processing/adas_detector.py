"""
Detector YOLOv8 asíncrono y multi-fuente.

El hilo que captura/publica solo llama a submit() (no bloquea) y lee resultados con get().
Un hilo de inferencia propio toma el último frame pendiente de cada fuente y los procesa
en un solo batch, a la velocidad que permita el hardware.
"""

import threading
import time
from typing import Dict, List, Optional, Tuple

import numpy as np
import torch
from ultralytics import YOLO

from img_processing.adas_geometry import Detection, build_detection
from utils import pub_sub_conf as cfg


class AsyncADASDetector:
    def __init__(self, model_path: str = cfg.ADAS_MODEL_PATH,
                 imgsz: int = cfg.ADAS_IMGSZ,
                 conf: float = cfg.ADAS_CONFIDENCE):
        self.imgsz = imgsz
        self.conf = conf
        self.device = 0 if torch.cuda.is_available() else "cpu"

        print(f"🧠 Cargando {model_path} ({'GPU' if self.device == 0 else 'CPU'})...")
        self.model = YOLO(model_path)
        self.names = self.model.names

        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._pending: Dict[int, np.ndarray] = {}
        self._results: Dict[int, Tuple[List[Detection], float]] = {}
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self.ai_fps = 0.0

    def start(self):
        """Hace warm-up del modelo (evita el freeze de la primera inferencia) y lanza el hilo."""
        if self._running:
            return
        dummy = np.zeros((self.imgsz, self.imgsz, 3), dtype=np.uint8)
        self.model(dummy, imgsz=self.imgsz, conf=self.conf, device=self.device, verbose=False)
        self._running = True
        self._thread = threading.Thread(target=self._worker, name="adas-inference", daemon=True)
        self._thread.start()
        print("✅ Detector ADAS listo")

    def submit(self, source_id: int, frame: np.ndarray):
        """Registra el último frame de una fuente. No bloquea; reemplaza el frame pendiente anterior."""
        with self._lock:
            self._pending[source_id] = frame
            self._wake.set()

    def get(self, source_id: int) -> Tuple[List[Detection], float]:
        """Devuelve (detecciones, edad_en_segundos). Sin resultados -> ([], inf)."""
        with self._lock:
            entry = self._results.get(source_id)
        if entry is None:
            return [], float("inf")
        detections, timestamp = entry
        return list(detections), time.time() - timestamp

    def forget(self, source_id: int):
        """Descarta resultados/pendientes de una fuente (p. ej. cámara desconectada)."""
        with self._lock:
            self._pending.pop(source_id, None)
            self._results.pop(source_id, None)

    def stop(self):
        self._running = False
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    # ------------------------------------------------------------------ interno
    def _extract(self, result, frame_width: int) -> List[Detection]:
        detections: List[Detection] = []
        boxes = result.boxes
        if boxes is None or len(boxes) == 0:
            return detections

        xyxy = boxes.xyxy.cpu().numpy()
        classes = boxes.cls.cpu().numpy().astype(int)
        confs = boxes.conf.cpu().numpy()

        for (x1, y1, x2, y2), cls_id, conf in zip(xyxy, classes, confs):
            det = build_detection(int(x1), int(y1), int(x2), int(y2), int(cls_id), float(conf),
                                  str(self.names.get(int(cls_id), "obj")), frame_width)
            if det is not None:
                detections.append(det)

        detections.sort(key=lambda d: d.dist_m)
        return detections

    def _worker(self):
        while self._running:
            if not self._wake.wait(timeout=0.1):
                continue

            with self._lock:
                batch = dict(self._pending)
                self._pending.clear()
                self._wake.clear()

            if not batch:
                continue

            source_ids = list(batch.keys())
            frames = [batch[sid] for sid in source_ids]
            t0 = time.time()

            try:
                results = self.model(frames, imgsz=self.imgsz, conf=self.conf,
                                     device=self.device, verbose=False)
            except Exception as e:
                print(f"❌ Error de inferencia ADAS: {e}")
                time.sleep(0.05)
                continue

            timestamp = time.time()
            new_results = {}
            for sid, frame, result in zip(source_ids, frames, results):
                new_results[sid] = (self._extract(result, frame.shape[1]), timestamp)

            with self._lock:
                self._results.update(new_results)

            elapsed = timestamp - t0
            if elapsed > 0:
                instant_fps = 1.0 / elapsed
                self.ai_fps = instant_fps if self.ai_fps == 0 else 0.8 * self.ai_fps + 0.2 * instant_fps