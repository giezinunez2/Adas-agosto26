import os
import platform

import cv2


def is_windows() -> bool:
    """
    Indica si el proceso se ejecuta sobre Windows.

    Returns:
        bool: True en Windows (comprobando os.name y platform.system()).
    """
    return os.name == "nt" or platform.system() == "Windows"


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


def camera_backends() -> tuple:
    """
    Backends de OpenCV a probar, en orden, para abrir una cámara.

    En Windows se intentan dos rutas nativas: CAP_DSHOW (DirectShow, la más
    compatible con webcams USB) y, si no abre, CAP_MSMF (Media Foundation).
    Ninguna de las dos entiende rutas POSIX como /dev/video0: la cámara se
    identifica por índice numérico (0, 1, 2...).

    Returns:
        tuple: Constantes de OpenCV ordenadas por preferencia.
    """
    system = platform.system()
    if system == "Windows":
        return (cv2.CAP_DSHOW, cv2.CAP_MSMF)
    if system == "Linux":
        return (cv2.CAP_V4L2,)
    if system == "Darwin":
        return (cv2.CAP_AVFOUNDATION,)
    return (cv2.CAP_ANY,)


def normalize_camera_source(camera):
    """
    Normaliza la fuente de cámara para cv2.VideoCapture.

    Args:
        camera: Índice (int), cadena numérica ("0", "1") o ruta admitida en Linux/macOS.

    Returns:
        int: Índice numérico de la cámara.
        str: Ruta o URL en sistemas POSIX (no se usa en Windows).

    Raises:
        ValueError: Si el índice es negativo o, en Windows, si se pasa algo que no es
            un índice numérico (por ejemplo /dev/video0).
    """
    if isinstance(camera, int):
        if camera < 0:
            raise ValueError(f"El índice de cámara no puede ser negativo: {camera}")
        return camera

    text = str(camera).strip()
    if text.lstrip("+-").isdigit():
        return normalize_camera_source(int(text))

    if is_windows():
        raise ValueError(
            f"'{text}' no es un índice de cámara válido en Windows. "
            f"Usa --camera 0, --camera 1, --camera 2... "
            f"(las rutas POSIX tipo /dev/video0 no existen en Windows)"
        )
    return text


def open_video_capture(camera, width: int | None = None, height: int | None = None,
                       fps: int | None = None):
    """
    Abre una cámara con el backend adecuado para el sistema operativo.

    Args:
        camera: Índice numérico (recomendado) o ruta compatible en Linux/macOS.
        width: Ancho opcional a configurar tras abrir.
        height: Alto opcional a configurar tras abrir.
        fps: FPS opcional a configurar tras abrir.

    Returns:
        cv2.VideoCapture abierto, o None si ningún backend pudo abrir la cámara.

    Notes:
        - Windows: CAP_DSHOW y, si falla, CAP_MSMF.
        - Linux: CAP_V4L2. macOS: CAP_AVFOUNDATION.
        - La captura fallida se libera antes de probar el siguiente backend.
    """
    source = normalize_camera_source(camera)

    for backend in camera_backends():
        cap = cv2.VideoCapture(source, backend)
        if not cap.isOpened():
            cap.release()
            continue
        if width:
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        if height:
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        if fps:
            cap.set(cv2.CAP_PROP_FPS, fps)
        return cap
    return None


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

    Notes:
        En Windows la cámara se abre por índice con CAP_DSHOW / CAP_MSMF
        (nunca con rutas POSIX tipo /dev/video0).
    """
    if camera_index < 0:
        raise ValueError("El índice de cámara no puede ser negativo")

    try:
        cap = open_video_capture(camera_index)
    except ValueError:
        return False

    if cap is None:
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
