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
    The active middleware is determined by the `MIDDLEWARE_TYPE`
    environment variable or configuration setting.
"""

from utils.connection_conf import MIDDLEWARE_TYPE

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
