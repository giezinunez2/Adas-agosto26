import cv2
import platform

def get_optimal_backend() -> int:
    """
    Obtiene el backend óptimo de OpenCV según el sistema operativo.

    Esta función detecta automáticamente el sistema operativo y retorna
    la constante de OpenCV correspondiente al backend más eficiente para
    ese sistema, optimizando el tiempo de inicialización de la cámara.

    Returns:
        int: Constante de OpenCV para el backend recomendado.

    Notes:
        - Windows: cv2.CAP_DSHOW (DirectShow)
        - Linux: cv2.CAP_V4L2 (Video4Linux2)
        - macOS: cv2.CAP_AVFOUNDATION
        - Otros: cv2.CAP_ANY (backend por defecto)

    Example:
        >>> backend = get_optimal_backend()
        >>> cap = cv2.VideoCapture(0, backend)
    """
    system = platform.system()

    backends = {
        "Windows": cv2.CAP_DSHOW,  # DirectShow para Windows
        "Linux": cv2.CAP_V4L2,  # Video4Linux2 para Linux
        "Darwin": cv2.CAP_AVFOUNDATION  # AVFoundation para macOS
    }

    return backends.get(system, cv2.CAP_ANY)


def validate_camera(camera_index: int = 0, timeout_ms: int = 2000) -> bool:
    """
    Valida si una cámara está disponible y funcionando correctamente.

    Intenta abrir el dispositivo de video, leer un frame de prueba y
    liberar los recursos. Incluye un timeout para evitar bloqueos.

    Args:
        camera_index (int): Índice de la cámara a validar (0 por defecto).
        timeout_ms (int): Tiempo máximo de espera en milisegundos (opcional).

    Returns:
        bool: True si la cámara está disponible y funciona, False en caso contrario.

    Raises:
        ValueError: Si camera_index es negativo.
    """
    if camera_index < 0:
        raise ValueError("El índice de cámara no puede ser negativo")

    backend = get_optimal_backend()
    cap = cv2.VideoCapture(camera_index, backend)

    if not cap.isOpened():
        return False

    # Configurar timeout para lectura
    cap.set(cv2.CAP_PROP_POS_MSEC, timeout_ms)

    try:
        ret, frame = cap.read()
        # Verificar que se leyó un frame válido (no vacío)
        return ret and frame is not None and frame.size > 0
    except Exception:
        return False
    finally:
        # Asegurar liberación de recursos
        cap.release()