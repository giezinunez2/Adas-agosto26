"""
Publisher Initializer Module - main_pub.py

Entry point for video streaming publisher applications.
Handles camera initialization, stream management, and real-time video publishing
with optional YOLO processing, optional BYD ADAS local preview and local preview.

Pipeline (hilo principal):  capturar -> (submit a hilo IA) -> serializar+publicar -> preview
Pipeline (hilo IA):         último frame -> YOLOv8n-seg (Hailo o CPU) -> distancias -> resultados

Modos de publicación:
    * Video crudo (por defecto):  frame LIMPIO codificado (JPEG/LZ4/PNG...). El receptor
      hace su propia inferencia (Prueba A - centralizado).
    * --edge-ai:                  además de enviar video, este nodo infiere y adjunta las
      detecciones (caja + polígono + distancias) en el Protobuf.
    * --edge-ai --no-video:       Edge Offloading (Prueba B): solo se publica el Protobuf
      ligero (is_metadata_only=True, frame_data=b"").

Ejemplo (ADASCore, Raspberry Pi 5 + AI HAT):
    python3 main_pub.py --stream-id 1 --camera 0 --edge-ai --no-video \
        --backend hailo --model models/yolov8n_seg.hef --no-preview

Los cuboides/máscaras solo se dibujan en una copia para el preview local; lo que viaja por
eCAL nunca lleva dibujos encima.

Keyboard Controls:
    - 'q': Quit application
    - 'p': Pause/resume streaming
    - 't': Adjust YOLO transparency (segmentation mode)
    - 'c': Cycle YOLO confidence thresholds
    - 's': Show current statistics
    - 'f': Show final statistics
"""

import time
from interfaces.middleware_factory import create_publisher
from user_interface.pub_visualizer import CameraManager
from user_interface.adas_renderer import draw_detection_overlay
from data_stream.pubs_stream_manager import StreamManagerPub
from img_processing.adas_detector import AsyncADASDetector
from utils.args_conf import parse_arguments
from utils.helper_functions import normalize_camera_source
from utils.pub_sub_conf import PUBLISHER_NAME, ADAS_MAX_DETECTION_AGE_S


def main():
    # Parse command line arguments
    args = parse_arguments()

    # Windows: la camara se abre SIEMPRE por indice numerico (0, 1, 2...) con
    # cv2.CAP_DSHOW / cv2.CAP_MSMF; nunca con rutas POSIX tipo /dev/video0.
    camera_source = normalize_camera_source(args.camera)

    # Create topic name
    topic_name = f"{args.topic_prefix}_{args.stream_id}"

    # ADAS: solo tiene sentido con preview local y es excluyente con el YOLO legacy
    use_adas = bool(args.adas)
    if use_adas and args.no_preview:
        print("ℹ️ --adas requiere preview local; se desactiva porque --no-preview está activo")
        use_adas = False
    if use_adas and args.enable_yolo:
        print("⚠️ --adas y --enable-yolo son excluyentes: se usa ADAS y se desactiva el YOLO legacy")

    # Edge AI: inferencia en este nodo (no depende del preview) y detecciones en el Protobuf
    use_edge = bool(args.edge_ai)
    if use_edge and args.enable_yolo:
        print("⚠️ --edge-ai y --enable-yolo son excluyentes: se usa Edge AI y se desactiva el YOLO legacy")
    need_detector = use_adas or use_edge
    backend = args.backend or ("hailo" if use_edge else "cpu")

    # Display startup banner with configuration
    print("=" * 50)
    print(f"📡 Topic: {topic_name}")
    print(f"🆔 Stream ID: {args.stream_id}")
    print(f"📷 Camera: {camera_source}")
    print(f"🔧 Encoding: {args.encoding}")
    print(f"⭐ Quality: {args.quality}")
    print(f"📏 Resolution: {args.width}x{args.height}")
    print(f"🎞️ FPS: {args.fps}")
    print(f"🔑 Keyframe every: {args.keyframe_interval} frames")
    if use_edge:
        print(f"🧠 EDGE AI: ENABLED (backend={backend}, modelo={args.model or 'por defecto'}, "
              f"conf={args.adas_conf})")
        print(f"📦 Publicación: {'SOLO METADATOS (sin video)' if args.no_video else 'video + detecciones'}")
    elif use_adas:
        print(f"🚗 BYD ADAS: ENABLED (backend={backend}, conf={args.adas_conf}, imgsz={args.adas_imgsz})")
    if not (use_edge or use_adas) and args.enable_yolo:
        print(f"🎯 YOLO: ENABLED")
        print(f"   Mode: {args.yolo_mode}")
        print(f"   Confidence: {args.yolo_conf}")
        print(f"   Interval: {args.yolo_interval}")
    print("=" * 50)

    # Initialize performance monitor
    stream_manager = StreamManagerPub()
    pub = None
    cam = None
    detector = None

    try:
        # Hilo de inferencia asíncrona (no participa en la captura ni en la publicación)
        if need_detector:
            detector = AsyncADASDetector(model_path=args.model, imgsz=args.adas_imgsz,
                                         conf=args.adas_conf, backend=backend)
            detector.start()

        # Initialize camera with context manager for automatic cleanup
        cam = CameraManager(camera_source,
                            args.width,
                            args.height,
                            args.fps,
                            enable_yolo=args.enable_yolo and not need_detector,
                            yolo_mode=args.yolo_mode,
                            yolo_interval=args.yolo_interval,
                            yolo_conf=args.yolo_conf)

        # Enter camera context (initializes hardware)
        with cam:

            # Create publisher for middleware (eCAL/RabbitMQ)
            pub = create_publisher(topic_name, f"{PUBLISHER_NAME}-{args.stream_id}")

            # Create preview window
            if not args.no_preview:
                preview_window = f"Publisher - Camera {args.stream_id}"
                cam.create_preview_window(preview_window, args.width, args.height)

            # State variables for main loop
            paused = False
            running = True

            # Main publishing loop
            while running:
                if not paused:

                    # 1. Capture frame from camera
                    frame = cam.read_frame()

                    # 2. Entregar el frame a la inferencia asíncrona (no bloquea)
                    if detector is not None:
                        detector.submit(args.stream_id, frame)

                    # 3. Serializar y publicar (video limpio, video + detecciones, o solo metadatos)
                    edge_dets, edge_ms, edge_fps = None, 0.0, 0.0
                    if use_edge:
                        edge_dets, edge_age = detector.get(args.stream_id)
                        if edge_age > ADAS_MAX_DETECTION_AGE_S:
                            edge_dets = []
                        edge_ms = detector.inference_ms
                        # ai_fps > 0 marca al nodo como Edge AI desde el primer mensaje
                        edge_fps = max(detector.ai_fps, 0.01)

                    result = stream_manager.process_frame(
                        frame=frame,
                        encoding=args.encoding,
                        quality=args.quality,
                        keyframe_interval=args.keyframe_interval,
                        publisher=pub,
                        detections=edge_dets,
                        inference_ms=edge_ms,
                        ai_fps=edge_fps,
                        metadata_only=bool(args.no_video)
                    )

                    if result['success']:
                        # 4. Show local preview
                        if not args.no_preview:
                            preview_source = frame
                            if detector is not None:
                                detections, age = detector.get(args.stream_id)
                                if age > ADAS_MAX_DETECTION_AGE_S:
                                    detections = []
                                preview_source = draw_detection_overlay(
                                    frame.copy(), detections, ai_fps=detector.ai_fps)

                            size_mb = result['metrics']['size'] / (1024 * 1024)
                            display_frame = cam.prepare_preview_frame(
                                preview_source,
                                result['message'],
                                fps=result['fps'],
                                latency=result['latency'],
                                lost=0,
                                size_mb=size_mb,
                                target_width=args.width,
                                target_height=args.height,
                                role='publisher',
                                camera_label=f"CAM {args.stream_id}"
                            )
                            cam.show_preview(preview_window, display_frame)

                        # Print stats
                        if stream_manager.should_print_stats():
                            stats = stream_manager.get_stats()
                            size_mb = result['metrics']['size'] / (1024 * 1024)
                            edge_info = ""
                            if use_edge:
                                edge_info = (f" | IA: {detector.ai_fps:.1f} fps, "
                                             f"inf {detector.inference_ms:.1f} ms, "
                                             f"obj {len(edge_dets or [])}")
                            print(f"📊 Cam{args.stream_id} | Frame {result['frame_number']:04d} | "
                                f"FPS: {stats['avg_fps']:.1f} | "
                                f"Size: {size_mb * 1024:.1f} KB | "
                                f"Latency: {result['latency'] * 1000:.1f}ms{edge_info}")
                else:
                    time.sleep(0.03)

                # Key controls (fuera del if: así 'p' también permite reanudar)
                key = cam.wait_key(1)
                if key == ord('q'):
                    running = False
                    print("⏹️ Stopping video stream...")
                elif key == ord('p'):
                    paused = not paused
                    status = "PAUSED" if paused else "RESUMED"
                    print(f"⏸️ {status}")
                elif key == ord('t') and args.enable_yolo and not need_detector and args.yolo_mode == 'segmentation':
                    # Change transparency in segmentation
                    cam.yolo_processor.set_transparency(cam.yolo_processor.transparency + 0.1)
                elif key == ord('c') and args.enable_yolo and not need_detector:
                    # Change trust
                    new_conf = cam.yolo_processor.confidence + 0.1
                    if new_conf > 0.9:
                        new_conf = 0.1
                    cam.yolo_processor.confidence = new_conf
                    print(f"🎯 Confianza YOLO ajustada a: {new_conf:.1f}")
                elif key == ord('s'):
                    stats = stream_manager.get_stats()
                    print(f"📊 CURRENT STATISTICS:")
                    print(f"   Total frames: {stats['total_frames_sent']}")
                    print(f"   Average FPS: {stats['avg_fps']:.1f}")
                    print(f"   Average size: {stats['avg_size']:.3f} MB")
                    print(f"   Average latency: {stats['avg_latency'] * 1000:.1f}ms")
                    print(f"   Total MB sent: {stats['total_mb_sent']:.2f}")
                    print(f"   Runtime: {stats['total_time']:.1f}s")
                elif key == ord('f'):
                    stream_manager.print_final_statistics()

    except KeyboardInterrupt:
        print(f"🛑 Publisher Cam{args.stream_id} stopped by user")
    except Exception as e:
        print(f"❌ Error in publisher - Cam{args.stream_id}: {e}")
        import traceback
        traceback.print_exc()
    finally:
        stream_manager.print_final_statistics()

        # Stop async inference thread
        if detector is not None:
            detector.stop()

        # Safely close resources
        try:
            if pub:
                pub.close()
                print("✅ Publisher closed correctly")
        except Exception as e:
            print(f"⚠️ Error closing publisher: {e}")

        # Close preview window
        if cam and not args.no_preview:
            cam.close_preview_windows()

if __name__ == "__main__":
    main()
