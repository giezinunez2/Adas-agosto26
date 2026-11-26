# -*- coding: utf-8 -*-
"""
Puente de compatibilidad: core.imagen_pb2  ->  protos.imagen_pb2

El esquema Protobuf canónico (VideoFrame con is_metadata_only / objects, DetectedObject,
Point2D, EncodingType) vive UNICAMENTE en protos/imagen_pb2.py (generado desde
protos/imagen.proto).

Este módulo NO define ningún descriptor propio: solo reexporta los símbolos de
protos.imagen_pb2. Así, cualquier `from core import imagen_pb2` heredado sigue
funcionando y NO se registra un segundo 'imagen.proto' en el descriptor pool
(que provocaría: "TypeError: Couldn't build proto file into descriptor pool:
duplicate file name imagen.proto").

NO regenerar este archivo con protoc.
"""
from protos.imagen_pb2 import *  # noqa: F401,F403
from protos.imagen_pb2 import (  # noqa: F401  (explícito, para IDEs y linters)
    DESCRIPTOR,
    EncodingType,
    Point2D,
    DetectedObject,
    VideoFrame,
    NONE,
    JPEG,
    LZ4,
    PNG,
)
