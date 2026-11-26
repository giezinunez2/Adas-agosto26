"""
Subscriber AI Module - main_sub_ai.py

Receptor / agregador HMI: se suscribe a hasta 4 cámaras eCAL (webcam_stream_1..4), fusiona las
detecciones en un plano cartesiano (BEV), dispara alertas críticas y dibuja el dashboard 1280x720.

Backends de inferencia (--backend):
    none   (por defecto)  NO infiere y NO importa PyTorch. Recibe las detecciones que ya calcularon
                          los nodos Edge AI (Prueba B - Edge Offloading). Imagen ligera adas:pi5.
    cpu                   Infiere yolov8n-seg.pt en la CPU de esta Pi sobre video crudo
                          (Prueba A - centralizado). Requiere la imagen adas:pi-cpu.
    hailo                 Infiere con el HEF en /dev/hailo0 de esta misma Pi (si tuviera AI HAT).

Uso:
    python3 main_sub_ai.py --streams 1 2 3 4                       # Prueba B (ligero)
    python3 main_sub_ai.py --streams 1 2 3 4 --backend cpu \
        --model yolov8n-seg.pt --imgsz 640                         # Prueba A (centralizado)
"""

import argparse

from data_stream.ai_subs_stream_manager import AIStreamManagerSub
from user_interface.ai_grid_visualizer import ADASGridVisualizer
from utils import pub_sub_conf, connection_conf


def stream_id_type(value: str) -> int:
    ivalue = int(value)
    if not 1 <= ivalue <= 4:
        raise argparse.ArgumentTypeError(f"Stream-id {ivalue} no válido. Usar valores entre 1 y 4")
    return ivalue


def parse_arguments():
    parser = argparse.ArgumentParser(description='BYD ADAS - Receptor AI multi-cámara')
    parser.add_argument('--streams', type=stream_id_type, nargs='+',
                        default=list(range(1, min(4, pub_sub_conf.NUM_STREAMS) + 1)),
                        help='IDs de streams a suscribir (1-4). Ej: --streams 1 2 3 4')
    parser.add_argument('--topic-prefix', type=str, default=connection_conf.TOPIC_PREFIX,
                        help='Prefijo del topic (debe coincidir con el emisor)')
    parser.add_argument('--backend', type=str, choices=['none', 'cpu', 'hailo'], default='none',
                        help='none: sin IA local (usa las detecciones de los nodos Edge, sin PyTorch); '
                             'cpu: yolov8n-seg.pt en CPU; hailo: HEF en /dev/hailo0')
    parser.add_argument('--model', type=str, default=None,
                        help='Modelo para --backend cpu (.pt, def. yolov8n-seg.pt) o '
                             'hailo (.hef, def. models/yolov8n_seg.hef)')
    parser.add_argument('--conf', type=float, default=pub_sub_conf.ADAS_CONFIDENCE,
                        help='Confianza mínima de detección (0.0 a 1.0)')
    parser.add_argument('--imgsz', type=int, default=pub_sub_conf.ADAS_IMGSZ,
                        help='Tamaño de inferencia YOLO')
    return parser.parse_args()


def main():
    args = parse_arguments()
    stream_ids = sorted(set(args.streams))

    print("=" * 50)
    print("🚗 BYD ADAS - Receptor AI")
    print(f"📡 Topics: {[f'{args.topic_prefix}_{s}' for s in stream_ids]}")
    if args.backend == 'none':
        print("🧠 Backend: none (sin inferencia local; detecciones desde los nodos Edge AI)")
    else:
        print(f"🧠 Backend: {args.backend} | modelo={args.model or 'por defecto'} | "
              f"imgsz={args.imgsz} | conf={args.conf}")
    print("=" * 50)

    detector = None
    stream_manager = None
    visualizer = None

    try:
        if args.backend != 'none':
            # Import diferido: con --backend none no se toca torch/ultralytics/hailo_platform
            from img_processing.adas_detector import AsyncADASDetector
            detector = AsyncADASDetector(model_path=args.model, imgsz=args.imgsz,
                                         conf=args.conf, backend=args.backend)
            detector.start()

        stream_manager = AIStreamManagerSub(args.topic_prefix, stream_ids)
        visualizer = ADASGridVisualizer(stream_manager, detector, stream_ids)

        stream_manager.setup_subscribers()
        print("\n⏳ Esperando datos de los publicadores...")
        visualizer.run_visualization()

    except KeyboardInterrupt:
        print("\n🛑 Aplicación detenida por el usuario")
    except Exception as e:
        print(f"\n❌ Error inesperado: {e}")
        import traceback
        traceback.print_exc()
    finally:
        print("🔄 Liberando recursos...")
        for name, action in (("visualizador", lambda: visualizer and visualizer.stop()),
                             ("detector", lambda: detector and detector.stop()),
                             ("stream manager", lambda: stream_manager and stream_manager.stop())):
            try:
                action()
            except Exception as e:
                print(f"⚠️ Error deteniendo {name}: {e}")
        print("✅ Aplicación finalizada correctamente")


if __name__ == "__main__":
    main()
