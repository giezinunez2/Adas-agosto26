# syntax=docker/dockerfile:1
FROM ubuntu:24.04

ARG WITH_TORCH=1
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    QT_X11_NO_MITSHM=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        software-properties-common ca-certificates gnupg \
        python3 python3-pip \
        libgl1 libglx-mesa0 libglib2.0-0 libsm6 libxext6 libxrender-dev \
        libxkbcommon-x11-0 libxcb-xinerama0 libxcb-cursor0 \
        v4l-utils pciutils \
    && add-apt-repository -y ppa:ecal/ecal-5.12 \
    && apt-get update \
    && apt-get install -y --no-install-recommends ecal python3-ecal5 \
    && sed -i 's/^network_enabled *= *false/network_enabled = true/' /etc/ecal/ecal.ini \
    && rm -rf /var/lib/apt/lists/*

RUN if [ "$WITH_TORCH" = "1" ]; then \
      pip3 install --break-system-packages --ignore-installed \
        --extra-index-url https://download.pytorch.org/whl/cpu \
        torch torchvision ultralytics opencv-python-headless protobuf lz4 numpy \
        "watchdog[watchmedo]"; \
    else \
      pip3 install --break-system-packages --ignore-installed \
        opencv-python-headless protobuf lz4 numpy "watchdog[watchmedo]"; \
    fi

WORKDIR /app
COPY . /app
CMD ["bash"]
