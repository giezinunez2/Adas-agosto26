# syntax=docker/dockerfile:1
# ----------------------------------------------------------------------------
# ADASCore - imagen multi-perfil ARM64 (Raspberry Pi 5 / Pi 4, Ubuntu 24.04)
#
#   WITH_TORCH=0  ->  adas:pi5      Ligera, SIN PyTorch. HailoRT 4.23.0 incluido.
#                                   Nodos Edge con AI HAT  y  Pi Central en modo
#                                   visualizador (main_sub_ai.py --backend none).
#   WITH_TORCH=1  ->  adas:pi-cpu   torch + torchvision + ultralytics. Solo para la
#                                   Pi Central en la Prueba A (yolov8n-seg.pt en CPU).
#
#   docker build --build-arg WITH_TORCH=0 -t adas:pi5 .
#   docker build --build-arg WITH_TORCH=1 -t adas:pi-cpu .
#
# OPENCV_PKG: por defecto "opencv-python" (con backend Qt/X11) porque la Pi Central
# dibuja el dashboard con cv2.imshow. Un wheel *-headless NO tiene GUI y fallaría ahí.
# Para un nodo Edge puramente headless puedes usar:
#   --build-arg OPENCV_PKG=opencv-python-headless
#
# Requisito: el directorio ./hailo_pkg debe existir en el contexto de build
# (con los .so y el .whl para WITH_TORCH=0; puede estar vacío para WITH_TORCH=1).
# ----------------------------------------------------------------------------
FROM ubuntu:24.04

ARG WITH_TORCH=0
ARG OPENCV_PKG=opencv-python

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    QT_X11_NO_MITSHM=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        software-properties-common ca-certificates gnupg \
        python3 python3-pip \
        protobuf-compiler \
        libgl1 libglx-mesa0 libglib2.0-0 libsm6 libxext6 libxrender-dev \
        libxkbcommon-x11-0 libxcb-xinerama0 libxcb-cursor0 \
        libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-randr0 \
        libxcb-render-util0 libxcb-shape0 libxcb-sync1 libxcb-xfixes0 libxcb-xkb1 \
        libfontconfig1 libdbus-1-3 \
        v4l-utils pciutils \
    && add-apt-repository -y ppa:ecal/ecal-5.12 \
    && apt-get update \
    && apt-get install -y --no-install-recommends ecal python3-ecal5 \
    && sed -i 's/^network_enabled *= *false/network_enabled = true/' /etc/ecal/ecal.ini \
    && rm -rf /var/lib/apt/lists/*

# Artefactos HailoRT (solo se instalan con WITH_TORCH=0)
COPY hailo_pkg /tmp/hailo_pkg

RUN if [ "$WITH_TORCH" = "1" ]; then \
      pip3 install --break-system-packages --ignore-installed \
        --extra-index-url https://download.pytorch.org/whl/cpu \
        torch torchvision ultralytics "$OPENCV_PKG" protobuf lz4 numpy \
        "watchdog[watchmedo]"; \
    else \
      cp -a /tmp/hailo_pkg/libhailort.so* /usr/local/lib/ \
      && ldconfig \
      && pip3 install --break-system-packages --ignore-installed \
        /tmp/hailo_pkg/hailort-4.23.0-cp312-cp312-linux_aarch64.whl \
        "$OPENCV_PKG" protobuf lz4 numpy "watchdog[watchmedo]"; \
    fi \
    && rm -rf /tmp/hailo_pkg

WORKDIR /app
COPY . /app

# Recompila el esquema Protobuf para que coincida con el runtime instalado
# y descarta los artefactos Hailo copiados con el código (ya están instalados).
RUN protoc --proto_path=protos --python_out=protos protos/imagen.proto \
    && rm -rf /app/hailo_pkg

CMD ["bash"]
