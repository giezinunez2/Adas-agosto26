from dataclasses import dataclass

#=============================================================================
#                          MIDDLEWARE CONFIGURATIONS
#=============================================================================
@dataclass
class MiddlewareConfig:
    type: str = "ecal"          # Default middleware (options: "ecal", "rabbitmq")

MIDDLEWARE_CONFIG = MiddlewareConfig()

MIDDLEWARE_TYPE = MIDDLEWARE_CONFIG.type


#=============================================================================
#                          ECAL-INTERFACE CONFIGURATIONS
#=============================================================================
# Configuration for eCAL
@dataclass
class EcalConfig:
    topic_prefix: str = 'webcam_stream'         # topic name

ECAL_CONFIG = EcalConfig()

TOPIC_PREFIX = ECAL_CONFIG.topic_prefix


# =============================================================================
#                       RABBITMQ CONFIGURATIONS
# =============================================================================
@dataclass
class RabbitMQConfig:
    # Connection settings
    host: str = "192.168.50.52"                 # Conexion server
    port: int = 5672                             # Conection port
    username: str = "zorros"                     # User connection
    password: str = "zorros"                     # User password

    # Streaming setup (cameras)
    streaming_exchange: str = "webcam_stream"   # Name of streamings
    streaming_exchange_type: str = "topic"      # Type: direct, fanout, topic, headers

    # Queue configuration
    queue_durability: bool = True               # Persistent queue


RABBITMQ_CONFIG = RabbitMQConfig()

HOST = RABBITMQ_CONFIG.host
PORT = RABBITMQ_CONFIG.port
USERNAME = RABBITMQ_CONFIG.username
PASSWORD = RABBITMQ_CONFIG.password
STREAM_EXCHANGE = RABBITMQ_CONFIG.streaming_exchange
STREAM_EXCHANGE_TYPE = RABBITMQ_CONFIG.streaming_exchange_type
QUEUE_DURABILITY = RABBITMQ_CONFIG.queue_durability
