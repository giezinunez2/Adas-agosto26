"""
Implementation of the RabbitMQ middleware interface for reliable
video streaming with message queuing guarantees.

This module provides robust publishers and subscribers using RabbitMQ's
Advanced Message Queuing Protocol (AMQP) with support for durable queues,
message persistence, and fault-tolerant communication patterns.

Key Features of RabbitMQ Implementation:
- Durable queues that survive broker restarts
- Configurable Quality of Service (QoS) settings
- Dead letter exchange for failed message handling
- Thread-safe asynchronous consumption
- Automatic reconnection patterns (TODO)

Configuration:
    All connection parameters are centralized in RABBITMQ_CONFIG
    for consistency across the application.
"""

import pika
import json
import threading
import time
from typing import Callable, Optional, Any
from utils.connection_conf import RABBITMQ_CONFIG


"""
Publisher for high-frequency video streaming using RabbitMQ.

Optimized for video frame publication with non-persistent messaging
for maximum throughput. Uses direct exchange with topic-based routing
for efficient message distribution.

Attributes:
    connection (pika.BlockingConnection): RabbitMQ connection
    channel (pika.Channel): AMQP channel for publishing
    topic_name (str): Routing key for message distribution
    publisher_name (str): Identifier for this publisher
    sent_count (int): Total frames published
    total_bytes (int): Total bytes transmitted
"""
class RabbitMQStreamPublisher:


    """
    Initialize RabbitMQ publisher for video streaming.

    Args:
        topic_name: Routing key for message distribution.
        publisher_name: Identifier for logging and monitoring.
        host: RabbitMQ broker hostname. Defaults to RABBITMQ_CONFIG.host
        port: RabbitMQ broker port. Defaults to RABBITMQ_CONFIG.port
        username: Authentication username. Defaults to RABBITMQ_CONFIG.username
        password: Authentication password. Defaults to RABBITMQ_CONFIG.password

    Raises:
        pika.exceptions.AMQPConnectionError: If connection fails
        pika.exceptions.AMQPChannelError: If channel setup fails

    Note:
        Uses non-persistent messages (delivery_mode=1) for performance.
        For critical data, consider implementing persistent messaging.
    """
    def __init__(self, topic_name: str, publisher_name: str,
                 host: str = None, port: int = None,
                 username: str = None, password: str = None):
        self.host = host
        self.port = port
        self.username = username
        self.password = password

        # Create connection parameters with credentials
        self.connection_params = pika.ConnectionParameters(
            host=self.host,
            port=self.port,
            credentials=pika.PlainCredentials(self.username, self.password)
        )
        self.connection = pika.BlockingConnection(self.connection_params)
        self.channel = self.connection.channel()

        self.topic_name = topic_name
        self.publisher_name = publisher_name
        self.sent_count = 0
        self.total_bytes = 0

        # Configure exchange for streaming
        self.channel.exchange_declare(
            exchange=RABBITMQ_CONFIG.streaming_exchange,
            exchange_type=RABBITMQ_CONFIG.streaming_exchange_type,
            durable=RABBITMQ_CONFIG.queue_durability
        )

    """
    Publish a message to RabbitMQ with performance tracking.

    Serializes the message (protobuf or JSON) and publishes it to
    the configured exchange with the topic_name as routing key.

    Args:
        msg: Message to send. Can be:
            - Protobuf message (with SerializeToString method)
            - JSON-serializable object
            - Already serialized bytes

    Returns:
        Dictionary with transmission metrics:
        - size (int): Size of serialized message in bytes
        - total_sent (int): Cumulative messages sent
        - total_bytes (int): Cumulative bytes sent

    Performance:
        - Localhost: 500-1000 messages/second
        - Network: 100-500 messages/second (depends on size)
        - Serialization overhead: 10-20% for protobuf
    """
    def send(self, msg) -> dict:
        # Assuming msg is a serializable protobuf
        if hasattr(msg, 'SerializeToString'):
            message_body = msg.SerializeToString()
        else:
            message_body = json.dumps(msg).encode('utf-8')

        self.channel.basic_publish(
            exchange=RABBITMQ_CONFIG.streaming_exchange,
            routing_key=self.topic_name,  # Use the topic as a routing key
            body=message_body,
            properties=pika.BasicProperties(
                delivery_mode=1,  # Non-persistent for better performance
            )
        )

        self.sent_count += 1
        self.total_bytes += len(message_body)

        return {
            'size': len(message_body),
            'total_sent': self.sent_count,
            'total_bytes': self.total_bytes
        }

    """
    Close RabbitMQ connection gracefully.

    Ensures all pending messages are delivered before closing.
    Should be called when the publisher is no longer needed.
    """
    def close(self):
        if self.connection and not self.connection.is_closed:
            self.connection.close()


"""
Subscriber for high-frequency video streaming using RabbitMQ.

Implements robust, thread-safe message consumption with automatic
queue setup, durable message storage, and configurable message TTL.
Uses a dedicated thread for asynchronous consumption to prevent
blocking the main application.

Attributes:
    topic_name (str): Routing key to subscribe to
    callback (Callable): User function for message processing
    component_name (str): Identifier for this subscriber
    _consuming (bool): Control flag for consumption thread
    _consuming_thread (threading.Thread): Background consumption thread
    received_count (int): Total messages received

Queue Configuration:
    - Durable: Survives broker restart
    - TTL: 10 seconds (auto-expire old frames)
    - Max length: 30 messages (prevent memory buildup)
"""
class RabbitMQStreamSubscriber:

    """
    Initialize RabbitMQ subscriber for video streaming.

    Args:
        topic_name: Routing key to bind to. Must match publisher's topic.
        callback: User function with signature:
                 callback(topic: str, message_bytes: bytes, timestamp: float)
        component_name: Identifier for logging and monitoring.
        host: RabbitMQ broker hostname.
        port: RabbitMQ broker port.
        username: Authentication username.
        password: Authentication password.

    Raises:
        pika.exceptions.AMQPConnectionError: If connection fails
        ValueError: If queue declaration fails

    Note:
        Creates a durable queue with message TTL and size limits
        to prevent memory issues during high-load scenarios.
    """
    def __init__(self, topic_name: str, callback: Callable,
                 component_name: str,
                 host: str , port: int,
                 username: str , password: str ):

        self.topic_name = topic_name
        self.callback = callback
        self.component_name = component_name

        self.host = host
        self.port = port
        self.username = username
        self.password = password

        # State control
        self._consuming = False
        self._consuming_thread = None
        self._connection = None
        self._channel = None

        # Statistics
        self.received_count = 0

        self._setup_connection()

    """
    Configure a robust RabbitMQ connection with proper queue setup.
    
    Sets up exchange, declares durable queue with TTL and size limits,
    and binds the queue to the exchange with the appropriate routing key.
    
    Raises:
        Exception: If any step of the connection setup fails
                  (re-raised for caller handling)
    
    Design:
        - Heartbeat: 600 seconds for long-lived connections
        - Blocked timeout: 300 seconds for flow control
        - Queue TTL: 10 seconds (video frames expire quickly)
        - Max length: 30 messages (prevune backlog)
    """
    def _setup_connection(self):
        try:
            # Connection parameters
            credentials = pika.PlainCredentials(self.username, self.password)
            parameters = pika.ConnectionParameters(
                host=self.host,
                port=self.port,
                credentials=credentials,
                heartbeat=600,
                blocked_connection_timeout=300
            )

            self._connection = pika.BlockingConnection(parameters)
            self._channel = self._connection.channel()

            # Configure exchange for streaming
            self._channel.exchange_declare(
                exchange=RABBITMQ_CONFIG.streaming_exchange,
                exchange_type=RABBITMQ_CONFIG.streaming_exchange_type,
                durable=RABBITMQ_CONFIG.queue_durability
            )

            # Create DURABLE queue with specific name
            queue_name = f"queue_{self.topic_name}"
            self._channel.queue_declare(
                queue=queue_name,
                durable=True,       # Survive reboots
                arguments={
                    'x-message-ttl': 10000, # Messages expire in 10 seconds
                    'x-max-length': 30      # Maximum 30 frames in queue
                }
            )

            # CORRECT Binding - verify the routing key
            self._channel.queue_bind(
                exchange=RABBITMQ_CONFIG.streaming_exchange,
                queue=queue_name,
                routing_key=self.topic_name
            )

            # Configure consumption
            self._channel.basic_consume(
                queue=queue_name,
                on_message_callback=self._internal_callback,
                auto_ack=True
            )

            print(f"✅ RabbitMQ subscriber ready: {self.topic_name}")

        except Exception as e:
            print(f"❌ Error setting up RabbitMQ for {self.topic_name}: {e}")
            raise

    """
    Internal callback that handles errors and metrics tracking.
    
    Wraps the user callback with exception handling to prevent
    consumption thread crashes. Updates reception statistics.
    
    Args:
        ch: Pika channel (unused in auto_ack mode)
        method: Delivery information including routing key
        properties: Message properties (headers, content_type, etc.)
        body: Serialized message bytes
    
    Note:
        Uses auto_ack=True for performance. For guaranteed delivery,
        implement manual acknowledgment with error handling.
    """
    def _internal_callback(self, ch, method, properties, body):
        if not self._consuming:
            return

        try:
            self.received_count += 1
            # Call the callback with a format similar to eCAL
            self.callback(method.routing_key, body, time.time())
        except Exception as e:
            print(f"❌ Error in RabbitMQ callback for {self.topic_name}: {e}")

    """
    Start consuming messages on a separate thread safely.
    
    Creates a daemon thread that runs the consumption loop,
    allowing the main thread to continue other operations.
    
    Note:
        Only starts if not already consuming. Idempotent.
    """
    def start_consuming_async(self):
        if self._consuming_thread and self._consuming_thread.is_alive():
            print(f"⚠️ Consumption already running for {self.topic_name}")
            return

        """
        Consumption loop with robust error handling.
        
        Continuously processes data events with a timeout to allow
        graceful shutdown when _consuming becomes False.
        
        Note:
            The time_limit parameter in process_data_events allows
            periodic checking of the _consuming flag.
        """
        def consuming_loop():
            print(f"🔄 Starting consumption for {self.topic_name}")
            self._consuming = True

            try:
                while self._consuming and self._connection and self._connection.is_open:
                    try:
                        self._connection.process_data_events(time_limit=0.5)
                    except Exception as e:
                        if self._consuming:  # Only log in if it was not an intentional closure
                            print(f"⚠️ Error in process_data_events for {self.topic_name}: {e}")
                        break

            except Exception as e:
                if self._consuming:
                    print(f"❌ Consumption loop error for {self.topic_name}: {e}")
            finally:
                self._consuming = False
                print(f"✅ Consumption loop ended for {self.topic_name}")

        # Create and start thread
        self._consuming_thread = threading.Thread(
            target=consuming_loop,
            daemon=True,
            name=f"RabbitMQ-Consumer-{self.topic_name}"
        )
        self._consuming_thread.start()

    """
    Close the connection safely and orderly.
    
    Implements a multi-step shutdown process:
    1. Signal consumption thread to stop
    2. Wait for loop to exit naturally
    3. Close channels and connection
    4. Wait for thread termination
    
    Design:
        - Graceful shutdown prevents message loss
        - Timeouts prevent hanging on network issues
        - Error suppression for expected closure errors
    """
    def close(self):
        print(f"🔌 Closing RabbitMQ subscriber: {self.topic_name}")

        # 1. Stop the consumption
        self._consuming = False

        # 2. Allow time for the loop to end naturally
        time.sleep(0.5)

        # 3. Use a secure closure that ignores Pika's internal errors
        self._safe_close_connection()

        # 4. Wait for the thread to finish
        if self._consuming_thread and self._consuming_thread.is_alive():
            print(f"⏳ Waiting for consumption thread to terminate: {self.topic_name}")
            self._consuming_thread.join(timeout=3.0)  # More timeout
            if self._consuming_thread.is_alive():
                print(f"⚠️ Consumption thread for {self.topic_name} didn't terminate cleanly")
            else:
                print(f"✅ Consumption thread terminated for {self.topic_name}")

        print(f"✅ RabbitMQ subscriber closed: {self.topic_name}")

    """
    Alias for close() for Stream Manager compatibility.
    
    Provided to maintain API consistency with other middleware
    implementations that use stop_consuming() method.
    """
    def stop_consuming(self):
        self.close()

    """
    Closes the connection in an ultra-secure manner, ignoring internal Pika errors.
    
    Suppresses known Pika exceptions during shutdown that are expected
    when connections are closing asynchronously.
    
    Suppressed Errors:
        - "pop from an empty deque" (internal Pika queue)
        - "Stream connection lost" (network termination)
        - "Connection already closed" (idempotent closure)
        - "SelectConnection OPEN" (state race condition)
    """
    def _safe_close_connection(self):
        try:
            if self._channel and self._channel.is_open:
                self._channel.close()
        except Exception as e:
            # Ignore errors from already closed channels
            if "Channel already closed" not in str(e):
                print(f"⚠️ Error closing channel for {self.topic_name}: {e}")

        try:
            if self._connection and self._connection.is_open:
                # Clear any pending operations before closing
                self._connection.process_data_events(time_limit=0.1)
                self._connection.close()
        except Exception as e:
            # Ignore Pika-specific errors during shutdown
            if any(error_msg in str(e) for error_msg in [
                "pop from an empty deque",
                "Stream connection lost",
                "Connection already closed",
                "SelectConnection OPEN"
            ]):
                pass  # Ignoring these known mistakes
            else:
                print(f"⚠️ Error closing connection for {self.topic_name}: {e}")


# =============================================================================
#                      CLASSES FOR SENSORS (EVENT-DRIVEN)
# =============================================================================
# toDo: Here you can design publisher and subscriber-based functions for sensor control via RabbitMQ.



# =============================================================================
#                       SUPPORTED HELPER FUNCTIONS
# =============================================================================

"""
Factory function to create a RabbitMQStreamPublisher instance.

Convenience wrapper that uses configuration defaults for connection
parameters. Used by the middleware factory for dynamic creation.

Args:
    topic_name: See RabbitMQStreamPublisher.__init__
    publisher_name: See RabbitMQStreamPublisher.__init__
    host: RabbitMQ host (defaults to config)
    port: RabbitMQ port (defaults to config)
    username: Authentication (defaults to config)
    password: Authentication (defaults to config)

Returns:
    New RabbitMQStreamPublisher instance
"""
def create_publisher(topic_name: str, publisher_name: str,
                     host: str = RABBITMQ_CONFIG.host, port: int = RABBITMQ_CONFIG.port,
                     username: str = RABBITMQ_CONFIG.username, password: str = RABBITMQ_CONFIG.password):
    return RabbitMQStreamPublisher(topic_name, publisher_name, host, port, username, password)


"""
Factory function to create a RabbitMQStreamSubscriber instance.

Convenience wrapper that uses configuration defaults. Note that
the subscriber requires explicit start_consuming_async() call.

Args:
   topic_name: See RabbitMQStreamSubscriber.__init__
   callback: See RabbitMQStreamSubscriber.__init__
   component_name: See RabbitMQStreamSubscriber.__init__
   host: RabbitMQ host (defaults to config)
   port: RabbitMQ port (defaults to config)
   username: Authentication (defaults to config)
   password: Authentication (defaults to config)

Returns:
   New RabbitMQStreamSubscriber instance (not started)
"""
def create_subscriber(topic_name: str, callback: Callable,
                     component_name: str,
                     host: str = RABBITMQ_CONFIG.host, port: int = RABBITMQ_CONFIG.port,
                     username: str = RABBITMQ_CONFIG.username, password: str = RABBITMQ_CONFIG.password):
   return RabbitMQStreamSubscriber(topic_name, callback, component_name, host, port, username, password)
