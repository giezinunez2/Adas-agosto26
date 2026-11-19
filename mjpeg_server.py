#!/usr/bin/env python3
"""
mjpeg_server.py - Expone una webcam de Windows como stream MJPEG por HTTP.

Uso (en el venv de Windows, con la cámara libre, es decir sin main_pub.py abierto):
    python mjpeg_server.py --camera 0 --port 8080

Endpoints:
    /video          stream MJPEG (multipart/x-mixed-replace)
    /snapshot.jpg   último frame como JPEG
    /health         200 si ya hay frames, 503 si todavía no

Prueba rápida: abrir http://127.0.0.1:8080/video en el navegador.
"""

import argparse
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cv2

BACKENDS = {
    "dshow": getattr(cv2, "CAP_DSHOW", cv2.CAP_ANY),
    "msmf": getattr(cv2, "CAP_MSMF", cv2.CAP_ANY),
    "any": cv2.CAP_ANY,
}


class FrameGrabber(threading.Thread):
    """Lee la cámara en un hilo y deja el último frame ya codificado en JPEG."""

    def __init__(self, camera, backend, width, height, fps, quality):
        super().__init__(daemon=True, name="grabber")
        self.camera = camera
        self.backend = backend
        self.width = width
        self.height = height
        self.fps = fps
        self.encode_params = [int(cv2.IMWRITE_JPEG_QUALITY), quality]
        self.cond = threading.Condition()
        self.jpeg = None
        self.seq = 0
        self.halt = threading.Event()

    def _open(self):
        cap = cv2.VideoCapture(self.camera, self.backend)
        if not cap.isOpened():
            cap.release()
            return None
        # MJPG primero: la C920 solo da 720p/1080p a 30 FPS en MJPG, no en YUY2
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        cap.set(cv2.CAP_PROP_FPS, self.fps)
        return cap

    def run(self):
        cap = None
        while not self.halt.is_set():
            if cap is None:
                cap = self._open()
                if cap is None:
                    print(f"No se pudo abrir la cámara {self.camera}; reintentando en 2 s...")
                    time.sleep(2)
                    continue
                w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                f = cap.get(cv2.CAP_PROP_FPS)
                print(f"Cámara {self.camera} abierta: {w}x{h} @ {f:.0f} FPS")

            ok, frame = cap.read()
            if not ok:
                print("Frame perdido; reabriendo la cámara...")
                cap.release()
                cap = None
                time.sleep(1)
                continue

            ok, buf = cv2.imencode(".jpg", frame, self.encode_params)
            if not ok:
                continue
            with self.cond:
                self.jpeg = buf.tobytes()
                self.seq += 1
                self.cond.notify_all()

        if cap is not None:
            cap.release()


class Handler(BaseHTTPRequestHandler):
    grabber = None  # se asigna en main()

    def log_message(self, fmt, *args):  # silencia el log por request
        pass

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/video":
            self._stream()
        elif path == "/snapshot.jpg":
            self._snapshot()
        elif path == "/health":
            ready = self.grabber.jpeg is not None
            self.send_response(200 if ready else 503)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"ok" if ready else b"waiting for camera")
        else:
            self.send_error(404)

    def _snapshot(self):
        jpeg = self.grabber.jpeg
        if jpeg is None:
            self.send_error(503, "camera not ready")
            return
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(jpeg)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(jpeg)

    def _stream(self):
        g = self.grabber
        print(f"Cliente conectado: {self.client_address[0]}")
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.send_header("Cache-Control", "no-cache, private")
        self.send_header("Pragma", "no-cache")
        self.end_headers()
        last = 0
        try:
            while True:
                with g.cond:
                    g.cond.wait_for(lambda: g.seq != last, timeout=5)
                    if g.seq == last:
                        continue  # timeout sin frames nuevos
                    jpeg, last = g.jpeg, g.seq
                self.wfile.write(
                    b"--frame\r\nContent-Type: image/jpeg\r\n"
                    b"Content-Length: %d\r\n\r\n" % len(jpeg)
                )
                self.wfile.write(jpeg)
                self.wfile.write(b"\r\n")
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        finally:
            print(f"Cliente desconectado: {self.client_address[0]}")


def parse_args():
    p = argparse.ArgumentParser(description="Servidor MJPEG de webcam para Docker")
    p.add_argument("--camera", type=int, default=0, help="Índice de la cámara en Windows")
    p.add_argument("--backend", choices=list(BACKENDS), default="dshow")
    p.add_argument("--width", type=int, default=1280)
    p.add_argument("--height", type=int, default=720)
    p.add_argument("--fps", type=int, default=30)
    p.add_argument("--quality", type=int, default=80, help="Calidad JPEG (1-100)")
    p.add_argument("--host", default="127.0.0.1",
                   help="Interfaz donde escuchar. Usar 0.0.0.0 solo si 127.0.0.1 no conecta "
                        "desde el contenedor (expone la cámara a tu red local)")
    p.add_argument("--port", type=int, default=8080)
    return p.parse_args()


def main():
    args = parse_args()
    grabber = FrameGrabber(args.camera, BACKENDS[args.backend],
                           args.width, args.height, args.fps, args.quality)
    grabber.start()
    Handler.grabber = grabber

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.daemon_threads = True
    print(f"Sirviendo http://{args.host}:{args.port}/video  (Ctrl+C para salir)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nDeteniendo servidor...")
    finally:
        grabber.halt.set()
        server.server_close()


if __name__ == "__main__":
    main()
