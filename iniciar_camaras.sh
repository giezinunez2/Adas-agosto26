#!/usr/bin/env bash
set -e
xhost +local:root >/dev/null

# Detecta solo camaras reales (index=0 en V4L2)
CAMERAS=$(python3 -c '
import glob, os
valid = []
for idx_file in sorted(glob.glob("/sys/class/video4linux/video*/index")):
    if open(idx_file).read().strip() == "0":
        dev = os.path.basename(os.path.dirname(idx_file))
        valid.append(dev.replace("video", ""))
print(" ".join(valid[:4]))
')

if [ -z "$CAMERAS" ]; then
    echo "No se detecto ninguna camara."
    exit 1
fi

docker rm -f adas_dashboard $(docker ps -aq --filter "name=adas_pub_") 2>/dev/null || true

STREAM_ID=0
STREAM_IDS=""
for CAM_IDX in $CAMERAS; do
    STREAM_ID=$((STREAM_ID + 1))
    STREAM_IDS="$STREAM_IDS $STREAM_ID"
    echo "Iniciando /dev/video${CAM_IDX} -> webcam_stream_${STREAM_ID}"
    docker run -d --name "adas_pub_${STREAM_ID}" \
        --network host --ipc host --pid host --init \
        -e DISPLAY="${DISPLAY:-:0}" \
        -e QT_X11_NO_MITSHM=1 \
        -e PYTHONUNBUFFERED=1 \
        -v "$PWD":/app \
        -v "$PWD/ecal.ini":/etc/ecal/ecal.ini \
        -v /dev/shm:/dev/shm \
        -v /tmp/.X11-unix:/tmp/.X11-unix \
        --device "/dev/video${CAM_IDX}:/dev/video${CAM_IDX}" \
        adas:dev \
        python3 main_pub.py --stream-id "$STREAM_ID" --camera "$CAM_IDX" --no-adas --no-preview
done

sleep 1
docker logs --tail 5 adas_pub_1

echo "Suscribiendo Dashboard a streams:$STREAM_IDS"
docker run --rm --name adas_dashboard \
    --network host --ipc host --pid host --init \
    -e DISPLAY="${DISPLAY:-:0}" \
    -e QT_X11_NO_MITSHM=1 \
    -e PYTHONUNBUFFERED=1 \
    -v "$PWD":/app \
    -v "$PWD/ecal.ini":/etc/ecal/ecal.ini \
    -v /dev/shm:/dev/shm \
    -v /tmp/.X11-unix:/tmp/.X11-unix \
    adas:dev \
    python3 main_sub_ai.py --streams $STREAM_IDS

docker rm -f $(docker ps -aq --filter "name=adas_pub_") 2>/dev/null || true
