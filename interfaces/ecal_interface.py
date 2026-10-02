"""
Implementation of the eCAL (Enhanced Communication Abstraction Layer)
middleware interface for high-performance video streaming.

This module provides concrete implementations of publishers and subscribers
using the eCAL framework, which is optimized for real-time communication
in distributed systems. eCAL uses a publish-subscribe pattern with
zero-copy shared memory when possible.

Key Features of eCAL:
- Ultra-low latency communication
- Shared memory optimization for local processes
- UDP multicast for network distribution
- Protobuf serialization support
- Automatic service discovery

Note: eCAL requires proper network configuration for multicast communication.
"""

from typing import Callable

import ecal.core.core as ecal_core
from ecal.core.publisher import ProtoPublisher
from ecal.core.subscriber import ProtoSubscriber

from core import imagen_pb2


class EcalPublisher:
    """
    Publisher for streaming video frames using eCAL middleware.

    This class handles the publication of VideoFrame protobuf messages
    to a specific topic using eCAL's optimized communication channels.
    It includes statistics tracking and automatic resource management.

    Attributes:
        pub (ProtoPublisher): Internal eCAL publisher instance
        topic_name (str): Name of the publication topic
        sent_count (int): Total number of frames sent
        total_bytes (int): Total bytes transmitted
        component_name (str): Identifier for this publisher component
        
    Lifecycle:
        1. __init__: Initialize eCAL and create publisher
        2. send(): Publish frames (repeat as needed)
        3. close()/__exit__: Cleanup and show statistics
    """

    def __init__(self, topic_name: str, component_name: str):
        """
        Initialize eCAL publisher for video frame streaming.

        Args:
            topic_name: Name of the eCAL topic to publish to.
            component_name: Identifier for this publisher instance.

        Raises:
            RuntimeError: If eCAL initialization fails
            ValueError: If topic_name is empty or invalid

        Note:
            eCAL uses automatic service discovery. Multiple publishers
            can publish to the same topic (last-writer-wins semantics).
        """
        # Initialize eCAL
        try:
            ecal_core.initialize(component_name)
        except TypeError:
            ecal_core.initialize([], component_name)

        # Create publisher for the specific topic
        self.pub = ProtoPublisher(topic_name, imagen_pb2.VideoFrame)
        self.topic_name = topic_name
        self.sent_count = 0          # Sent frames counter
        self.total_bytes = 0         # Total bytes sent
        self.component_name = component_name

    def send(self, msg) -> dict:
        """
        Send a VideoFrame message and return transmission metrics.
        
        Publishes the message using eCAL's optimized transport (shared memory
        for local subscribers, UDP multicast for remote).
        
        Args:
            msg: VideoFrame protobuf message to send. 
        
        Returns:
            Dictionary containing transmission metrics:
            - size (int): Size of the serialized message in bytes
            - total_sent (int): Cumulative frames sent by this publisher
            - total_bytes (int): Cumulative bytes sent by this publisher
        """
        # Send message over the network
        self.pub.send(msg)

        # Calculate serialized message size
        serialized_data = msg.SerializeToString()

        # Update statistics
        self.sent_count += 1
        self.total_bytes += len(serialized_data)

        # Return metrics for diagnosis
        return {
            'size': len(serialized_data),
            'total_sent': self.sent_count,
            'total_bytes': self.total_bytes
        }

    def __enter__(self):
        """Context manager entry. Returns self for use in with statements."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """
        Context manager exit. Ensures proper cleanup.

        Args:
            exc_type: Exception type if an exception occurred
            exc_val: Exception value if an exception occurred
            exc_tb: Exception traceback if an exception occurred
        """
        self.close()

    def close(self):
        """
        Clean up eCAL resources and print final statistics.
            
        Safely finalizes the eCAL instance if it's still initialized.
        This method is idempotent and can be called multiple times.
            
        Note:
            In distributed systems, always call close() to ensure
            proper deregistration from the eCAL registry.
        """
        if hasattr(ecal_core, 'initialized') and ecal_core.initialized():
            print(
                f"📊 Final statistics - : {self.sent_count} frames sent , {self.total_bytes / 1024 / 1024:.2f} MB")
            ecal_core.finalize()


class EcalSubscriber:
    """
    Subscriber for receiving video frames using eCAL middleware.

    Listens for VideoFrame messages on a specific topic and forwards them
    to a user-provided callback function. Includes error handling and
    statistics tracking.

    Attributes:
        sub (ProtoSubscriber): Internal eCAL subscriber instance
        user_callback (Callable): User function to process received messages
        running (bool): Control flag for message processing
        topic_name (str): Name of the subscription topic
        received_count (int): Total number of frames received
        component_name (str): Identifier for this subscriber component
        
    Threading:
        Callbacks execute in eCAL's internal thread pool. Ensure your
        callback is thread-safe if processing frames concurrently.
    """

    def __init__(self, topic_name: str, callback: Callable, component_name: str = "Video Subscriber"):
        """
        Initialize eCAL subscriber for video frame reception.

        Args:
            topic_name: Name of the eCAL topic to subscribe to.
            callback: User function to call when messages arrive.
            component_name: Identifier for this subscriber instance.

        Note:
            eCAL supports multiple subscribers per topic (fan-out pattern).
            All subscribers receive all messages published to the topic.
        """
        # Initialize eCAL
        try:
            ecal_core.initialize(component_name)
        except TypeError:
            ecal_core.initialize([], component_name)

        # Create subscriber
        self.sub = ProtoSubscriber(topic_name, imagen_pb2.VideoFrame)

        # Configure callbacks
        self.user_callback = callback
        self.running = True
        self.topic_name = topic_name
        self.received_count = 0             # Received frame counter
        self.component_name = component_name
        self.sub.set_callback(self._internal_callback)

    def _internal_callback(self, topic_name, msg, time):
        """
        Internal callback wrapper with error handling and metrics.
            
        This method is registered with eCAL and receives all incoming
        messages. It provides a layer of protection around the user's
        callback function.
            
        Args:
            topic_name: Topic from which the message originated
            msg: Deserialized VideoFrame protobuf message
            time: eCAL reception timestamp (seconds since epoch)
            
        Design:
            - Filters messages if subscriber is stopped (self.running)
            - Tracks received message count
            - Wraps user callback in try-except to prevent crashes
            - Logs errors but continues processing other messages
        """
        if self.running:  # We can add conditions
            try:
                self.received_count += 1       # We can add metrics
                self.user_callback(topic_name, msg, time)   # Secure call
            except Exception as e:
                print(f"❌ Error en callback: {e}")

    def __enter__(self):
        """Context manager entry. Returns self for use in with statements."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit. Ensures proper cleanup."""
        self.close()

    def close(self):
        """
        Stop message reception and clean up eCAL resources.

        Sets the running flag to False to stop callbacks, then
        finalizes eCAL if it's still initialized. Statistics are
        printed to console.
        """
        self.running = False
        if hasattr(ecal_core, 'initialized') and ecal_core.initialized():
            print(f"📊 Final statistics - : {self.received_count} frames received")
            ecal_core.finalize()


# Auxiliary functions
def create_publisher(topic_name: str, component_name: str) -> EcalPublisher:
    """
    Factory function to create an EcalPublisher instance.

    Convenience wrapper for the EcalPublisher constructor. Used by the
    middleware factory to create eCAL publishers dynamically.

    Args:
        topic_name: See EcalPublisher.__init__
        component_name: See EcalPublisher.__init__

    Returns:
        New EcalPublisher instance
    """
    return EcalPublisher(topic_name, component_name)


def create_subscriber(topic_name: str, callback: Callable,
                      component_name: str = "Video Subscriber") -> EcalSubscriber:
    """
    Factory function to create an EcalSubscriber instance.

    Convenience wrapper for the EcalSubscriber constructor. Used by the
    middleware factory to create eCAL subscribers dynamically.

    Args:
        topic_name: See EcalSubscriber.__init__
        callback: See EcalSubscriber.__init__
        component_name: See EcalSubscriber.__init__

    Returns:
        New EcalSubscriber instance
    """
    return EcalSubscriber(topic_name, callback, component_name)