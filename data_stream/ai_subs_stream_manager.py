"""
Gestor de suscripciones multi-canal para el receptor AI.

Mantiene el último mensaje de cada stream (webcam_stream_1..4), métricas de recepción
(FPS, latencia, frames perdidos) y decodifica bajo demanda solo cuando hay un frame nuevo.

Recepción HÍBRIDA por stream:
    - Nodo Edge AI con video       -> frame + detecciones (msg.objects) ya calculadas.
    - Nodo Edge AI sin video       -> solo metadatos (is_metadata_only=True): frame None + detecciones.
    - Nodo sin AI HAT (video crudo)-> frame sin detecciones; el receptor puede inferir en local
                                      (--backend cpu|hailo) o mostrarlo sin IA (--backend none).
"""

import threading
import time
from typing import Dict, List, Optional, Tuple

import numpy as np

from interfaces.middleware_factory import create_subscriber
from img_processing.adas_geometry import Detection
from img_processing.video_serializer import proto_to_detections, proto_to_frame
from utils import pub_sub_conf


def _patch_ecal_bytes_compat():
    """
    Compatibilidad bytearray -> bytes para ProtoSubscriber (ya usada en byd_adas_ecal.py).
    Es inocua si eCAL no está instalado o la versión no expone _on_receive.
    """
    try:
        from ecal.core.subscriber import ProtoSubscriber
    except ImportError:
        return
    if not hasattr(ProtoSubscriber, "_on_receive") or getattr(ProtoSubscriber, "_adas_patched", False):
        return

    original = ProtoSubscriber._on_receive

    def patched(self, topic_name, msg, time_stamp):
        if isinstance(msg, (bytearray, memoryview)):
            msg = bytes(msg)
        return original(self, topic_name, msg, time_stamp)

    ProtoSubscriber._on_receive = patched
    ProtoSubscriber._adas_patched = True


class AIStreamManagerSub:
    def __init__(self, topic_prefix: str, stream_ids: List[int]):
        self.topic_prefix = topic_prefix
        self.stream_ids = list(stream_ids)
        self._subs: Dict[int, object] = {}
        self._lock = threading.Lock()
        self._states: Dict[int, dict] = {sid: self._new_state() for sid in self.stream_ids}

    @staticmethod
    def _new_state() -> dict:
        now = time.time()
        return {
            'msg': None, 'frame': None, 'dets': [], 'dec_meta': False,
            'decoded_number': -1, 'served_number': -1, 'frame_number': -1,
            'last_rx': 0.0, 'received': 0, 'lost': 0, 'last_number': -1,
            'win_start': now, 'win_frames': 0, 'fps': 0.0,
            'latency': 0.0, 'encoding': 1, 'quality': 0,
            'metadata_only': False, 'edge_ai': False,
            'inference_ms': 0.0, 'ai_fps': 0.0, 'width': 0, 'height': 0,
        }

    def topic_for(self, stream_id: int) -> str:
        return f"{self.topic_prefix}_{stream_id}"

    # ------------------------------------------------------------------ conexión
    def setup_subscribers(self):
        _patch_ecal_bytes_compat()
        for sid in self.stream_ids:
            topic = self.topic_for(sid)
            self._subs[sid] = create_subscriber(
                topic, self._make_callback(sid), f"{pub_sub_conf.SUBSCRIBER_NAME}-AI-{sid}")
            print(f"✅ Suscrito a '{topic}'")

    def _make_callback(self, stream_id: int):
        def _callback(topic_name, msg, time_stamp):
            self._on_message(stream_id, msg)
        return _callback

    def _on_message(self, stream_id: int, msg):
        """Se ejecuta en el hilo del middleware: debe ser rápido (solo guarda y mide)."""
        now = time.time()
        with self._lock:
            st = self._states[stream_id]
            st['msg'] = msg
            st['last_rx'] = now
            st['received'] += 1
            st['win_frames'] += 1

            number = getattr(msg, 'frame_number', st['last_number'] + 1)
            if st['last_number'] >= 0 and number > st['last_number'] + 1:
                st['lost'] += number - st['last_number'] - 1
            st['last_number'] = number

            st['latency'] = max(0.0, now - getattr(msg, 'timestamp', now))
            st['encoding'] = getattr(msg, 'encoding', 1)
            st['quality'] = getattr(msg, 'compression_quality', 0)
            st['metadata_only'], st['edge_ai'] = self._edge_flags(msg)
            st['inference_ms'] = getattr(msg, 'inference_ms', 0.0)
            st['ai_fps'] = getattr(msg, 'ai_fps', 0.0)
            st['width'] = getattr(msg, 'width', 0)
            st['height'] = getattr(msg, 'height', 0)

            elapsed = now - st['win_start']
            if elapsed >= 2.0:
                st['fps'] = st['win_frames'] / elapsed
                st['win_frames'] = 0
                st['win_start'] = now

    @staticmethod
    def _edge_flags(msg) -> Tuple[bool, bool]:
        """(metadata_only, edge_ai). edge_ai = el nodo emisor corre la inferencia (Hailo)."""
        metadata_only = bool(getattr(msg, 'is_metadata_only', False))
        edge_ai = (metadata_only
                   or getattr(msg, 'ai_fps', 0.0) > 0.0
                   or getattr(msg, 'inference_ms', 0.0) > 0.0
                   or len(getattr(msg, 'objects', ())) > 0)
        return metadata_only, edge_ai

    # ------------------------------------------------------------------ lectura
    def get_stream(self, stream_id: int) -> Tuple[Optional[np.ndarray], List[Detection], dict]:
        """
        Devuelve (frame_BGR | None, detecciones_remotas, info).

        - frame es None si el nodo envía solo metadatos (o aún no llegó nada decodificable).
        - detecciones_remotas viene del Protobuf (vacía para streams sin IA en el emisor);
          info['edge_ai'] indica si esa lista es confiable aunque esté vacía.
        - Decodifica solo si hay un frame_number nuevo. info['new'] es True la primera vez
          que se entrega cada mensaje.
        """
        with self._lock:
            st = self._states[stream_id]
            msg = st['msg']
            number = getattr(msg, 'frame_number', -1) if msg is not None else -1
            need_decode = msg is not None and number != st['decoded_number']

        if need_decode:
            metadata_only, edge_ai = self._edge_flags(msg)
            frame = None if metadata_only else proto_to_frame(msg)   # fuera del lock: es lo costoso
            dets = proto_to_detections(msg) if edge_ai else []
            with self._lock:
                if metadata_only:
                    st['frame'] = None
                elif frame is not None:
                    st['frame'] = frame
                st['dets'] = dets
                st['dec_meta'] = metadata_only
                st['decoded_number'] = number    # evita reintentar un frame corrupto en bucle

        with self._lock:
            frame = st['frame']
            has_content = frame is not None or (st['dec_meta'] and st['decoded_number'] >= 0)
            is_new = has_content and st['decoded_number'] != st['served_number']
            st['served_number'] = st['decoded_number']
            now = time.time()
            age = now - st['last_rx'] if st['last_rx'] > 0 else float('inf')
            active = has_content and age < pub_sub_conf.ADAS_STREAM_TIMEOUT_S
            dets = list(st['dets'])
            info = {
                'active': active, 'new': is_new, 'age': age,
                'fps': st['fps'], 'latency': st['latency'], 'lost': st['lost'],
                'encoding': st['encoding'], 'quality': st['quality'],
                'frame_number': st['decoded_number'], 'received': st['received'],
                'metadata_only': st['metadata_only'], 'edge_ai': st['edge_ai'],
                'inference_ms': st['inference_ms'], 'ai_fps': st['ai_fps'],
                'width': st['width'], 'height': st['height'],
            }
        return frame, dets, info

    def get_frame(self, stream_id: int) -> Tuple[Optional[np.ndarray], dict]:
        """Compatibilidad con el API anterior: (frame_BGR | None, info)."""
        frame, _, info = self.get_stream(stream_id)
        return frame, info

    # ------------------------------------------------------------------ utilidades
    def print_statistics(self):
        print("📊 ESTADÍSTICAS DEL RECEPTOR AI")
        with self._lock:
            for sid in self.stream_ids:
                st = self._states[sid]
                mode = ("EDGE-META" if st['metadata_only'] else
                        "EDGE+VIDEO" if st['edge_ai'] else "VIDEO-CRUDO")
                edge = (f" | IA nodo {st['ai_fps']:.1f} fps, inf {st['inference_ms']:.1f} ms"
                        if st['edge_ai'] else "")
                print(f"   CAM {sid} ({self.topic_for(sid)}) [{mode}]: recibidos={st['received']} "
                      f"perdidos={st['lost']} fps={st['fps']:.1f} "
                      f"lat={st['latency'] * 1000:.0f}ms{edge}")

    def stop(self):
        for sid, sub in self._subs.items():
            try:
                sub.close()
            except Exception as e:
                print(f"⚠️ Error cerrando suscriptor CAM {sid}: {e}")
        self._subs.clear()
