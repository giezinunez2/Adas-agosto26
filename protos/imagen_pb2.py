# -*- coding: utf-8 -*-
# Generated equivalent of: protoc --proto_path=protos --python_out=protos protos/imagen.proto
# source: imagen.proto
"""Generated protocol buffer code."""
from google.protobuf.internal import builder as _builder
from google.protobuf import descriptor as _descriptor
from google.protobuf import descriptor_pool as _descriptor_pool
from google.protobuf import symbol_database as _symbol_database
# @@protoc_insertion_point(imports)

_sym_db = _symbol_database.Default()


DESCRIPTOR = _descriptor_pool.Default().AddSerializedFile(b'\n\x0cimagen.proto\x12\x0fprotocolBuffers"%\n\x07Point2D\x12\x0c\n\x01x\x18\x01 \x01(\x05R\x01x\x12\x0c\n\x01y\x18\x02 \x01(\x05R\x01y"\xac\x02\n\x0eDetectedObject\x12\x0e\n\x02x1\x18\x01 \x01(\x05R\x02x1\x12\x0e\n\x02y1\x18\x02 \x01(\x05R\x02y1\x12\x0e\n\x02x2\x18\x03 \x01(\x05R\x02x2\x12\x0e\n\x02y2\x18\x04 \x01(\x05R\x02y2\x12\x15\n\x06cls_id\x18\x05 \x01(\x05R\x05clsId\x12\x14\n\x05label\x18\x06 \x01(\tR\x05label\x12\x12\n\x04conf\x18\x07 \x01(\x02R\x04conf\x12\x0f\n\x03z_m\x18\x08 \x01(\x02R\x02zM\x12\x1b\n\tlateral_m\x18\t \x01(\x02R\x08lateralM\x12\x15\n\x06dist_m\x18\n \x01(\x02R\x05distM\x12 \n\x0creal_width_m\x18\x0b \x01(\x02R\nrealWidthM\x122\n\x07polygon\x18\x0c \x03(\x0b2\x18.protocolBuffers.Point2DR\x07polygon"\xe2\x03\n\nVideoFrame\x12\x1d\n\nframe_data\x18\x01 \x01(\x0cR\tframeData\x12\x14\n\x05width\x18\x02 \x01(\x05R\x05width\x12\x16\n\x06height\x18\x03 \x01(\x05R\x06height\x12\x1a\n\x08channels\x18\x04 \x01(\x05R\x08channels\x129\n\x08encoding\x18\x05 \x01(\x0e2\x1d.protocolBuffers.EncodingTypeR\x08encoding\x12!\n\x0cframe_number\x18\x06 \x01(\x03R\x0bframeNumber\x12\x1c\n\ttimestamp\x18\x07 \x01(\x01R\ttimestamp\x12/\n\x13compression_quality\x18\x08 \x01(\x05R\x12compressionQuality\x12\x1f\n\x0bis_keyframe\x18\t \x01(\x08R\nisKeyframe\x12(\n\x10is_metadata_only\x18\n \x01(\x08R\x0eisMetadataOnly\x12!\n\x0cinference_ms\x18\x0b \x01(\x02R\x0binferenceMs\x12\x15\n\x06ai_fps\x18\x0c \x01(\x02R\x05aiFps\x129\n\x07objects\x18\r \x03(\x0b2\x1f.protocolBuffers.DetectedObjectR\x07objects*4\n\x0cEncodingType\x12\x08\n\x04NONE\x10\x00\x12\x08\n\x04JPEG\x10\x01\x12\x07\n\x03LZ4\x10\x02\x12\x07\n\x03PNG\x10\x03b\x06proto3')

_globals = globals()
_builder.BuildMessageAndEnumDescriptors(DESCRIPTOR, _globals)
_builder.BuildTopDescriptorsAndMessages(DESCRIPTOR, 'imagen_pb2', _globals)
# @@protoc_insertion_point(module_scope)
