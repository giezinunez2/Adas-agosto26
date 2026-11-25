# Adas-agosto26
# CODIGO PARA EJECUTARLO


cd ~/Adas-agosto26
git fetch origin
git checkout -B docker-setup origin/docker-setup
sudo ufw disable
export DISPLAY=:0
xhost +local:root
docker compose --profile pi5-standalone up -d --build

cd ~/Adas-agosto26
./iniciar_camaras.sh
