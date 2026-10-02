import cv2
from utils.helper_functions import get_optimal_backend
from user_interface.text_labels_overlay import draw_overlay
from img_processing.yolo_processor import YOLOProcessor

"""
Camera manager for real-time video capture and display.

This class provides a unified interface for:
    - Camera initialization and configuration
    - Frame capture with error handling
    - Optional YOLO processing
    - Display in preview windows
    - Resource management (context manager)

Args:
    camera_index: Camera index (0 for main camera)
    width: Desired resolution width
    height: Desired resolution height
    fps: Target frames per second
    enable_yolo: Enable YOLO processing
    yolo_mode: YOLO mode ('detection' or 'segmentation')
    yolo_interval: Process every N frames with YOLO
    yolo_conf: Minimum confidence for YOLO detections (0.0-1.0)

Attributes:
    cap: OpenCV VideoCapture object
    preview_windows: List of active preview windows
    _windows_closed: Flag for closed status windows
    yolo_processor: Instance of YOLOProcessor if enabled
"""
class CameraManager:
    """Initialize the basic camera parameters"""
    def __init__(self, camera_index: int, width: int, height: int, fps: int, enable_yolo: bool,
                 yolo_mode: str, yolo_interval: int, yolo_conf: float):

        # Parameter validation
        if camera_index < 0:
            raise ValueError(f"camera_index cannot be negative: {camera_index}")
        if width <= 0 or height <= 0:
            raise ValueError(f"Invalid dimensions:{width}x{height}")
        if fps <= 0:
            raise ValueError(f"FPS must be positive: {fps}")
        if yolo_mode not in ['detection', 'segmentation']:
            raise ValueError(f"yolo_mode invalid: {yolo_mode}. Use 'detection' o 'segmentation'")
        if not 0.0 <= yolo_conf <= 1.0:
            raise ValueError(f"yolo_conf must be between 0.0 and 1.0: {yolo_conf}")
        if yolo_interval < 1:
            raise ValueError(f"yolo_interval must be >= 1: {yolo_interval}")

        self.camera_index = camera_index
        self.width = width
        self.height = height
        self.fps = fps
        self.cap = None
        self.preview_windows = []
        self._windows_closed = False

        # Initialize YOLO if enabled
        self.enable_yolo = enable_yolo
        self.enable_yolo = enable_yolo
        if enable_yolo:
            self.yolo_processor = YOLOProcessor(
                mode=yolo_mode,
                processing_interval=yolo_interval,
                confidence=yolo_conf
            )
        else:
            self.yolo_processor = None

    """
    Initializes the camera upon entering the context.
    
    Returns:
        CameraManager: Ready instance
    
    Raises:
        RuntimeError: If the camera cannot be opened
        ValueError: If the properties cannot be configured
    
    Note:
        Use get_optimal_backend() to select the most efficient backend
        based on the operating system
    """
    def __enter__(self):
        backend = get_optimal_backend()
        self.cap = cv2.VideoCapture(self.camera_index, backend)

        if not self.cap.isOpened():
            raise RuntimeError(f"Error: Could not open camera {self.camera_index}")

        # Configure camera properties
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        self.cap.set(cv2.CAP_PROP_FPS, self.fps)
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        return self

    """
    Captures a single frame from the camera.
    
    Returns:
        cv2.Mat: Captured frame 
    
    Raises:
        RuntimeError: If the frame fails to be read
        
    Note:
    - Applies YOLO processing if enabled
    - Handles automatic rotation if necessary
    - Maintains standard OpenCV BGR format
    """
    def read_frame(self):
        ret, frame = self.cap.read()
        if not ret:
            raise RuntimeError("Error reading frame from camera")
        # Process with YOLO if enabled
        if self.enable_yolo and self.yolo_processor:
            frame = self.yolo_processor.process_frame(frame)

        return frame

    """
    Creates a preview window with specific dimensions.
    
    Args:
        window_name: Unique name of the window
        width: Width of the window in pixels
        height: Height of the window in pixels
    
    Note:
        - Uses WINDOW_NORMAL to allow resizing
        - Stores window names for automatic cleanup
    """
    def create_preview_window(self, window_name: str, width: int, height: int):
        cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(window_name, width, height)
        self.preview_windows.append(window_name)

    """
    Prepare a preview frame with a metrics overlay.
    
    Args:
        frame: High-resolution original frame
        msg: Protobuf message with metadata
        fps: Current frames per second
        latency: Latency in seconds
        lost: Percentage of lost frames
        size_mb: Frame size in MB
        target_width: Target width for preview
        target_height: Target height for preview
        role: 'publisher' or 'subscriber'
        camera_label: Camera label for overlay
    
    Returns:
        cv2.Mat: Resized frame with overlay applied
    
    Note:
        - Resizes while maintaining aspect ratio
        - Applies overlay with draw_overlay()
        - Maintains visual quality during resizing
    """
    @staticmethod
    def prepare_preview_frame(frame, msg, fps: float, latency: float, lost: int,
                              size_mb: float, target_width: int, target_height: int,
                              role: str, camera_label: str):
        # Resize for preview
        preview_frame = cv2.resize(frame, (target_width, target_height))

        # Apply metrics overlay
        display_frame = draw_overlay(
            preview_frame, msg,
            fps=fps,
            latency=latency,
            lost=lost,
            size_mb=size_mb,
            role=role,
            camera_label=camera_label
        )
        return display_frame

    """
    Displays a frame in the specified preview window.
    
    Args:
        window_name: Name of the window to display
        frame: Frame to display
    
    Note:
    - Verifies that the window is not closed
    - Optimized for real-time updates
    """
    def show_preview(self, window_name: str, frame):
        if not self._windows_closed:
            cv2.imshow(window_name, frame)

    """
    Wait for a key press.
    
    Args:
        delay: Wait time in milliseconds (0 = infinity)
    
    Returns:
        int: ASCII code of the pressed key, or -1 if timeout occurs
    
    Note:
        - Compatible with Windows/Linux/macOS systems
        - Correctly handles special keys
        - Clears OpenCV event buffer
    """
    @staticmethod
    def wait_key(delay: int = 1):
        return cv2.waitKey(delay) & 0xFF

    """
    Closes all preview windows safely.
    
    Performs an orderly cleanup of graphic resources
    and prevents errors caused by multiple closures.
    """
    def close_preview_windows(self):
        if self._windows_closed:
            return

        try:
            for window in self.preview_windows:
                try:
                    cv2.destroyWindow(window)
                except cv2.error:
                    pass

            cv2.destroyAllWindows()
            self._windows_closed = True
        except Exception as e:
            print(f"⚠️ Warning closing windows: {e}")

    """"
    Cleans up resources upon exiting context.
    
    Args:
        exc_type: Exception type (if it occurred)   
        exc_val: Exception value
        exc_tb: Exception traceback
    
    Note:
        - Always releases resources even with errors
        - Cleanup order: windows → YOLO → camera
    """
    def __exit__(self, exc_type, exc_val, exc_tb):
        # Close preview windows
        self.close_preview_windows()
        # Stop YOLO processor if it exists
        if self.yolo_processor:
            self.yolo_processor.stop()
        # Release camera
        if self.cap:
            self.cap.release()