import threading
import queue
import cv2
import torch
import numpy as np
from ultralytics import YOLO

"""
Real-time video processor using YOLO for detection and segmentation.

This class provides optimized video frame processing using YOLOv11 
for two modes of operation: object detection and semantic segmentation.
It includes optimizations for real-time processing and GPU/CPU support.

Args:
    mode: Mode of operation ('detection' or 'segmentation')
    processing_interval: Process every N frames (1 = all frames)
    confidence: Confidence threshold for detections (0.0 to 1.0)

Attributes:
    model: Loaded YOLO model
    mode: Current mode of operation
    processing_interval: Processing interval
    confidence: Confidence threshold
    frame_count: Total number of frames processed
    last_processed_frame: Last frame processed (for caching)
    lock: Lock for thread safety
    running: Running state
    colors: Color map per class (segmentation only)
    transparency: Mask transparency (segmentation only)
"""
class YOLOProcessor:
    def __init__(self, mode: str, processing_interval: int, confidence: float):

        # Validate parameters
        if mode not in ['detection', 'segmentation']:
            raise ValueError(f"Modo no válido: {mode}. Usar 'detection' o 'segmentation'")
        if not 0.0 <= confidence <= 1.0:
            raise ValueError(f"Confianza debe estar entre 0.0 y 1.0, recibido: {confidence}")
        if processing_interval < 1:
            raise ValueError(f"processing_interval debe ser >= 1, recibido: {processing_interval}")

        self.mode = mode

        # Build model route according to the mode
        if mode == 'segmentation':
            model_name = f"yolo11n-seg.pt"
        else:
            model_name = f"yolo11n.pt"

        self.model = YOLO(model_name)

        # Optimize for GPU if available
        if torch.cuda.is_available():
            self.model.to('cuda')
            print("✅ YOLO using GPU")
        else:
            print("⚠️ YOLO using CPU")

        # Processing configuration
        self.processing_interval = processing_interval
        self.confidence = confidence
        self.frame_count = 0
        self.last_processed_frame = None
        self.lock = threading.Lock()
        self.running = True

        # Specific configuration for segmentation
        if mode == 'segmentation':
            self.colors = {
                "person": (0, 255, 0),     # Green - people
                #"car": (0, 0, 255),        # Red - cars
                #"dog": (255, 0, 0),        # Blue - dogs
                #"cat": (255, 255, 0),      # Cyan - cats
                #"chair": (255, 0, 255),    # Magenta - chairs
                #"bicycle": (0, 255, 255),  # Yellow - bicycles
                # You can add another class
            }
            self.transparency = 0.4  # Default transparency for segmentation

    """
    Processes a frame using YOLO (synchronous).

    Applies detection or segmentation depending on the configured mode.
    Uses caching and interval processing to optimize performance.

    Args:
        frame: Input frame as a NumPy array (BGR format)

    Returns:
        np.ndarray: Processed frame with detection/segmentation applied

    Note:
    - Only processes each `processing_interval` frame
    - Uses the last processed frame as a cache when not processing
    - Thread-safe through locking
    """
    def process_frame(self, frame):
        self.frame_count += 1

        # Process only every N frames for better performance
        if self.frame_count % self.processing_interval != 0:
            return self.last_processed_frame if self.last_processed_frame is not None else frame

        try:
            with self.lock:
                # Optimized YOLO processing
                results = self.model(
                    frame,
                    verbose=False,
                    imgsz=320,  # Reduce size for greater speed
                    conf=self.confidence,  # Configurable trust threshold
                    device='0' if torch.cuda.is_available() else 'cpu'
                )

                # Draw according to the mode
                if self.mode == 'segmentation':
                    processed_frame = self._draw_segmentation(frame, results)
                else:
                    processed_frame = self._draw_detections(frame, results)

                self.last_processed_frame = processed_frame
                return processed_frame

        except Exception as e:
            print(f"❌ YOLO processing error: {e}")
            return frame

    """
    Draws the detections in the frame (only people by default).

    Args:
        frame: Original frame
        results: YOLO results

    Returns:
        np.ndarray: Frame with bounding boxes drawn
    """
    def _draw_detections(self, frame, results):
        frame_copy = frame.copy()
        for result in results:
            for box in result.boxes:
                #toDo: You can improve the filter to add more classes.
                if int(box.cls) == 0:  # People only (class 0)
                    x1, y1, x2, y2 = map(int, box.xyxy[0])
                    conf = box.conf[0]

                    # Draw rectangle
                    cv2.rectangle(frame_copy, (x1, y1), (x2, y2), (0, 255, 0), 2)

                    # Draw label
                    label = f"Person {conf:.2f}"
                    label_size = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)[0]
                    # Label background
                    cv2.rectangle(frame_copy, (x1, y1 - label_size[1] - 10),
                                  (x1 + label_size[0], y1), (0, 255, 0), -1)
                    # Label text
                    cv2.putText(frame_copy, label, (x1, y1 - 5),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)

        return frame_copy

    """
    Draws color-coded segmentation by class.
    
    Args:
        frame: Original frame
        results: YOLO results with masks
    
    Returns:
        np.ndarray: Frame with overlapping segmentation
    """
    def _draw_segmentation(self, frame, results):
        frame_segmented = frame.copy()
        result = results[0]

        if result.masks is not None:
            masks = result.masks.data.cpu().numpy()

            for i, mask in enumerate(masks):
                # Get class information
                class_id = int(result.boxes.cls[i])
                class_name = result.names[class_id]
                confidence = float(result.boxes.conf[i])

                # Filter by minimum trust
                if confidence < self.confidence:
                    continue

                # Get class color
                if class_name in self.colors:
                    color = self.colors[class_name]
                else:
                    continue

                    # Resize mask to frame size
                mask_resized = cv2.resize(mask,
                                          (frame.shape[1],
                                           frame.shape[0]))

                # Create binary mask
                mask_binary = (mask_resized > 0.5).astype(np.uint8)

                # Create a color layer for this class
                color_layer = np.zeros_like(frame)
                color_layer[mask_binary == 1] = color

                # Apply transparency and combine with original frame
                frame_segmented = cv2.addWeighted(frame_segmented, 1,
                                                  color_layer, self.transparency, 0)

        return frame_segmented

    """
    Adjusts transparency for segmentation.

    Args:
        value: Transparency value (0.1 to 0.9)

    Raises:
        ValueError: If the value is outside the allowed range

    Note:
        Only applies in 'segmentation' mode
    """
    def set_transparency(self, value):
        if 0.1 <= value <= 0.9:
            self.transparency = value
            #print(f" Transparency adjusted to: {value:.1f}")

    """
    Stops the processor.
    Releases resources and marks the processor as non-executable.
    """
    def stop(self):
        self.running = False