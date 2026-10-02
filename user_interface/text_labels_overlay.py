import cv2
import time
import numpy as np

# --- Encoding mapping and colors ---
ENCODING_MAP = {0: 'RAW', 1: 'JPEG', 2: 'LZ4', 3: 'PNG'}
ENCODING_COLORS = {
    0: (255, 100, 100),  # RAW - Red
    1: (100, 255, 100),  # JPEG - Green
    2: (100, 200, 255),  # LZ4 - Blue
    3: (255, 100, 255)   # PNG - Magenta
}

# --- Metrics configuration per role ---
METRICS_CONFIG = {
    'publisher': ['fps', 'latency', 'size_mb', 'encoding', 'quality'],
    'subscriber': ['fps', 'latency', 'lost', 'encoding', 'quality']
}

# --- Global state for streams ---
STREAM_STATE = {}

"""
Returns the human-readable name of the encoding type.

Args:
    encoding_type: Numeric encoding type (0-3)

Returns:
    str: Name of the encoding ('RAW', 'JPEG', 'LZ4', 'PNG', 'UNK')
"""
def get_encoding_name(encoding_type: int) -> str:
    return ENCODING_MAP.get(encoding_type, 'UNK')

"""
Returns the color associated with the encoding type.

Args:
    encoding_type: Numeric encoding type (0-3)

Returns:
    Tuple[int, int, int]: BGR color for display
"""
def get_encoding_color(encoding_type: int) -> tuple:
    return ENCODING_COLORS.get(encoding_type, (128, 128, 128))

"""
Updates real-time metrics for a specific stream.

Calculates FPS, latency, lost frames, and size in a 2-second sliding window. 
Maintains global state by camera_id.

Args:
    camera_id: Unique identifier of the camera/stream
    msg: Protobuf message with frame metadata

Returns:
    Tuple[float, float, float, float]:
    (fps, latency, lost_percentage, size_mb)

Metrics Calculated:
    - FPS: Frames per second in a 2-second window
    - Latency: Delay between timestamp and current time
    - Lost: Percentage of lost frames in the window
    - Size: Frame size in MB

Note:
    Uses a 2-second sliding window for smooth calculations
"""
def update_stream_metrics(camera_id: str, msg):
    current_time = time.time()
    state = STREAM_STATE.setdefault(camera_id, {
        'last_timestamp': current_time,
        'frame_count': 0,
        'lost_count': 0,
        'last_frame_number': -1,
        'fps': 0,
        'latency': 0,
        'lost': 0,
        'frames_in_window': 0,
        'window_start_time': current_time,
        'lost_in_window': 0
    })

    # FPS every 2 seconds
    if current_time - state['window_start_time'] >= 2.0:
        time_elapsed = current_time - state['window_start_time']
        state['fps'] = state['frames_in_window'] / time_elapsed if time_elapsed > 0 else 0

        # Calculate percentage of window loss
        total_expected_in_window = state['frames_in_window'] + state['lost_in_window']
        state['lost'] = (state[
                             'lost_in_window'] / total_expected_in_window) * 100 if total_expected_in_window > 0 else 0

        # Reset window counters
        state['frames_in_window'] = 0
        state['lost_in_window'] = 0
        state['window_start_time'] = current_time

    # Increment frame counter in current window
    state['frames_in_window'] += 1

    # Latency
    latency = max(0, current_time - getattr(msg, 'timestamp', current_time))
    state['latency'] = latency

    # Lost frames
    frame_number = getattr(msg, 'frame_number', state['last_frame_number'] + 1)
    expected = state['last_frame_number'] + 1
    lost = max(0, frame_number - expected)
    state['lost_count'] += lost
    state['lost_in_window'] += lost
    state['last_frame_number'] = frame_number

    # FPS
    state['last_timestamp'] = current_time

    # Frame size in MB
    size_mb = len(msg.frame_data)/1e6 if hasattr(msg, 'frame_data') else 0

    # Increment frame counter
    state['frame_count'] += 1

    return state['fps'], state['latency'], state['lost'], size_mb

"""
Prepare metrics to display on screen based on the user's role.

Args:
    role: 'publisher' or 'subscriber' (defines metrics to display)
    msg: Protobuf message with metadata
    fps: Frames per second
    latency: Latency in seconds
    lost: Percentage of lost frames (subscriber only)
    size_mb: Frame size in MB (publisher only)

Returns:
    List[Tuple[str, str, Tuple]]: List of (label, value, color)

Color Coding:
    - FPS: Green (>30), Yellow (20-30), Red (<20)
    - Latency: Green (<100ms), Yellow (100-500ms), Red (>500ms)
    - Lost: Green (<10%), Light Blue (10-50%), Blue (>50%)
    - Encoding: Light Cyan
    - Quality: Orange
"""
def get_display_metrics(role: str, msg, fps: float, latency: float, lost: float = 0, size_mb: float = None):
    metrics = []
    if role == 'publisher':
        if 'fps' in METRICS_CONFIG['publisher']:
            metrics.append(('FPS', f"{fps:.1f}",
            (100, 255, 100) if fps >= 30 else (255, 255, 100) if fps >= 20 else (255, 100, 100)))
        if 'latency' in METRICS_CONFIG['subscriber']:
            color = (100, 255, 100) if latency < 0.1 else (255, 255, 100) if latency < 0.5 else (255, 100, 100)
            metrics.append(('LAT', f"{latency:.3f}s", color))
        if 'size_mb' in METRICS_CONFIG['publisher'] and size_mb is not None:
            metrics.append(('SIZE', f"{size_mb:.3f}MB", (100, 200, 255)))
    else:  # subscriber
        if 'fps' in METRICS_CONFIG['publisher']:
            metrics.append(('FPS', f"{fps:.1f}",
            (100, 255, 100) if fps >= 30 else (255, 255, 100) if fps >= 20 else (255, 100, 100)))
        if 'latency' in METRICS_CONFIG['subscriber']:
            color = (100, 255, 100) if latency < 0.1 else (255, 255, 100) if latency < 0.5 else (255, 100, 100)
            metrics.append(('LAT', f"{latency:.3f}s", color))
        if 'lost' in METRICS_CONFIG['subscriber']:
            lost_color = (100, 255, 100) if lost < 10 else (100, 200, 255) if lost < 50 else (100, 100, 255)
            metrics.append(('LOST', f"{lost:.1f}%", lost_color))

    # Commons
    metrics.append(('ENC', get_encoding_name(getattr(msg, 'encoding', 1)), (200, 200, 255)))
    metrics.append(('QUAL', getattr(msg, 'compression_quality', 0), (255, 200, 100)))

    return metrics

"""
Draws metrics in the top right corner of the frame.

Args:
    frame: Video frame to draw from
    metrics: List of metrics to display (from get_display_metrics)
    width: Frame width in pixels
    font: OpenCV font
    font_scale: Font scale
    thickness: Text thickness
    margin: Margin from the edges
    line_height: Line spacing

Returns:
    np.ndarray: Frame with drawn metrics

Layout:
    Top right corner, right-aligned
    Semi-transparent black background for readability
    Text colored according to metric
"""
def draw_metrics_overlay(frame, metrics, width, font=cv2.FONT_HERSHEY_SIMPLEX, font_scale=0.6, thickness=2, margin=10, line_height=25):
    y_pos = margin
    for label, value, color in metrics:
        text = f"{label}: {value}"
        text_size = cv2.getTextSize(text, font, font_scale, thickness)[0]

        # Position in upper right corner
        x_pos = width - text_size[0] - margin

        # Draw a black background for readability
        cv2.rectangle(frame,
                      (x_pos - 5, y_pos - 3),
                      (x_pos + text_size[0] + 5, y_pos + text_size[1] + 3),
                      (0, 0, 0), -1)

        # Draw text
        cv2.putText(frame, text, (x_pos, y_pos + text_size[1]),
                    font, font_scale, color, thickness)
        y_pos += line_height
    return frame

"""

Draw a complete overlay with all metrics and visual elements.

Combine multiple elements into a single overlay:
    1. Camera label (top left corner)
    2. Frame number (top center)
    3. Current time (top center, below the frame)
    4. Metrics (top right corner)
    5. Keyframe indicator (top right corner)
    6. Status bar (top edge)

Args:
    frame: Original video frame
    msg: Protobuf message with metadata
    fps: Frames per second (if None, it is calculated)
    latency: Latency in seconds (if None, it is calculated)
    lost: Percentage of lost frames
    size_mb: Frame size in MB
    role: 'publisher' or 'subscriber'
    camera_label: Label to identify the camera

Returns:
    np.ndarray: Frame with applied overlay

Design Principles:
    - Clear visual hierarchy
    - Meaningful colors (code (in colors)
    - Essential information without saturation
    - Responsive to different frame sizes
"""
def draw_optimized_overlay(frame: np.ndarray, msg, fps: float, latency: float, lost: int,
                            size_mb: float = None, role: str = None, camera_label: str = None
                           ):
    if fps is None or latency is None:
        fps, latency, lost, size_mb = update_stream_metrics(camera_label, msg)

    # Create a copy of the frame so as not to modify the original
    display_frame = frame.copy()
    height, width = frame.shape[:2]

    # Font settings
    font = cv2.FONT_HERSHEY_SIMPLEX
    font_medium = 0.8
    font_small = 0.6
    thickness = 2
    line_height = 25
    margin = 10

    # --- Camera label (top left corner)---
    if camera_label:
        label_size = cv2.getTextSize(camera_label, font, font_medium, thickness)[0]

        # Label background
        cv2.rectangle(display_frame,
                      (margin, margin),
                      (margin + label_size[0] + 10, margin + label_size[1] + 10),
                      (0, 0, 0), -1)

        # Label text
        cv2.putText(display_frame, camera_label,
                    (margin + 5, margin + label_size[1] + 5),
                    font, font_medium, (255, 255, 255), thickness)

    # --- Frame header (upper center)---
    frame_text = f"FRAME #{getattr(msg, 'frame_number', 0):04d}"
    frame_size = cv2.getTextSize(frame_text, font, font_medium, thickness)[0]
    frame_x = (width - frame_size[0]) // 2
    frame_y = margin + frame_size[1] + 5

    # Background for frame number
    cv2.rectangle(display_frame,
                  (frame_x - 8, frame_y - frame_size[1] - 3),
                  (frame_x + frame_size[0] + 8, frame_y + 3),
                  (0, 0, 0), -1)

    # Frame number text
    cv2.putText(display_frame, frame_text, (frame_x, frame_y),
                font, font_medium, (255, 255, 255), thickness)

    # --- Time header (top center, below the frame)---
    time_text = time.strftime("%H:%M:%S")
    time_size = cv2.getTextSize(time_text, font, font_small, thickness)[0]
    time_x = (width - time_size[0]) // 2
    time_y = frame_y + line_height

    # Background for hour
    cv2.rectangle(display_frame,
                  (time_x - 5, time_y - time_size[1] - 3),
                  (time_x + time_size[0] + 5, time_y + 3),
                  (0, 0, 0), -1)

    # Time text
    cv2.putText(display_frame, time_text, (time_x, time_y),
                font, font_small, (200, 200, 200), thickness)

    # --- Obtain metrics if they are not provided ---
    if fps is None or latency is None:
        fps, latency, lost, size_mb = update_stream_metrics(camera_label, msg)
    else:
        lost = lost or 0.0
        size_mb = size_mb or 0.0

    # --- Draw metrics (top right corner) ---
    metrics = get_display_metrics(role, msg, fps, latency, lost, size_mb)
    draw_metrics_overlay(display_frame, metrics, width, font, font_small, thickness, margin, line_height)

    # --- Keyframe indicator (top right corner) ---
    if hasattr(msg, 'is_keyframe') and msg.is_keyframe:
        key_text = "KEY"
        key_size = cv2.getTextSize(key_text, font, font_small, thickness + 1)[0]
        key_x = width - key_size[0] - margin
        key_y = margin + key_size[1] + 5

        # Blue background for keyframe
        cv2.rectangle(display_frame,
                      (key_x - 8, key_y - key_size[1] - 5),
                      (key_x + key_size[0] + 8, key_y + 5),
                      (0, 0, 150), -1)

        # Keyframe text
        cv2.putText(display_frame, key_text, (key_x, key_y),
                    font, font_small, (255, 255, 255), thickness + 1)

    # --- Status bar (top border) ---
    encoding_color = get_encoding_color(getattr(msg, 'encoding', 1))
    cv2.rectangle(display_frame, (0, 0), (width, 2), encoding_color, -1)

    return display_frame

"""
Compatibility function to maintain existing API.

Args:
    *args: Positional arguments for draw_optimized_overlay
    **kwargs: Keyword arguments for draw_optimized_overlay

Returns:
    np.ndarray: Frame with overlay applied

Note:
    This function only redirects to draw_optimized_overlay
    to maintain compatibility with existing code
"""
def draw_overlay(*args, **kwargs):
    return draw_optimized_overlay(*args, **kwargs)