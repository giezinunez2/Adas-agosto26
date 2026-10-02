"""
Subscriber AI Module - main_sub_ai.py

Receptor con IA: se suscribe a hasta 4 cámaras eCAL (webcam_stream_1..4), ejecuta YOLOv8n
de forma asíncrona, calcula distancias y proyecta las detecciones a un plano cartesiano (BEV).

Uso:
    python main_sub_ai.py                      # streams 1..NUM_STREAMS
    python main_sub_ai.py --streams 1 2        # solo cámaras 1 y 2
"""

import argparse

from data_stream.ai_subs_stream_manager import AIStreamManagerSub
from img_processing.adas_detector import AsyncADASDetector
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
    parser.add_argument('--model', type=str, default=pub_sub_conf.ADAS_MODEL_PATH,
                        help='Modelo YOLOv8 a usar')
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
    print(f"🧠 Modelo: {args.model} | imgsz={args.imgsz} | conf={args.conf}")
    print("=" * 50)

    detector = None
    stream_manager = None
    visualizer = None

    try:
        detector = AsyncADASDetector(model_path=args.model, imgsz=args.imgsz, conf=args.conf)
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