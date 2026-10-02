import cv2
import time
import numpy as np
import platform
from typing import Dict, Any
from user_interface.text_labels_overlay import draw_overlay

"""
Grid viewer for multiple video streams.

Responsible for:
- Creating a grid with multiple video streams
- Displaying frames in real time with overlaid metrics
- Handling placeholders for inactive streams
- Optimizing performance using a caching system
- Providing an interactive user interface

Note: Only handles visualization, NOT data management.

Args:
    stream_manager: Instance of StreamManagerSub to obtain data
    cell_width: Width of each cell in pixels
    cell_height: Height of each cell in pixels
    grid_cols: Number of columns in the grid
    padding: Space between cells in pixels
"""

class GridVisualizer:
    #Initialize visualization parameters and dependencies
    def __init__(self,
                 stream_manager,
                 cell_width: int,
                 cell_height: int,
                 grid_cols: int,
                 padding: int):

        # StreamManager Dependency
        self.stream_manager = stream_manager
        self.num_streams = stream_manager.num_streams

        # Visual configuration of the grid
        self.cell_width = cell_width
        self.cell_height = cell_height
        self.grid_cols = grid_cols
        self.grid_rows = (self.num_streams + grid_cols - 1) // grid_cols
        self.padding = padding

        # Display status
        self._running = True

        # Cache system for optimization
        self._grid_cache = None
        self._last_update_time = 0
        self._cache_valid = True
        self._cache_duration = 0.5

        # Connection type settings
        self.connection_types = {
            f"{stream_manager.topic_prefix}_{i}": "Unknown" for i in range(self.num_streams)
        }
        self.default_connection_config = {
            f"{stream_manager.topic_prefix}_0": "WiFi",
            f"{stream_manager.topic_prefix}_1": "WiFi",
            f"{stream_manager.topic_prefix}_2": "WiFi",
            f"{stream_manager.topic_prefix}_3": "WiFi"
        }

    """
    Dynamically determines the connection type for a stream.

    Args:
        stream_id: Stream ID (0-based)

    Returns:
        str: Connection type ("WiFi", "Ethernet", "Unknown")
    """
    def _get_connection_type(self, stream_id: int) -> str:
        topic_name = f"{self.stream_manager.topic_prefix}_{stream_id+1}"
        stream_metrics = self.stream_manager.get_stream_metrics(stream_id)
        # If the camera is not active, display “Unknown”
        if not stream_metrics or not self.stream_manager.is_stream_active(stream_id):
            return "Unknown"
        # If the camera is active, use the default settings
        return self.default_connection_config.get(topic_name, "Unknown")

    """
    Generates a placeholder frame for inactive cameras.

    Args:
        stream_id: Stream ID (0-based)
        status: Status to display in the placeholder

    Returns:
        np.ndarray: Placeholder frame with visual information

    Note:
        Colors vary depending on the status:
        - WAITING: Dark blue
        - ACTIVE: Dark green
        - Other: Gray
    """
    def create_placeholder(self, stream_id: int, status: str = "DISCONNECTED") -> np.ndarray:
        display_stream_id = stream_id + 1
        frame = np.zeros((self.cell_height, self.cell_width, 3), dtype=np.uint8)

        if "WAITING" in status:
            bg_color = (40, 40, 80)  # Dark blue
            text_color = (200, 200, 255)  # Light blue
        elif "ACTIVE" in status:
            bg_color = (40, 80, 40)  # Dark green
            text_color = (200, 255, 200)  # Light green
        else:
            bg_color = (60, 60, 60)  # Gray
            text_color = (200, 200, 200)  # Light gray

        frame[:] = bg_color

        font = cv2.FONT_HERSHEY_SIMPLEX

        # Principal text
        main_text = f"Camera {display_stream_id}"
        status_text = status

        main_text_size = cv2.getTextSize(main_text, font, 1.2, 3)[0]
        main_x = (self.cell_width - main_text_size[0]) // 2
        main_y = (self.cell_height - main_text_size[1]) // 2 - 30

        cv2.putText(frame, main_text, (main_x, main_y), font, 1.2, text_color, 3)

        # State text
        status_text_size = cv2.getTextSize(status_text, font, 0.9, 2)[0]
        status_x = (self.cell_width - status_text_size[0]) // 2
        status_y = main_y + 50

        cv2.putText(frame, status_text, (status_x, status_y), font, 0.9, text_color, 2)

        return frame

    """
    Resizes a frame while maintaining its aspect ratio.

    Args:
        frame: Input frame (numpy array)
        target_width: Target width
        target_height: Target height

    Returns:
        np.ndarray: Resized frame with padding if necessary

    Note:
        Adds black bars (letterbox) to maintain the aspect ratio
    """
    @staticmethod
    def resize_keep_aspect_ratio(frame: np.ndarray, target_width: int, target_height: int) -> np.ndarray:
        h, w = frame.shape[:2]

        # Check if is the correct size
        if w == target_width and h == target_height:
            return frame

        # Calculate aspect ratios
        aspect_original = w / h
        aspect_target = target_width / target_height

        # Decide on resizing strategy
        if aspect_original > aspect_target:
            new_width = target_width
            new_height = int(target_width / aspect_original)
        else:
            new_height = target_height
            new_width = int(target_height * aspect_original)

        # Resize and center
        resized_frame = cv2.resize(frame, (new_width, new_height), interpolation=cv2.INTER_LINEAR)
        canvas = np.zeros((target_height, target_width, 3), dtype=np.uint8)

        y_offset = (target_height - new_height) // 2
        x_offset = (target_width - new_width) // 2
        canvas[y_offset:y_offset + new_height, x_offset:x_offset + new_width] = resized_frame

        return canvas

    """
    Compose the complete grid with all frames.

    Args:
        frames_dict: Dictionary with frames from each stream

    Returns:
        np.ndarray: Image of the complete grid

    Note:
        Uses a caching system to optimize performance
        when there are no new frames.
    """
    def create_grid(self, frames_dict: Dict[str, Any]) -> np.ndarray:
        current_time = time.time()
        # Check cache system
        if self._should_use_cache(current_time):
            return self._grid_cache

        # Calculate grid dimensions
        grid_width = self.grid_cols * self.cell_width + (self.grid_cols - 1) * self.padding
        grid_height = self.grid_rows * self.cell_height + (self.grid_rows - 1) * self.padding

        grid = np.zeros((grid_height, grid_width, 3), dtype=np.uint8)
        frames_updated = 0

        # Process each stream for the grid
        for i in range(self.num_streams):
            frame = self._process_stream_for_grid(i, frames_dict)
            if frame is not None:
                self._place_frame_in_grid(grid, i, frame)
                frames_updated += 1

        # Update cache if necessary
        self._update_cache_if_needed(grid, current_time, frames_updated)

        return grid

    """
    Determines whether to use the cached grid.

    Args:
        current_time: Current timestamp

    Returns:
        bool: True if cached, False otherwise
    """
    def _should_use_cache(self, current_time: float) -> bool:
        streams_metrics = self.stream_manager.get_all_streams_metrics()
        has_new_frames = any(metrics.get('has_new_frame', False) for metrics in streams_metrics.values())

        return (not has_new_frames and self._cache_valid and
                self._grid_cache is not None and
                current_time - self._last_update_time < self._cache_duration)

    """
    Processes an individual stream for inclusion in the grid.

    Args:
        stream_id: Stream ID (0-based)
        frames_dict: Dictionary of received frames

    Returns:
        Optional[np.ndarray]: Processed frame or None if there is an error
    """
    def _process_stream_for_grid(self, stream_id: int, frames_dict: Dict[str, Any]) -> np.ndarray:
        topic_name = f"{self.stream_manager.topic_prefix}_{stream_id+1}"
        stream_metrics = self.stream_manager.get_stream_metrics(stream_id)
        is_active = self.stream_manager.is_stream_active(stream_id)

        if not is_active:
            return self._create_placeholder_frame(stream_id, stream_metrics, is_active)
        if topic_name in frames_dict and frames_dict[topic_name] is not None:
            return self._process_live_frame(frames_dict[topic_name], stream_metrics)
        latest_frame = stream_metrics.get('last_display_frame')
        if latest_frame is not None:
            return latest_frame

        # fallback
        return self._create_placeholder_frame(stream_id, stream_metrics, is_active)

    """
    Processes a live frame received from the stream.

    Args:
        frame_data: Data from the received frame
        stream_metrics: Stream metrics

    Returns:
        np.ndarray: Processed frame with metrics overlay

    Raises:
        Exception: If there is an error during processing
    """
    def _process_live_frame(self, frame_data: Dict, stream_metrics: Dict) -> np.ndarray:
        try:
            msg = frame_data['msg']
            frame = frame_data['msg_frame']

            if frame is not None:
                loss_percentage = stream_metrics.get('loss_percentage', 0.0)
                latency = min(stream_metrics.get('latency', 0.0), 1.0)

                display_frame = draw_overlay(
                    frame, msg,
                    fps=min(stream_metrics['fps'], 60),
                    latency=min(stream_metrics['latency'], 1000),
                    lost=stream_metrics.get('loss_percentage', 0.0)
                )

                # Save to avoid flickering
                stream_metrics['last_display_frame'] = self._resize_frame_for_display(display_frame)
                return stream_metrics['last_display_frame']
            else:
                return self.create_placeholder(0, "DECODE_ERROR")
        except Exception as e:
            print(f"❌ Error processing live frame: {e}")
            return self.create_placeholder(0, "PROCESS_ERROR")

    """
    Creates a placeholder frame for inactive streams.

    Args:
        stream_id: Stream ID
        stream_metrics: Stream metrics
        is_active: Activity status

    Returns:
        np.ndarray: Placeholder frame
    """
    def _create_placeholder_frame(self, stream_id: int, stream_metrics: Dict, is_active: bool) -> np.ndarray:
        time_since_last = (time.time() - stream_metrics['last_received']
                           if stream_metrics['last_received'] > 0
                           else time.time() - stream_metrics['session_start_time'])

        status = "ACTIVE" if is_active else f"INACTIVE ({time_since_last:.1f}s)"
        return self.create_placeholder(stream_id, status)

    """
    Resizes the frame to display on the grid.

    Args:
        frame: Input frame

    Returns:
        np.ndarray: Resized frame
    """
    def _resize_frame_for_display(self, frame: np.ndarray) -> np.ndarray:
        if frame.shape[:2] != (self.cell_height, self.cell_width):
            return self.resize_keep_aspect_ratio(frame, self.cell_width, self.cell_height)
        return frame

    """
    Places a frame in its corresponding position on the grid.

    Args:
        grid: Grid matrix
        stream_id: Stream ID
        frame: Frame to place
    """
    def _place_frame_in_grid(self, grid: np.ndarray, stream_id: int, frame: np.ndarray) -> None:
        try:
            # Calculate position on the grid
            row, col, y_start, y_end, x_start, x_end = self._calculate_grid_position(stream_id)

            # Place frame on the grid
            grid[y_start:y_end, x_start:x_end] = frame

            # Draw state border
            self._draw_status_border(grid, stream_id, x_start, y_start, x_end, y_end)

            # Draw camera and connection information
            self._draw_camera_info(grid, stream_id, x_start, y_start, x_end, y_end)

        except Exception as e:
            print(f"❌ Error placing frame in grid for stream {stream_id}: {e}")

    """
    Calculates the position of a stream on the grid.

    Args:
        stream_id: Stream ID

    Returns:
        Tuple: (row, column, start_y, end_y, start_x, end_x)
    """
    def _calculate_grid_position(self, stream_id: int) -> tuple:
        row = stream_id // self.grid_cols
        col = stream_id % self.grid_cols

        y_start = row * (self.cell_height + self.padding)
        y_end = y_start + self.cell_height
        x_start = col * (self.cell_width + self.padding)
        x_end = x_start + self.cell_width

        return row, col, y_start, y_end, x_start, x_end

    """
    Draws the state border around the frame.

    Args:
        grid: Grid array
        stream_id: Stream ID
        x_start: Start X coordinate
        y_start: Start Y coordinate
        x_end: End X coordinate
        y_end: End Y coordinate

    Note:
        Green for active streams, red for inactive streams
    """
    def _draw_status_border(self, grid: np.ndarray, stream_id: int,
                            x_start: int, y_start: int, x_end: int, y_end: int) -> None:
        is_active = self.stream_manager.is_stream_active(stream_id)

        border_color = (0, 255, 0) if is_active else (0, 0, 255)  # Green or Red
        border_thickness = 4 if is_active else 2

        cv2.rectangle(grid, (x_start, y_start), (x_end - 1, y_end - 1),
                      border_color, border_thickness)

    """
    Draws camera and connection information in each cell.

    Args:
        grid: Grid array
        stream_id: Stream ID
        x_start: Start X coordinate
        y_start: Start Y coordinate
        x_end: End X coordinate
        y_end: End Y coordinate
    """
    def _draw_camera_info(self, grid: np.ndarray, stream_id: int,
                          x_start: int, y_start: int, x_end: int, y_end: int) -> None:
        topic_name = f"{self.stream_manager.topic_prefix}_{stream_id}"
        display_cam_id = stream_id + 1
        connection_type = self._get_connection_type(stream_id)

        # Principal text
        cam_text = f"Cam {display_cam_id}"
        text_size = cv2.getTextSize(cam_text, cv2.FONT_HERSHEY_SIMPLEX, 1.0, 3)[0]
        text_x = x_start + 15
        text_y = y_start + text_size[1] + 20

        # Background for camera text
        cv2.rectangle(grid,
                      (text_x - 5, text_y - text_size[1] - 5),
                      (text_x + text_size[0] + 5, text_y + 5),
                      (0, 0, 0), -1)

        cv2.putText(grid, cam_text, (text_x, text_y),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 3)

        # Connection type legend
        connection_text = f"({connection_type})"

        if connection_type == 'WiFi':
            connection_color = (100, 200, 255)  # Light blue
        elif connection_type == 'Ethernet':
            connection_color = (100, 255, 100)  # Green
        else:  # Unknown
            connection_color = (200, 200, 200)  # Gray

        conn_text_size = cv2.getTextSize(connection_text, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)[0]
        conn_text_x = x_start + 15
        conn_text_y = text_y + conn_text_size[1] + 15

        # Background for connecting text
        cv2.rectangle(grid,
                      (conn_text_x - 5, conn_text_y - conn_text_size[1] - 5),
                      (conn_text_x + conn_text_size[0] + 5, conn_text_y + 5),
                      (0, 0, 0), -1)

        cv2.putText(grid, connection_text, (conn_text_x, conn_text_y),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, connection_color, 2)

    """
    Updates the cached grid if new frames were rendered.

    Args:
        grid: Current grid
        current_time: Current timestamp
        frames_updated: Number of frames updated
    """
    def _update_cache_if_needed(self, grid: np.ndarray, current_time: float, frames_updated: int) -> None:
        if frames_updated > 0 or not self._cache_valid:
            self._grid_cache = grid.copy()
            self._last_update_time = current_time
            self._cache_valid = True

    """
    Main display loop, controls the UI cycle.

    Args:
        target_fps: Target FPS for display

    Note:
        Controls:
        - 'q': Exit
        - 'd': Show final statistics
    """
    def run_visualization(self, target_fps: int = 20) -> None:
        print("🚀 STARTING GRID VISUALIZATION...")

        # Window settings
        window_name = "Multi-Camera Monitor - ADAS System"
        self._setup_display_window(window_name)

        # FPS Control
        frame_time = 1.0 / target_fps
        last_frame_time = time.time()
        last_diagnostic_time = time.time()

        # Main display loop
        while self._running:
            current_time = time.time()
            if not self._should_process_frame(current_time, last_frame_time, frame_time):
                continue
            last_frame_time = current_time

            # Get frames and display grid
            self._render_frame(window_name)

            # Diagnostics and controls
            self._handle_ui_operations(current_time, last_diagnostic_time)

        self._cleanup_display()

    """
    Configure the display window.

    Args:
        window_name: Window name

    Note:
        Special configuration for Linux (Qt)
    """
    def _setup_display_window(self, window_name: str) -> None:
        if platform.system() == "Linux":
            import os
            os.environ['QT_QPA_PLATFORM'] = 'xcb'

        cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)

        # Initial window size
        grid_width = self.grid_cols * self.cell_width + (self.grid_cols - 1) * self.padding
        grid_height = self.grid_rows * self.cell_height + (self.grid_rows - 1) * self.padding
        cv2.resizeWindow(window_name, grid_width + 50, grid_height + 100)

    """
    Determines whether to render a new frame based on FPS.

    Args:
        current_time: Current timestamp
        last_frame_time: Timestamp of the last frame
        frame_time: Time per frame (1/FPS)

    Returns:
        bool: True if the frame should be processed
    """
    @staticmethod
    def _should_process_frame(current_time: float, last_frame_time: float, frame_time: float) -> bool:
        elapsed = current_time - last_frame_time
        if elapsed < frame_time:
            time.sleep(max(0, frame_time - elapsed - 0.001))
            return False
        return True

    """
    Renders a frame in the window.

    Args:
        window_name: Window name
    """
    def _render_frame(self, window_name: str) -> None:
        # Getting frames from Stream Manager
        frames_dict = self.stream_manager.get_latest_frames()

        # Create and display grid
        grid = self.create_grid(frames_dict)
        cv2.imshow(window_name, grid)

    """
    Handles UI operations (diagnostics and controls).

    Args:
        current_time: Current timestamp
        last_diagnostic_time: Timestamp of the last diagnosis
    """
    def _handle_ui_operations(self, current_time: float, last_diagnostic_time: float) -> None:
        # Periodic diagnostics
        if self.stream_manager.should_print_diagnostics():
            self.stream_manager.print_diagnostics()

        # Key handling
        key = cv2.waitKey(1) & 0xFF
        if key == ord('q'):
            self._running = False
        elif key == ord('d'):
            self.stream_manager.print_final_statistics()

    """Clean display resources"""
    @staticmethod
    def _cleanup_display() -> None:
        cv2.destroyAllWindows()

    """Stops the display"""
    def stop(self) -> None:
        self._running = False