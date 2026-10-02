import time
import queue
import threading
from typing import Dict, Callable, Any

from interfaces.middleware_factory import create_subscriber
from img_processing.video_serializer import proto_to_frame
from utils.connection_conf import MIDDLEWARE_TYPE
from utils.pub_sub_conf import SUBSCRIBER_NAME
from core import imagen_pb2

class StreamManagerSub:
    """
    Manager of multiple video streams received by middleware.
    Responsible for:
    - Connecting to and receiving multiple video streams
    - Managing frame queues for each stream
    - Calculating real-time performance metrics
    - Providing data to visualizers (such as GridVisualizer)

    Note: Only handles data and connections, NOT visualization.

    Args:
    topic_prefix: Prefix for topic names (e.g., 'camera')
    num_streams: Number of streams to manage
    """
    def __init__(self, topic_prefix: str, num_streams: int):
        # Streams Configuration
        self.topic_prefix = topic_prefix
        self.num_streams = num_streams

        # Data structures for stream management
        self._streams_data: Dict[str, Dict] = {}
        self._frame_queues: Dict[str, queue.Queue] = {}
        self._subscribers = []
        self._consumption_threads = []

        # Status control
        self._running = True
        self._lock = threading.Lock()  # For thread-safety

        # Global metrics
        self._diagnostics = {
            'start_time': time.time(),
            'total_messages': 0,
            'total_bytes_received': 0,
            'active_streams': 0,
            'last_diagnostic_time': time.time(),
            'diagnostic_interval': 5.0,
            'total_lost_frames': 0,
            'total_expected_frames': 0
        }

        # Initialize structures
        self._initialize_streams()

    """Initialize per-stream data structures and frame queues"""
    def _initialize_streams(self) -> None:
        for i in range(self.num_streams):
            topic_name = f"{self.topic_prefix}_{i+1}"
            self._streams_data[topic_name] = {
                # Connection status
                'active': False,
                'subscriber_created': False,
                'first_message_time': None,
                'last_received': 0,
                'session_start_time': time.time(),

                # Performance metrics
                'received_count': 0,
                'bytes_received': 0,
                'last_frame_number': -1,
                'lost_frames': 0,
                'fps': 0.0,
                'latency': 0.0,
                'timestamps': [],
                'last_fps_update': time.time(),

                # Video information
                'encoding': '',
                'quality': 0,
                'original_width': 0,
                'original_height': 0,
                'aspect_ratio': 0.0,

                # Update control
                'has_new_frame': False,

                # FPS control
                'fps_method': 'unknown',    # Method used
                'frame_tracking': {         # Real frame tracking
                    'last_frame_count': 0,
                    'last_check_time': time.time(),
                    'frames_since_last_check': 0
                },
                'arrival_timestamps': [],   # Arrival timestamps
                'theoretical_fps': 0.0,     # The publisher's theoretical FPS
                'fps_accuracy': 'high',     # Calculation accuracy
                'loss_percentage': 0.0,
                'recent_lost_frames': 0
            }

            self._frame_queues[topic_name] = queue.Queue(maxsize=2)

    """
    Configure subscribers for all streams.

    Create connections with the middleware (eCAL/RabbitMQ) and
    start consuming messages in the background.

    Raises:
        Exception: If there is an error creating any subscriber
    """
    def setup_subscribers(self) -> None:
        print("🎯 CONFIGURING STREAM SUBSCRIBERS")
        print("=" * 50)

        for i in range(self.num_streams):
            topic_name = f"{self.topic_prefix}_{i+1}"
            subscriber_name = f"{SUBSCRIBER_NAME}"
            print(subscriber_name)
            try:
                # Create specific callback for this stream
                callback = self._create_stream_callback(topic_name)

                # Create subscriber
                sub = create_subscriber(topic_name, callback, subscriber_name)

                self._subscribers.append(sub)
                self._streams_data[topic_name]['subscriber_created'] = True

                # Start background consumption (RabbitMQ)
                if hasattr(sub, 'start_consuming_async'):
                    sub.start_consuming_async()
                    self._consumption_threads.append(sub)
                    print(f"✅ Subscribed & consuming: {topic_name} [ASYNC]")
                else:
                    print(f"✅ Subscribed: {topic_name} [eCAL]")

            except Exception as e:
                print(f"❌ Error subscribing to {topic_name}: {e}")
                self._streams_data[topic_name]['subscriber_created'] = False

    """
    Creates a specific callback to process messages from a stream.

    Args:
        topic_name: Name of the topic for this stream

    Returns:
        Callback function that processes received messages
    """
    def _create_stream_callback(self, topic_name: str) -> Callable:
        def callback(topic_name_received: str, msg: Any, timestamp: float) -> None:
            if not self._running:
                return

            try:
                current_time = time.time()

                processed_msg = None
                frame_data = None

                if MIDDLEWARE_TYPE == "rabbitmq" and isinstance(msg, bytes):
                    # RabbitMQ: Comes as bytes, we need to deserialize
                    video_frame = imagen_pb2.VideoFrame()
                    video_frame.ParseFromString(msg)
                    processed_msg = video_frame

                elif MIDDLEWARE_TYPE == "ecal":
                    # eCAL: Now comes as a deserialized protobuf object
                    processed_msg = msg

                else:
                    print(f"Unknown middleware or unexpected message type")
                    return

                # DESERIALIZE THE FRAME ONLY ONCE (for both middlewares)
                frame_data = proto_to_frame(processed_msg)

                with self._lock:  # Thread-safe
                    stream_data = self._streams_data[topic_name]

                    # New publisher detection
                    if stream_data['first_message_time'] is None:
                        self._handle_first_message(stream_data, processed_msg, current_time)
                        print(f"🎉 First message from {topic_name}")

                    # Message processing
                    self._process_incoming_message(stream_data, processed_msg, current_time)

                    # Prepare frame data for the queue
                    frame_data = self._prepare_frame_data(topic_name, processed_msg, frame_data, current_time)

                    # Queue management (most recent frame only)
                    self._update_frame_queue(topic_name, frame_data)

            except Exception as e:
                print(f"❌ Error in callback for {topic_name}: {e}")

        return callback

    """
    Processes the first message received from a stream.

    Args:
        stream_data: Dictionary with data from the stream
        msg: Received protobuf message
        current_time: Current timestamp
    """
    @staticmethod
    def _handle_first_message(stream_data: Dict, msg: Any, current_time: float) -> None:
        stream_data['first_message_time'] = current_time
        stream_data['session_start_time'] = current_time
        stream_data['original_width'] = msg.width
        stream_data['original_height'] = msg.height
        stream_data['aspect_ratio'] = msg.width / msg.height

    """Processes an incoming message, update metrics, detect lost frames, calculate latency and FPS
    
    Args:
        stream_data: Dictionary with stream data
        msg: Received protobuf message
        current_time: Current timestamp
    """
    def _process_incoming_message(self, stream_data: Dict, msg: Any, current_time: float) -> None:
        # Update basic status
        stream_data['active'] = True
        stream_data['received_count'] += 1
        stream_data['last_received'] = current_time
        stream_data['has_new_frame'] = True
        stream_data['encoding'] = msg.encoding
        stream_data['quality'] = msg.compression_quality

        # Calculate message size and update bandwidth metrics
        try:
            msg_size = len(msg.frame_data)
            stream_data['bytes_received'] += msg_size
            self._diagnostics['total_bytes_received'] += msg_size
        except (AttributeError, TypeError):
            msg_size = 0

        self._diagnostics['total_messages'] += 1

        # Calculate latency
        current_time = time.time()
        latency_ms = max(0, current_time - msg.timestamp)
        stream_data['latency'] = latency_ms

        # Detect lost frames and update frame tracking
        current_frame = msg.frame_number
        last_frame = stream_data['last_frame_number']

        lost = 0
        if last_frame != -1 and current_frame > last_frame + 1:
            lost = current_frame - last_frame - 1
            stream_data['recent_lost_frames'] += lost
            self._diagnostics['total_lost_frames'] += lost

        stream_data['last_frame_number'] = current_frame

        # Track frames for FPS calculation
        self._update_frame_tracking(stream_data, current_frame, current_time)

        # FPS and lost
        if current_time - stream_data['last_fps_update'] >= 2.0:
            self._update_fps_calculation(stream_data, current_time)

        self._diagnostics['total_expected_frames'] += 1 + lost

    """Track frames for accurate FPS calculation"""
    @staticmethod
    def _update_frame_tracking(stream_data: Dict, frame_number: int, current_time: float) -> None:
        if 'frame_tracking' not in stream_data:
            stream_data['frame_tracking'] = {
                'last_frame_count': 0,
                'last_check_time': current_time,
                'frames_since_last_check': 0
            }

        tracking = stream_data['frame_tracking']
        tracking['frames_since_last_check'] += 1

    """Calculate rolling FPS based on recent timestamps"""
    @staticmethod
    def _update_fps_calculation(stream_data: Dict, current_time: float) -> None:
        time_elapsed = current_time - stream_data['last_fps_update']

        if time_elapsed > 0.8:
            if time_elapsed > 0:
                current_count = stream_data['received_count']
                last_count = stream_data.get('last_frame_count', current_count)
                frames_received = current_count - last_count

                recent_lost = stream_data.get('recent_lost_frames', 0)
                total_frames_in_period = frames_received + recent_lost

                if total_frames_in_period > 0:
                    stream_data['loss_percentage'] = (recent_lost / total_frames_in_period) * 100
                else:
                    stream_data['loss_percentage'] = 0.0

                # FPS calculation
                stream_data['fps'] = frames_received / time_elapsed

                # Reset counters
                stream_data['last_frame_count'] = current_count
                stream_data['recent_lost_frames'] = 0
                stream_data['last_fps_update'] = current_time

    """Prepares frame data for queuing"""
    def _prepare_frame_data(self, topic_name: str, msg: Any, frame_data: Any, current_time: float) -> Dict:
        return {
            'topic': topic_name,
            'msg': msg,
            'msg_frame': frame_data,
            'latency': self._streams_data[topic_name]['latency'],
            'lost': self._streams_data[topic_name]['loss_percentage'],
            'timestamp': current_time,
            'size': len(msg.frame_data) if hasattr(msg, 'frame_data') else 0
        }

    """Updates the frame queue keeping only the most recent frames"""
    def _update_frame_queue(self, topic_name: str, frame_data: Dict) -> None:
        queue_obj = self._frame_queues[topic_name]
        # Clear queue if it is full
        while queue_obj.qsize() >= 2:
            try:
                queue_obj.get_nowait()
            except queue.Empty:
                break

        # Add new frame
        try:
            queue_obj.put_nowait(frame_data)
        except queue.Full:
            pass

    # PUBLIC API for the GridVisualizer
    """ Gets the most recent frames from all streams """
    def get_latest_frames(self) -> Dict[str, Any]:
        frames_dict = {}
        for i in range(self.num_streams):
            topic_name = f"{self.topic_prefix}_{i+1}"
            frame_data = self._get_latest_frame(topic_name)
            if frame_data:
                frames_dict[topic_name] = frame_data
        return frames_dict

    """Gets the most recent frame from a specific stream"""
    def _get_latest_frame(self, topic_name: str) -> Any:
        queue_obj = self._frame_queues[topic_name]
        try:
            return queue_obj.queue[-1] if not queue_obj.empty() else None
        except IndexError:
            return None

    """Gets metrics for a specific stream
   
    Args:    
        stream_id: Stream ID (0-based)
        
    Returns:
        Dict with all stream metrics
        
    Raises:
        KeyError: If stream_id does not exist
    """
    def get_stream_metrics(self, stream_id: int) -> Dict[str, Any]:
        if not 0 <= stream_id < self.num_streams:
            raise KeyError(f"stream_id {stream_id} fuera de rango (0-{self.num_streams - 1})")
        topic_name = f"{self.topic_prefix}_{stream_id+1}"
        return self._streams_data.get(topic_name, {}).copy()

    """Gets metrics for all streams"""
    def get_all_streams_metrics(self) -> Dict[str, Dict]:
        return {k: v.copy() for k, v in self._streams_data.items()}

    """Check if a stream is active (receiving frames recently)
    
    Args:
        stream_id: Stream ID (0-based)
        
    Returns:
        True if it received frames in the last second
    """
    def is_stream_active(self, stream_id: int) -> bool:
        topic_name = f"{self.topic_prefix}_{stream_id + 1}"
        metrics = self._streams_data.get(topic_name, {})
        if not metrics:
            return False
        time_since_last = time.time() - metrics['last_received']
        return metrics['active'] and time_since_last < 1.0

    """ Check if it's time to print periodic diagnostics in console"""
    def should_print_diagnostics(self) -> bool:
        current_time = time.time()
        if current_time - self._diagnostics['last_diagnostic_time'] >= self._diagnostics['diagnostic_interval']:
            self._diagnostics['last_diagnostic_time'] = current_time
            return True
        return False

    """ Calculate global frame loss percentage across all streams """
    def _calculate_global_loss_percentage(self) -> float:
        total_expected = self._diagnostics['total_expected_frames']
        total_lost = self._diagnostics['total_lost_frames']
        if total_expected > 0:
            return (total_lost / total_expected) * 100
        return 0.0

    """ Get global bandwidth statistics """
    def get_bandwidth_stats(self) -> Dict[str, Any]:
        current_time = time.time()
        total_time = current_time - self._diagnostics['start_time']
        total_bytes = self._diagnostics['total_bytes_received']
        total_mb = total_bytes / (1024 * 1024)
        mb_per_second = total_mb / total_time if total_time > 0 else 0
        return {
            'total_bytes': total_bytes,
            'total_mb': total_mb,
            'mb_per_second': mb_per_second,
            'total_time': total_time,
            'total_messages': self._diagnostics['total_messages']
        }

    """Print per-stream and global diagnostics to console"""
    def print_diagnostics(self) -> None:
        active_streams = sum(1 for i in range(self.num_streams) if self.is_stream_active(i))
        status_line = ""
        for i in range(self.num_streams):
            metrics = self.get_stream_metrics(i)
            is_active = self.is_stream_active(i)

            status = "🔵" if is_active else "🔴"
            fps = f" {metrics['fps']:.1f} " if is_active else "0.0 "
            frame_info = f"Fr:{metrics['last_frame_number']} " if metrics['last_frame_number'] != -1 else "Fr:---"

            # Percentage of loss
            loss_percentage = metrics.get('loss_percentage', 0.0)
            loss_info = f"Loss:{loss_percentage:.1f}%"

            status_line += f"{status}C{i + 1}: {fps}fps {frame_info} {loss_info} |"

        # Global statistics
        bandwidth_stats = self.get_bandwidth_stats()
        global_loss_percentage = self._calculate_global_loss_percentage()

        print(f"📊 {active_streams}/{self.num_streams} active | {status_line.strip()} "
              f" MB: {bandwidth_stats['total_mb']:.1f}({bandwidth_stats['mb_per_second']:.2f}/s) | "
              f"Global Loss: {global_loss_percentage:.1f}%")

    """Print final statistics summary for all streams"""
    def print_final_statistics(self) -> None:
        print("\n" + "° " * 20)
        print("📊 FINAL STATISTICS")
        print("°" * 20)

        current_time = time.time()
        total_runtime = current_time - self._diagnostics['start_time']
        bandwidth_stats = self.get_bandwidth_stats()
        loss_percentage = self._calculate_global_loss_percentage()

        print("\n📷 PER-STREAM:")
        print("-" * 35)
        for i in range(self.num_streams):
            metrics = self.get_stream_metrics(i)
            stream_mb = metrics.get('bytes_received', 0) / (1024 * 1024)
            session_time = current_time - metrics.get('session_start_time', current_time)
            stream_mbps = stream_mb / session_time if session_time > 0 else 0

            print(f"Cam{i + 1}: Frames: {metrics.get('received_count', 0)} | "
                  f"Data: {stream_mb:.2f} MB | Rate: {stream_mbps:.2f} MB/s")

        print("\n🌐 GLOBAL:")
        print("-" * 35)
        print(f"Total Runtime: {total_runtime:.1f}s")
        print(f"Total Frames: {self._diagnostics['total_messages']}")
        print(f"Total Data: {bandwidth_stats['total_mb']:.2f} MB")
        print(f"Avg Rate: {bandwidth_stats['mb_per_second']:.2f} MB/s")
        print(f"Lost Frames: {self._diagnostics['total_lost_frames']}")
        print(f"Loss %: {loss_percentage:.1f}%")

        if total_runtime > 0:
            avg_fps = self._diagnostics['total_messages'] / total_runtime
            print(f"Avg FPS: {avg_fps:.1f}")

        print("=" * 35)

    """Return current diagnostic data as a dictionary"""
    def get_diagnostics(self) -> Dict[str, Any]:
        diagnostics = self._diagnostics.copy()
        diagnostics.update(self.get_bandwidth_stats())
        return diagnostics

    """Stop the manager and close all subscribers
       Ensures clean release of resources and threads."""
    def stop(self) -> None:
        self._running = False
        time.sleep(0.5)  # Time for callbacks to finish

        for sub in self._subscribers:
            try:
                if hasattr(sub, 'close'):
                    sub.close()
                elif hasattr(sub, 'stop_consuming'):
                    sub.stop_consuming()
            except Exception as e:
                if "Stream connection lost" not in str(e):
                    print(f"⚠️ Error closing subscriber: {e}")
