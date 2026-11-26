"""
Detector YOLOv8-seg asíncrono y multi-fuente con dos backends:

    backend="hailo"  ->  models/yolov8n_seg.hef sobre /dev/hailo0 (Hailo-8L, HailoRT 4.23)
                         Preproceso letterbox 640x640 RGB UINT8, decodificación de las
                         3 escalas (80/40/20) + prototipos (160x160x32) en el host.
    backend="cpu"    ->  yolov8n-seg.pt con ultralytics/PyTorch en CPU (solo imagen adas:pi-cpu).

Formato de los scores de clase del HEF (backend hailo):
    Según cómo se compiló el .hef, las 80 clases salen como LOGITS crudos (rango real, con
    negativos) o YA PASADAS por sigmoide ([0, 1]). Interpretar sigmoides como logits hace que
    TODAS las anclas superen el umbral (falsos positivos masivos). Se controla con:
        scores_mode="auto"     (def.) detecta el formato en el warm-up y lo reverifica en los
                               primeros frames mirando el rango [min, max] de las 3 escalas.
        scores_mode="logits"   fuerza logits (aplica sigmoide en el host).
        scores_mode="sigmoid"  fuerza probabilidades ya en [0, 1] (no vuelve a aplicar sigmoide).
    También se puede fijar con la variable de entorno ADAS_HAILO_SCORES=auto|logits|sigmoid
    (docker run -e ADAS_HAILO_SCORES=sigmoid ...) sin tocar los main_*.py.

`torch`, `ultralytics` y `hailo_platform` se importan de forma DIFERIDA: una imagen
adas:pi5 (WITH_TORCH=0) puede importar este módulo sin tener PyTorch instalado.

El hilo que captura/publica solo llama a submit() (no bloquea) y lee resultados con get().
Un hilo de inferencia propio toma el último frame pendiente de cada fuente y los procesa
en un solo batch, a la velocidad que permita el hardware.
"""

import contextlib
import math
import os
import threading
import time
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

from img_processing.adas_geometry import Detection, build_detection
from utils import pub_sub_conf as cfg

BACKENDS = ("hailo", "cpu")
DEFAULT_MODELS = {"hailo": "models/yolov8n_seg.hef", "cpu": "yolov8n-seg.pt"}

INPUT_SIZE = 640                 # entrada fija del HEF (NHWC 640x640x3)
PROTO_SIZE = 160                 # prototipos conv48: 160x160x32
GRID_SIZES = (80, 40, 20)        # escalas de salida (strides 8/16/32)
DFL_BINS = 16                    # 64 canales de caja = 4 lados x 16 bins
MAX_DETECTIONS = 30
LETTERBOX_FILL = 114

# Formato de los scores de clase del HEF
SCORES_MODES = ("auto", "logits", "sigmoid")
SCORES_ENV_VAR = "ADAS_HAILO_SCORES"
SIGMOID_TOL = 0.02               # holgura de cuantización: salida sigmoide = [0, 1] +/- tol
AUTO_PROBE_FRAMES = 30           # en modo auto se reverifica el rango durante los primeros N lotes

# Nombres COCO de las clases que usa el ADAS (el resto se descarta antes del NMS)
COCO_NAMES = {0: "person", 2: "car", 3: "motorcycle", 5: "bus", 7: "truck",
              15: "cat", 16: "dog", 39: "bottle"}


# ----------------------------------------------------------------------------
# Utilidades comunes
# ----------------------------------------------------------------------------
def _sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(x, -60.0, 60.0)))


def _simplify_polygon(points: np.ndarray, frame_w: int, frame_h: int,
                      eps_ratio: float = 0.008, min_eps: float = 1.5,
                      max_points: int = 80) -> Optional[Tuple[Tuple[int, int], ...]]:
    """cv2.approxPolyDP sobre un contorno (N x 2, px del frame) -> tupla de vértices enteros."""
    pts = np.asarray(points, dtype=np.float32).reshape(-1, 1, 2)
    if len(pts) < 3:
        return None
    eps = max(min_eps, eps_ratio * cv2.arcLength(pts, True))
    approx = cv2.approxPolyDP(pts, eps, True)
    while len(approx) > max_points:
        eps *= 1.5
        approx = cv2.approxPolyDP(pts, eps, True)
    if len(approx) < 3:
        return None
    xy = approx.reshape(-1, 2)
    xs = np.clip(np.rint(xy[:, 0]), 0, frame_w - 1).astype(int)
    ys = np.clip(np.rint(xy[:, 1]), 0, frame_h - 1).astype(int)
    return tuple((int(x), int(y)) for x, y in zip(xs, ys))


def _letterbox(frame: np.ndarray, size: int = INPUT_SIZE):
    """BGR (cualquier tamaño) -> RGB UINT8 size x size con bandas grises. Devuelve (img, scale, pad_x, pad_y)."""
    h, w = frame.shape[:2]
    scale = min(size / float(w), size / float(h))
    new_w, new_h = max(1, int(round(w * scale))), max(1, int(round(h * scale)))
    resized = frame if (new_w, new_h) == (w, h) else cv2.resize(
        frame, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
    pad_x, pad_y = (size - new_w) // 2, (size - new_h) // 2
    canvas = np.full((size, size, 3), LETTERBOX_FILL, dtype=np.uint8)
    canvas[pad_y:pad_y + new_h, pad_x:pad_x + new_w] = resized
    return cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB), scale, pad_x, pad_y


def _make_vstream_params(params_cls, network_group, format_type, quantized: bool):
    """Compatibilidad entre versiones de HailoRT (make / make_from_network_group)."""
    maker = getattr(params_cls, "make", None) or getattr(params_cls, "make_from_network_group")
    try:
        return maker(network_group, format_type=format_type)
    except TypeError:
        return maker(network_group, quantized=quantized, format_type=format_type)


class _HailoSession:
    """VDevice + red configurada + InferVStreams abiertos durante toda la vida del hilo de inferencia."""

    def __init__(self, hef_path: str):
        self.hef_path = hef_path
        self.input_name = ""
        self._pipeline = None
        self._stack = contextlib.ExitStack()

    def __enter__(self):
        try:
            from hailo_platform import (HEF, ConfigureParams, FormatType, HailoStreamInterface,
                                        InferVStreams, InputVStreamParams, OutputVStreamParams,
                                        VDevice)
        except ImportError as e:
            raise RuntimeError(
                "No se pudo importar 'hailo_platform'. Usa la imagen adas:pi5 (WITH_TORCH=0) "
                "con ./hailo_pkg y ejecuta con --device /dev/hailo0") from e
        try:
            hef = HEF(self.hef_path)
            target = self._stack.enter_context(VDevice())
            params = ConfigureParams.create_from_hef(hef, interface=HailoStreamInterface.PCIe)
            network_group = target.configure(hef, params)[0]
            group_params = network_group.create_params()
            in_params = _make_vstream_params(InputVStreamParams, network_group,
                                             FormatType.UINT8, quantized=True)
            out_params = _make_vstream_params(OutputVStreamParams, network_group,
                                              FormatType.FLOAT32, quantized=False)
            self.input_name = hef.get_input_vstream_infos()[0].name
            self._pipeline = self._stack.enter_context(
                InferVStreams(network_group, in_params, out_params))
            self._stack.enter_context(network_group.activate(group_params))
        except BaseException:
            self._stack.close()
            raise
        return self

    def __exit__(self, exc_type, exc, tb):
        self._stack.close()
        return False

    def infer(self, batch: np.ndarray) -> Dict[str, np.ndarray]:
        return self._pipeline.infer({self.input_name: batch})


# ----------------------------------------------------------------------------
# Detector
# ----------------------------------------------------------------------------
class AsyncADASDetector:
    def __init__(self, model_path: Optional[str] = None,
                 imgsz: int = cfg.ADAS_IMGSZ,
                 conf: float = cfg.ADAS_CONFIDENCE,
                 backend: str = "cpu",
                 iou: float = 0.45,
                 scores_mode: Optional[str] = None):
        if backend not in BACKENDS:
            raise ValueError(f"backend '{backend}' no válido. Usa uno de {BACKENDS}")
        # Formato de scores del HEF: parámetro > variable de entorno > "auto"
        scores_mode = (scores_mode or os.environ.get(SCORES_ENV_VAR) or "auto").strip().lower()
        if scores_mode not in SCORES_MODES:
            raise ValueError(f"scores_mode '{scores_mode}' no válido. Usa uno de {SCORES_MODES}")
        self.scores_mode = scores_mode                     # lo pedido: auto | logits | sigmoid
        self._scores_fmt: Optional[str] = None if scores_mode == "auto" else scores_mode
        self._scores_probe_left = AUTO_PROBE_FRAMES        # lotes a verificar (auto: detectar/corregir; forzado: avisar)
        self._scores_warned = False
        self.backend = backend
        self.model_path = model_path or DEFAULT_MODELS[backend]
        self.conf = conf
        self.iou = iou
        self.imgsz = INPUT_SIZE if backend == "hailo" else imgsz   # el HEF es de entrada fija
        self.names = dict(COCO_NAMES)

        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._pending: Dict[int, np.ndarray] = {}
        self._results: Dict[int, Tuple[List[Detection], float]] = {}
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._init_error: Optional[BaseException] = None
        self.ai_fps = 0.0
        self.inference_ms = 0.0

        # Solo las clases con ancho físico conocido pasan al NMS (descarta el resto de COCO)
        self._allowed = np.array(sorted(cfg.CLASS_REAL_WIDTHS), dtype=np.int64)
        c = min(max(float(conf), 1e-4), 1.0 - 1e-4)
        self._logit_thr = math.log(c / (1.0 - c))          # umbral en logits (sigmoide monótona)
        self._prob_thr = c                                 # umbral en probabilidad (HEF con sigmoide)
        self._dfl_bins = np.arange(DFL_BINS, dtype=np.float32)
        self._out_map: Optional[Dict[Tuple[int, int], str]] = None

        if backend == "cpu":
            self._init_cpu()
        else:
            if not os.path.isfile(self.model_path):
                raise FileNotFoundError(
                    f"No existe el HEF '{self.model_path}'. Móntalo con -v $(pwd)/models:/app/models")
            print(f"🧠 Backend Hailo: {self.model_path} (/dev/hailo0)")

    # ------------------------------------------------------------------ backend CPU
    def _init_cpu(self):
        try:
            import torch                      # import diferido: solo existe en adas:pi-cpu
            from ultralytics import YOLO
        except ImportError as e:
            raise RuntimeError(
                "backend 'cpu' requiere PyTorch + ultralytics (imagen adas:pi-cpu, "
                "WITH_TORCH=1). En la imagen ligera adas:pi5 usa --backend hailo o none") from e
        self.device = 0 if torch.cuda.is_available() else "cpu"
        print(f"🧠 Cargando {self.model_path} ({'GPU' if self.device == 0 else 'CPU'})...")
        self.model = YOLO(self.model_path)
        self.names = self.model.names

    def _infer_cpu(self, frames: List[np.ndarray]) -> List[List[Detection]]:
        results = self.model(frames, imgsz=self.imgsz, conf=self.conf, iou=self.iou,
                             device=self.device, verbose=False)
        return [self._extract_cpu(r, f.shape[1], f.shape[0]) for r, f in zip(results, frames)]

    def _extract_cpu(self, result, frame_w: int, frame_h: int) -> List[Detection]:
        detections: List[Detection] = []
        boxes = result.boxes
        if boxes is None or len(boxes) == 0:
            return detections

        xyxy = boxes.xyxy.cpu().numpy()
        classes = boxes.cls.cpu().numpy().astype(int)
        confs = boxes.conf.cpu().numpy()
        masks = getattr(result, "masks", None)
        polys = masks.xy if masks is not None else None      # lista de arrays (n,2) en px originales

        for n, ((x1, y1, x2, y2), cls_id, conf) in enumerate(zip(xyxy, classes, confs)):
            polygon = None
            if polys is not None and n < len(polys) and len(polys[n]) >= 3:
                polygon = _simplify_polygon(polys[n], frame_w, frame_h)
            det = build_detection(int(x1), int(y1), int(x2), int(y2), int(cls_id), float(conf),
                                  str(self.names.get(int(cls_id), "obj")), frame_w, polygon=polygon)
            if det is not None:
                detections.append(det)

        detections.sort(key=lambda d: d.dist_m)
        return detections

    # ------------------------------------------------------------------ backend Hailo
    def _infer_hailo(self, session: _HailoSession, frames: List[np.ndarray]) -> List[List[Detection]]:
        prepared = [_letterbox(f) for f in frames]
        batch = np.ascontiguousarray(np.stack([p[0] for p in prepared]))     # (N,640,640,3) UINT8
        outs = session.infer(batch)

        if self._out_map is None:
            self._out_map = self._build_output_map(outs)
        arrays = {}
        for key, name in self._out_map.items():
            arr = np.asarray(outs[name])
            arrays[key] = arr[None] if arr.ndim == 3 else arr                # (N,H,W,C)

        self._update_scores_format(arrays)

        results = []
        for i, frame in enumerate(frames):
            _, scale, pad_x, pad_y = prepared[i]
            results.append(self._decode_hailo(arrays, i, frame.shape[1], frame.shape[0],
                                              scale, pad_x, pad_y))
        return results

    @property
    def scores_format(self) -> Optional[str]:
        """Formato efectivo de los scores del HEF: 'logits' | 'sigmoid' (None = aún sin determinar)."""
        return self._scores_fmt

    @staticmethod
    def _scores_range(arrays) -> Tuple[float, float]:
        """(min, max) de los mapas de clase (80 canales) de las 3 escalas."""
        lo, hi = float("inf"), float("-inf")
        for g in GRID_SIZES:
            a = arrays[(g, 80)]
            lo, hi = min(lo, float(a.min())), max(hi, float(a.max()))
        return lo, hi

    def _update_scores_format(self, arrays) -> None:
        """
        Determina (auto) o valida (forzado) si los scores de clase son logits o sigmoides.

        Criterio: una salida sigmoide vive en [0, 1]; los logits de 80 clases x ~8400 anclas
        siempre incluyen valores muy negativos (fondo) y/o > 1. Si hay valores fuera de
        [-tol, 1+tol] son LOGITS sin ambigüedad; si todo cae dentro, son SIGMOIDES.
        """
        if self._scores_fmt is not None and self._scores_probe_left <= 0:
            return                                           # ya decidido y reverificado: sin costo por frame

        lo, hi = self._scores_range(arrays)
        in_unit = lo >= -SIGMOID_TOL and hi <= 1.0 + SIGMOID_TOL
        detected = "sigmoid" if in_unit else "logits"
        if self._scores_probe_left > 0:
            self._scores_probe_left -= 1

        if self.scores_mode == "auto":
            if self._scores_fmt == "logits":
                return                       # veredicto definitivo: valores fuera de [0,1] ya observados
            if detected != self._scores_fmt:
                # 1ª decisión, o corrección (un 'sigmoid' provisional contradicho por valores fuera de [0,1])
                if self._scores_fmt is not None:
                    print(f"⚠️ Hailo scores: corregido {self._scores_fmt} -> {detected} "
                          f"(rango observado [{lo:.3f}, {hi:.3f}])")
                else:
                    print(f"🧠 Hailo scores: {detected} (auto-detectado, rango de clases "
                          f"[{lo:.3f}, {hi:.3f}]). Forzar con {SCORES_ENV_VAR}=logits|sigmoid")
                self._scores_fmt = detected
            return

        # Modo forzado: se respeta, pero se avisa (una sola vez) si los datos lo contradicen
        if detected != self._scores_fmt and not self._scores_warned:
            self._scores_warned = True
            print(f"⚠️ Hailo scores: forzado '{self._scores_fmt}' pero el rango observado "
                  f"[{lo:.3f}, {hi:.3f}] parece '{detected}'. Revisa {SCORES_ENV_VAR} / scores_mode")

    @staticmethod
    def _build_output_map(outs: Dict[str, np.ndarray]) -> Dict[Tuple[int, int], str]:
        """Identifica cada salida por (alto, canales), sin depender de los nombres conv73, conv74..."""
        found: Dict[Tuple[int, int], str] = {}
        shapes = {}
        for name, arr in outs.items():
            h, w, c = np.asarray(arr).shape[-3:]
            found[(h, c)] = name
            shapes[name] = (h, w, c)
        required = [(g, c) for g in GRID_SIZES for c in (64, 80, 32)] + [(PROTO_SIZE, 32)]
        missing = [k for k in required if k not in found]
        if missing:
            raise RuntimeError(f"El HEF no expone las salidas esperadas {missing}. Salidas: {shapes}")
        return found

    def _decode_hailo(self, arrays, i: int, w: int, h: int,
                      scale: float, pad_x: int, pad_y: int) -> List[Detection]:
        boxes, scores, classes, coefs = [], [], [], []

        if self._scores_fmt is None:                     # llamada directa sin pasar por _infer_hailo
            self._update_scores_format(arrays)
        is_logits = self._scores_fmt != "sigmoid"
        thr = self._logit_thr if is_logits else self._prob_thr    # umbral en el espacio del HEF

        for g in GRID_SIZES:
            stride = INPUT_SIZE / float(g)
            cls_map = arrays[(g, 80)][i].reshape(-1, 80)[:, self._allowed]   # scores de clases útiles
            best_pos = cls_map.argmax(axis=1)
            best = cls_map[np.arange(cls_map.shape[0]), best_pos]
            keep = np.flatnonzero(best > thr)
            if keep.size == 0:
                continue

            # DFL: softmax sobre 16 bins y esperanza -> distancias l, t, r, b (en celdas)
            reg = arrays[(g, 64)][i].reshape(-1, 64)[keep].reshape(-1, 4, DFL_BINS)
            reg = np.exp(reg - reg.max(axis=2, keepdims=True))
            reg /= reg.sum(axis=2, keepdims=True)
            dist = (reg * self._dfl_bins).sum(axis=2)

            gy, gx = np.divmod(keep, g)
            ax, ay = gx + 0.5, gy + 0.5
            boxes.append(np.stack([(ax - dist[:, 0]) * stride, (ay - dist[:, 1]) * stride,
                                   (ax + dist[:, 2]) * stride, (ay + dist[:, 3]) * stride], axis=1))
            scores.append(_sigmoid(best[keep]) if is_logits else np.clip(best[keep], 0.0, 1.0))
            classes.append(self._allowed[best_pos[keep]])
            coefs.append(arrays[(g, 32)][i].reshape(-1, 32)[keep])

        if not boxes:
            return []

        boxes = np.clip(np.concatenate(boxes).astype(np.float32), 0, INPUT_SIZE)
        scores = np.concatenate(scores).astype(np.float32)
        classes = np.concatenate(classes)
        coefs = np.concatenate(coefs).astype(np.float32)

        valid = ((boxes[:, 2] - boxes[:, 0]) >= 1) & ((boxes[:, 3] - boxes[:, 1]) >= 1)
        if not valid.any():
            return []
        boxes, scores, classes, coefs = boxes[valid], scores[valid], classes[valid], coefs[valid]

        # NMS por clase: se desplazan las cajas de cada clase para que no se solapen entre sí
        shifted = boxes + classes.astype(np.float32)[:, None] * (INPUT_SIZE + 1)
        rects = np.rint(np.stack([shifted[:, 0], shifted[:, 1],
                                  shifted[:, 2] - shifted[:, 0],
                                  shifted[:, 3] - shifted[:, 1]], axis=1)).astype(int).tolist()
        idx = cv2.dnn.NMSBoxes(rects, scores.tolist(), float(self.conf), float(self.iou))
        idx = np.asarray(idx, dtype=int).reshape(-1)[:MAX_DETECTIONS]
        if idx.size == 0:
            return []

        # Máscaras: coeficientes (K,32) x prototipos (160*160,32)^T -> sigmoide
        protos = arrays[(PROTO_SIZE, 32)][i].reshape(-1, 32).astype(np.float32)
        mask_probs = _sigmoid(protos @ coefs[idx].T).reshape(PROTO_SIZE, PROTO_SIZE, -1)

        detections: List[Detection] = []
        for k, j in enumerate(idx):
            x1, y1, x2, y2 = boxes[j]
            ox1 = int(np.clip((x1 - pad_x) / scale, 0, w - 1))
            oy1 = int(np.clip((y1 - pad_y) / scale, 0, h - 1))
            ox2 = int(np.clip((x2 - pad_x) / scale, 0, w - 1))
            oy2 = int(np.clip((y2 - pad_y) / scale, 0, h - 1))
            polygon = self._mask_to_polygon(mask_probs[:, :, k], (x1, y1, x2, y2),
                                            w, h, scale, pad_x, pad_y)
            cls_id = int(classes[j])
            det = build_detection(ox1, oy1, ox2, oy2, cls_id, float(scores[j]),
                                  COCO_NAMES.get(cls_id, "obj"), w, polygon=polygon)
            if det is not None:
                detections.append(det)

        detections.sort(key=lambda d: d.dist_m)
        return detections

    @staticmethod
    def _mask_to_polygon(prob: np.ndarray, box, w: int, h: int, scale: float,
                         pad_x: int, pad_y: int) -> Optional[Tuple[Tuple[int, int], ...]]:
        """Máscara 160x160 -> contorno simplificado en píxeles del frame original."""
        ratio = INPUT_SIZE // PROTO_SIZE                                     # 4
        x1, y1, x2, y2 = box
        px1, py1 = max(int(x1 / ratio), 0), max(int(y1 / ratio), 0)
        px2, py2 = min(int(math.ceil(x2 / ratio)), PROTO_SIZE), min(int(math.ceil(y2 / ratio)), PROTO_SIZE)
        if px2 <= px1 or py2 <= py1:
            return None

        roi = prob[py1:py2, px1:px2]
        up = cv2.resize(roi, ((px2 - px1) * ratio, (py2 - py1) * ratio), interpolation=cv2.INTER_LINEAR)
        binary = (up > 0.5).astype(np.uint8)

        # Recorte exacto a la caja (el ROI está redondeado a celdas de 4 px)
        ox, oy = px1 * ratio, py1 * ratio
        bx1, by1 = int(max(x1 - ox, 0)), int(max(y1 - oy, 0))
        bx2, by2 = int(math.ceil(x2 - ox)), int(math.ceil(y2 - oy))
        binary[:, :bx1] = 0
        binary[:, bx2:] = 0
        binary[:by1, :] = 0
        binary[by2:, :] = 0

        contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return None
        pts = max(contours, key=cv2.contourArea).reshape(-1, 2).astype(np.float32)
        pts[:, 0] = (pts[:, 0] + ox - pad_x) / scale
        pts[:, 1] = (pts[:, 1] + oy - pad_y) / scale
        return _simplify_polygon(pts, w, h)

    # ------------------------------------------------------------------ ciclo de vida
    def start(self):
        """Arranca el hilo de inferencia y espera a que el modelo esté listo (warm-up incluido)."""
        if self._running:
            return
        self._running = True
        self._ready.clear()
        self._init_error = None
        self._thread = threading.Thread(target=self._thread_main, name="adas-inference", daemon=True)
        self._thread.start()

        if not self._ready.wait(timeout=120.0):
            self._running = False
            raise RuntimeError("Timeout inicializando el detector ADAS")
        if self._init_error is not None:
            self._running = False
            raise RuntimeError(f"No se pudo iniciar el detector ({self.backend}): "
                               f"{self._init_error}") from self._init_error
        print(f"✅ Detector ADAS listo ({self.backend})")

    def _thread_main(self):
        try:
            if self.backend == "hailo":
                with _HailoSession(self.model_path) as session:
                    infer = lambda frames: self._infer_hailo(session, frames)   # noqa: E731
                    self._warmup(infer)
                    self._ready.set()
                    self._loop(infer)
            else:
                self._warmup(self._infer_cpu)
                self._ready.set()
                self._loop(self._infer_cpu)
        except BaseException as e:                       # noqa: BLE001
            if not self._ready.is_set():
                self._init_error = e
                self._ready.set()
            else:
                print(f"❌ El hilo de inferencia ADAS terminó: {e}")
        finally:
            self._running = False

    def _warmup(self, infer):
        """Evita el freeze de la primera inferencia y valida las salidas del modelo."""
        dummy = np.zeros((self.imgsz, self.imgsz, 3), dtype=np.uint8)
        infer([dummy])

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
            self._thread.join(timeout=5.0)
            self._thread = None

    # ------------------------------------------------------------------ hilo de inferencia
    def _loop(self, infer):
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
                detections_per_frame = infer(frames)
            except Exception as e:
                print(f"❌ Error de inferencia ADAS ({self.backend}): {e}")
                time.sleep(0.05)
                continue

            timestamp = time.time()
            with self._lock:
                for sid, dets in zip(source_ids, detections_per_frame):
                    self._results[sid] = (dets, timestamp)

            elapsed = timestamp - t0
            if elapsed > 0:
                ms = elapsed * 1000.0
                self.inference_ms = ms if self.inference_ms == 0 else 0.8 * self.inference_ms + 0.2 * ms
                instant_fps = 1.0 / elapsed
                self.ai_fps = instant_fps if self.ai_fps == 0 else 0.8 * self.ai_fps + 0.2 * instant_fps
