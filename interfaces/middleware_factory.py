"""
Middleware Factory
==================

Factory module to dynamically create publishers and subscribers
for multiple middlewares and communication patterns.

This module provides a unified interface for creating communication
components (publishers/subscribers) across different middleware
implementations (eCAL, RabbitMQ). It abstracts the underlying
communication layer, allowing the rest of the application to remain
middleware-agnostic.

The factory supports two communication patterns:
1. **Streaming (High Frequency)**: For video/continuous data
2. **Event-Driven (Low Frequency)**: For sensor/control messages (TODO)

Configuration:
    The active middleware is determined by `MIDDLEWARE_TYPE`, defined in
    `utils/connection_conf.py` (MiddlewareConfig.type).

Protobuf schema:
    The single source of truth is `protos/imagen_pb2.py`. This module loads it
    BEFORE importing any middleware interface and aliases the legacy path
    `core.imagen_pb2` to the very same module object, so the descriptor
    'imagen.proto' is registered exactly once in the protobuf descriptor pool
    (no "duplicate file name imagen.proto" error).
"""

import sys

from utils.connection_conf import MIDDLEWARE_TYPE

# =============================================================================
#      WINDOWS: ecal_windows.ini -> ECAL_DATA (antes de que se importe eCAL)
# =============================================================================
# En Linux/macOS no se ejecuta nada: la configuración de los nodos Raspberry Pi
# (ecal.ini montado en /app/ecal.ini y /etc/ecal/ecal.ini) queda intacta.
if sys.platform == "win32":
    from utils.ecal_conf import prepare_ecal_config

    prepare_ecal_config()


# =============================================================================
#                       PROTOBUF: ESQUEMA UNICO (protos/imagen_pb2.py)
# =============================================================================
def _unify_protobuf_schema():
    """
    Garantiza que 'imagen.proto' se registre UNA sola vez en el descriptor pool.

    1. Importa protos.imagen_pb2 (esquema canónico) antes que cualquier interfaz.
    2. Si `core.imagen_pb2` aún no fue importado, lo registra en sys.modules como alias
       del módulo canónico. Así, un `from core import imagen_pb2` posterior (por ejemplo
       en interfaces/ecal_interface.py) reutiliza las mismas clases y NUNCA ejecuta un
       core/imagen_pb2.py generado con protoc (que registraría un descriptor duplicado).
    3. Si `core.imagen_pb2` ya estaba cargado, verifica que exponga las MISMAS clases.

    Returns:
        module: protos.imagen_pb2

    Raises:
        ImportError: si protos/imagen_pb2.py no existe o no se puede importar.
        RuntimeError: si 'imagen.proto' ya fue registrado por otro módulo (duplicado real).
    """
    try:
        from protos import imagen_pb2 as canonical
    except ImportError as e:
        raise ImportError(
            "No se pudo importar protos.imagen_pb2. Verifica que exista protos/imagen_pb2.py "
            "(regenerar: protoc --proto_path=protos --python_out=protos protos/imagen.proto)"
        ) from e
    except TypeError as e:   # "duplicate file name imagen.proto" / símbolo duplicado
        raise RuntimeError(
            "Descriptor 'imagen.proto' duplicado: otro módulo (¿un core/imagen_pb2.py generado "
            "con protoc?) lo registró antes. Reemplaza core/imagen_pb2.py por el puente "
            "'from protos.imagen_pb2 import *' y asegúrate de importar interfaces.middleware_factory primero."
        ) from e

    legacy = sys.modules.get("core.imagen_pb2")
    if legacy is None:
        sys.modules["core.imagen_pb2"] = canonical
    elif getattr(legacy, "VideoFrame", None) is not canonical.VideoFrame:
        raise RuntimeError(
            "core.imagen_pb2 ya cargó un esquema distinto al de protos.imagen_pb2 "
            "(descriptor 'imagen.proto' duplicado). Reemplaza core/imagen_pb2.py por el puente: "
            "'from protos.imagen_pb2 import *'"
        )
    return canonical


imagen_pb2 = _unify_protobuf_schema()

# =============================================================================
#                       STREAMING (CAMERAS) - HIGH FREQUENCY
# =============================================================================

"""
    Factory for creating streaming publishers (cameras, video sources).
    
    Creates a publisher instance for the configured middleware type.
    Designed for high-frequency streaming data like video frames.
    
    Args:
        topic_name: Name of the topic/channel to publish to.
        publisher_name: Identifier for this publisher component.
    
    Returns:
        Publisher instance specific to the configured middleware.
        The returned object will have a `send()` method.
    
    Raises:
        ValueError: If `MIDDLEWARE_TYPE` is not "ecal" or "rabbitmq"
        ImportError: If the required middleware module cannot be imported
        ConnectionError: If the middleware cannot be initialized
    
    Notes:
        - The publisher manages its own connection lifecycle
        - Use context manager (`with` statement) for automatic cleanup
        - Thread safety depends on the underlying middleware implementation
    """
def create_publisher(topic_name: str, publisher_name: str):
    middleware_type = MIDDLEWARE_TYPE

    if middleware_type == "ecal":
        from interfaces.ecal_interface import create_publisher as ecal_create_publisher
        return ecal_create_publisher(topic_name, publisher_name)

    elif middleware_type == "rabbitmq":
        from interfaces.rabbitmq_interface import create_publisher as rabbit_create_publisher
        return rabbit_create_publisher(topic_name, publisher_name)

    else:
        raise ValueError(f"❌ Unknown middleware type: {middleware_type}")


"""
    Factory for creating streaming subscribers (cameras, video consumers).
    
    Creates a subscriber instance for the configured middleware type.
    Designed for high-frequency streaming data consumption.
    
    Args:
        topic_name: Name of the topic/channel to subscribe to.
        callback: Callback function that processes incoming messages.
                  Signature: `callback(topic_name: str, msg: Any, timestamp: float)`
                  The `msg` type depends on the middleware and serialization.
        component_name: Identifier for this subscriber component.
    
    Returns:
        Subscriber instance specific to the configured middleware.
        The returned object will manage message consumption automatically.
        
    Raises:
        ValueError: If `MIDDLEWARE_TYPE` is not "ecal" or "rabbitmq"
        ImportError: If the required middleware module cannot be imported
        ConnectionError: If the middleware cannot connect to the topic
    
    Notes:
        - The subscriber starts consuming messages immediately upon creation
        - The callback runs in the middleware's thread/event loop
        - Implement error handling in your callback to prevent crashes
"""
def create_subscriber(topic_name: str, callback, component_name: str):
    middleware_type = MIDDLEWARE_TYPE

    if middleware_type == "ecal":
        from interfaces.ecal_interface import create_subscriber as ecal_create_subscriber
        return ecal_create_subscriber(topic_name, callback, component_name)

    elif middleware_type == "rabbitmq":
        from interfaces.rabbitmq_interface import create_subscriber as rabbit_create_subscriber
        return rabbit_create_subscriber(topic_name, callback, component_name)

    else:
        raise ValueError(f"❌ Unknown middleware type: {middleware_type}")

# toDo: Here you can design publisher and subscriber-based functions for sensor control via RabbitMQ.
# =============================================================================
#                       EVENT-DRIVEN (SENSORS) - LOW FREQUENCY
# =============================================================================
