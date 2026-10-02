import cv2
import numpy as np
import logging
import time
from typing import Optional
from core import imagen_pb2

# Configure logging
logging.basicConfig(level=logging.WARNING, format='[%(levelname)s] %(message)s')

try:
    import lz4.frame as lz4f
except ImportError:
    lz4f = None

"""
    Serializes an OpenCV frame to a protobuf VideoFrame message.
    
    Converts a numpy array (OpenCV frame) to a protobuf message with optional
    compression and encoding. Supports multiple encoding formats with configurable
    quality settings.
    
    Args:
        frame: OpenCV frame as numpy array (height, width, channels)
        frame_number: Sequential frame identifier
        keyframe_interval: Number of frames between keyframes (full quality frames)
        encoding: Encoding method ('JPEG', 'PNG', 'LZ4', 'NONE')
        quality: Compression quality (0-100 for JPEG/PNG, 0-16 for LZ4)
    
    Returns:
        imagen_pb2.VideoFrame: Protobuf message containing frame data and metadata
    
    Raises:
        ValueError: If encoding method is not supported
        RuntimeError: If compression/encoding fails
"""
def frame_to_proto(frame: np.ndarray, frame_number: int, keyframe_interval: int,
                   encoding: str, quality: int) -> imagen_pb2.VideoFrame:
    height, width, channels = frame.shape

    # Create protobuf message
    msg = imagen_pb2.VideoFrame()
    msg.width = width
    msg.height = height
    msg.channels = channels
    msg.frame_number = frame_number
    msg.timestamp = time.time()
    msg.encoding = getattr(imagen_pb2, encoding, imagen_pb2.JPEG)
    msg.compression_quality = quality
    msg.is_keyframe = (frame_number % keyframe_interval == 0)

    # Apply encoding based on selected method
    if encoding == 'JPEG':
        encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), quality]
        success, encoded_data = cv2.imencode('.jpg', frame, encode_param)
        if success:
            msg.frame_data = encoded_data.tobytes()
        else:
            raise RuntimeError("JPEG encoding failed")

    # PNG compression: 0=no compression, 9=max compression
    elif encoding == 'PNG':
        png_compression = min(9, max(0, 10 - quality // 10))
        encode_param = [int(cv2.IMWRITE_PNG_COMPRESSION), png_compression]
        success, encoded_data = cv2.imencode('.png', frame, encode_param)
        if success:
            msg.frame_data = encoded_data.tobytes()
        else:
            raise RuntimeError("PNG encoding failed")

    # LZ4 compression level: 0-16 (higher = more compression)
    elif encoding == 'LZ4' and lz4f:
        msg.frame_data = lz4f.compress(frame.tobytes(), compression_level=min(16, max(0, quality // 6)))
    elif encoding == 'LZ4':
        logging.warning("LZ4 not available, falling back to JPEG")
        encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), quality]
        success, encoded_data = cv2.imencode('.jpg', frame, encode_param)
        if success:
            msg.frame_data = encoded_data.tobytes()
            msg.encoding = imagen_pb2.JPEG
        else:
            raise RuntimeError("Fallback JPEG encoding failed")

    elif encoding == 'NONE':
        msg.frame_data = frame.tobytes()

    else:
        raise ValueError(f"Unsupported encoding method: {encoding}")

    return msg

"""
    Deserializes a VideoFrame protobuf message to an OpenCV frame.

    Reconstructs a numpy array from protobuf data, handling different
    encoding formats and performing validation at each step.

    Args:
        msg: Protobuf VideoFrame message to decode

    Returns:
        Optional[np.ndarray]: Decoded frame as numpy array, or None if decoding fails
"""
def proto_to_frame(msg) -> Optional[np.ndarray]:
    try:
        # Basic data verification
        if not msg.frame_data:
            logging.warning("Error: Frame data is empty")
            return None

        # Verify required fields
        required_attrs = ['width', 'height', 'channels', 'encoding']
        if not all(hasattr(msg, attr) for attr in required_attrs):
            logging.warning("Error: Incomplete message - missing required fields")
            return None

        frame = None

        # FORMAT-SPECIFIC HANDLING
        if msg.encoding == imagen_pb2.JPEG:
            frame = _decode_opencv_format(msg.frame_data, "JPEG")

        elif msg.encoding == imagen_pb2.PNG:
            frame = _decode_opencv_format(msg.frame_data, "PNG")

        elif msg.encoding == imagen_pb2.LZ4:
            frame = _decode_lz4(msg.frame_data, msg.height, msg.width, msg.channels)

        elif msg.encoding == imagen_pb2.NONE:
            frame = _decode_raw(msg.frame_data, msg.height, msg.width, msg.channels)

        else:
            logging.warning(f"Error: Unsupported encoding: {msg.encoding}")
            return None

        # FINAL FRAME VERIFICATION
        return _verify_and_reshape_frame(frame, msg)

    except Exception as e:
        logging.warning(f"Error decoding frame {getattr(msg, 'frame_number', 'N/A')}: {e}")
        return None

"""
Decodes formats that OpenCV understands (JPEG, PNG).

Args:
    frame_data: Compressed frame data as bytes
    format_name: Format identifier for logging ('JPEG' or 'PNG')

Returns:
    Optional[np.ndarray]: Decoded frame or None if decoding fails
"""
def _decode_opencv_format(frame_data, format_name: str) -> Optional[np.ndarray]:
    try:
        # Convert bytes to numpy array and decode
        frame_bytes = np.frombuffer(frame_data, np.uint8)
        frame = cv2.imdecode(frame_bytes, cv2.IMREAD_COLOR)

        if frame is None:
            logging.warning(f"Error: OpenCV failed to decode {format_name} format")
            return None

        return frame
    except Exception as e:
        logging.warning(f"Error decoding {format_name}: {e}")
        return None

"""
Decodes LZ4 compressed frame data.

Args:
    frame_data: LZ4 compressed frame data
    height: Expected frame height
    width: Expected frame width
    channels: Expected number of color channels

Returns:
    Optional[np.ndarray]: Decompressed frame or None if decoding fails
"""
def _decode_lz4(frame_data, height: int, width: int, channels: int) -> Optional[np.ndarray]:
    try:
        import lz4.frame
        # Decompress data
        decompressed_data = lz4.frame.decompress(frame_data)

        # Reconstruct numpy array
        frame = np.frombuffer(decompressed_data, dtype=np.uint8)

        # Verify expected size
        expected_size = height * width * channels
        if len(frame) != expected_size:
            logging.warning(f"LZ4 Error: Incorrect size. Expected: {expected_size}, Got: {len(frame)}")
            return None

        return frame.reshape((height, width, channels))
    except Exception as e:
        logging.warning(f"Error decompressing LZ4: {e}")
        return None

"""
Decodes uncompressed RAW frame data.

Args:
    frame_data: Raw uncompressed frame data
    height: Expected frame height
    width: Expected frame width
    channels: Expected number of color channels

Returns:
    Optional[np.ndarray]: Reconstructed frame or None if size mismatch
"""
def _decode_raw(frame_data, height: int, width: int, channels: int) -> Optional[np.ndarray]:
    try:
        frame = np.frombuffer(frame_data, dtype=np.uint8)

        # Verify expected size
        expected_size = height * width * channels
        if len(frame) != expected_size:
            logging.warning(f"RAW Error: Incorrect size. Expected: {expected_size}, Got: {len(frame)}")
            return None

        return frame.reshape((height, width, channels))
    except Exception as e:
        logging.warning(f"Error reconstructing RAW frame: {e}")
        return None

"""
Final verification and dimension adjustment of the decoded frame.

Args:
    frame: Decoded frame (may be None or wrong shape)
    msg: Original protobuf message for expected dimensions

Returns:
    Optional[np.ndarray]: Validated and reshaped frame, or None if invalid
"""
def _verify_and_reshape_frame(frame, msg) -> Optional[np.ndarray]:
    if frame is None:
        logging.warning("Error: Frame is None after decoding")
        return None

    if frame.size == 0:
        logging.warning("Error: Frame is empty")
        return None

    expected_shape = (msg.height, msg.width, msg.channels)

    if frame.shape != expected_shape:
        logging.warning(f"Warning: Dimensions don't match. Expected: {expected_shape}, Got: {frame.shape}")

        # Try to automatically resize if possible
        try:
            if frame.size == np.prod(expected_shape):
                frame = frame.reshape(expected_shape)
                logging.info("Frame automatically resized")
            else:
                logging.warning("Error: Cannot resize - incompatible sizes")
                return None
        except Exception as e:
            logging.warning(f"Error resizing frame: {e}")
            return None

    return frame
