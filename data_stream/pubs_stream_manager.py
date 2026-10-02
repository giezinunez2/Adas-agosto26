import time
from utils import pub_sub_conf
from img_processing.video_serializer import frame_to_proto

"""
Video Stream Publishing Manager with Performance Monitoring

Responsibilities:
- Monitor FPS, data size, and latency in real time
- Maintain a history of recent measurements
- Calculate averaged statistics
- Serialize frames to protobuf format
- Manage message publishing

Args:
    window_size: Window size for mobile calculations
"""
class StreamManagerPub:

    """
    Initializes the performance monitor.

    Args:
        window_size: Number of measurements to keep in history
    """
    def __init__(self, window_size = pub_sub_conf.WINDOW_SIZE):
        self.window_size = window_size
        self.fps_history = []
        self.size_history = []
        self.latency_history = []
        self.start_time = time.time()
        self.last_print_time = time.time()
        self.total_frames_sent = 0
        self.total_bytes_sent = 0
        self.frame_number = 0

    """ 
    Processes an entire frame: serializes, publishes and calculates metrics. 

    Args: 
        frame: Video frame to process 
        encoding: Encoding method ('JPEG', 'LZ4', etc.) 
        quality: Compression quality (0-100) 
        keyframe_interval: Keyframe interval 
        publisher: Publisher instance (eCAL/RabbitMQ) 

    Returns: 
        Dict with processing results: 
        - 'metrics': Publication metrics 
        - 'fps': current FPS 
        - 'latency': Processing latency 
        - 'message': Protobuf message sent 
        - 'frame_number': Frame number 
        - 'success': Success status 

    Raises: 
        Exception: If there is an error in serialization or publication 
    """
    def process_frame(self, frame, encoding, quality, keyframe_interval, publisher):
        frame_start_time = time.time()

        try:
            # Convert to protobuf
            msg = frame_to_proto(frame, self.frame_number, keyframe_interval, encoding, quality)

            # Send message and get metrics
            metrics = publisher.send(msg)

            # Calculate current FPS
            current_time = time.time()
            total_time = current_time - self.start_time
            fps = self.frame_number / total_time if self.frame_number > 0 else 0
            processing_latency = current_time - frame_start_time

            # Update metrics
            self.update(fps, metrics['size'], processing_latency)
            self.frame_number += 1

            return {
                'metrics': metrics,
                'fps': fps,
                'latency': processing_latency,
                'message': msg,
                'frame_number': self.frame_number,
                'success': True
            }

        except Exception as e:
            print(f"❌ Error processing frame: {e}")
            return {
                'success': False,
                'error': str(e)
            }

    """ 
    Update metrics with new data. 

    Args: 
        fps: Current frames per second 
        size: Frame size in bytes 
        latency: Processing latency in seconds 

    Note: 
        Maintains sliding window size window_size 
        removing the oldest measurements when exceeded. 
    """
    def update(self, fps: float, size: int, latency: float):
        self.fps_history.append(fps)
        self.size_history.append(size)
        self.latency_history.append(latency)
        self.total_frames_sent += 1
        self.total_bytes_sent += size

        # Deletes the oldest measurements
        if len(self.fps_history) > self.window_size:
            self.fps_history.pop(0)
            self.size_history.pop(0)
            self.latency_history.pop(0)

    """ 
    Calculates and returns consolidated statistics. 

    Returns: 
        Dict with current window statistics: 
        - 'avg_fps': Average windowed FPS 
        - 'avg_size': Average size in bytes 
        - 'avg_latency': Average latency in seconds 
        - 'total_frames': Frames in window 
        - 'total_time': Total time since start 
        - 'total_mb': Megabytes in window 
        - 'total_frames_sent': Total frames sent 
        - 'total_bytes_sent': Total bytes sent 
        - 'total_mb_sent': Total megabytes sent 
    """
    def get_stats(self) -> dict:
        current_time = time.time()
        total_time = current_time - self.start_time

        # Dictionary with average and total statistics
        return {
            'avg_fps': sum(self.fps_history) / len(self.fps_history) if self.fps_history else 0,
            'avg_size': sum(self.size_history) / len(self.size_history) if self.size_history else 0,
            'avg_latency': sum(self.latency_history) / len(self.latency_history) if self.latency_history else 0,
            'total_frames': len(self.fps_history),
            'total_time': total_time,
            'total_mb': sum(self.size_history) / 1024 / 1024,
            'total_frames_sent': self.total_frames_sent,
            'total_bytes_sent': self.total_bytes_sent,
            'total_mb_sent': self.total_bytes_sent / (1024 * 1024)
        }


    """ 
    Returns final statistics covering the entire runtime of the stream. 

    Returns: 
        Dict with full statistics: 
        - 'total_runtime': Total execution time 
        - 'total_frames_sent': Total frames sent 
        - 'total_bytes_sent': Total bytes sent 
        - 'total_mb_sent': Total MB sent 
        - 'average_fps': total average FPS 
        - 'average_mbps': average MB/s 
        - 'window_avg_fps': Average FPS in window 
        - 'window_avg_latency': Average window latency 
        - 'window_avg_size': Average window size 
        - 'window_size_used': Window size used 
    """
    def get_final_statistics(self) -> dict:
        current_time = time.time()
        total_runtime = current_time - self.start_time

        total_mb = self.total_bytes_sent / (1024 * 1024)
        avg_mbps = total_mb / total_runtime if total_runtime > 0 else 0
        avg_fps_total = self.total_frames_sent / total_runtime if total_runtime > 0 else 0

        return {
            'total_runtime': total_runtime,
            'total_frames_sent': self.total_frames_sent,
            'total_bytes_sent': self.total_bytes_sent,
            'total_mb_sent': total_mb,
            'average_fps': avg_fps_total,
            'average_mbps': avg_mbps,
            'window_avg_fps': sum(self.fps_history) / len(self.fps_history) if self.fps_history else 0,
            'window_avg_latency': sum(self.latency_history) / len(self.latency_history) if self.latency_history else 0,
            'window_avg_size': sum(self.size_history) / len(self.size_history) if self.size_history else 0,
            'window_size_used': len(self.fps_history)
        }

    """ 
    Controls the frequency of printing statistics. 

    Args: 
        interval: Minimum interval between prints (seconds) 

    Returns: 
        bool: True if it should be printed, False otherwise 
    """
    def should_print_stats(self, interval=pub_sub_conf.PRINT_INTERVALS) -> bool:
        current_time = time.time()
        if current_time - self.last_print_time >= interval:
            self.last_print_time = current_time
            return True
        return False

    """Prints a formatted summary of the final statistics to the console"""

    def print_final_statistics(self):
        stats = self.get_final_statistics()

        print("°" * 35)
        print("📊 PUBLISHER FINAL STATISTICS")
        print("°" * 35)
        print(f"   Total Time: {stats['total_runtime']:.1f}s")
        print(f"   Total Frames Sent: {stats['total_frames_sent']}")
        print(f"   Total Data: {stats['total_mb_sent']:.2f} MB")
        print(f"   Average Rate: {stats['average_mbps']:.2f} MB/s")
        print(f"   Average FPS: {stats['average_fps']:.1f}")
        print(f"   Average Latency: {stats['window_avg_latency'] * 1000:.1f}ms\n")