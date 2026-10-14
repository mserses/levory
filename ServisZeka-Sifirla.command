#!/bin/bash
# ServisZeka demo verisini siler (bir sonraki başlatmada demo verisi yeniden yüklenir).
cd "$(dirname "$0")" || exit 1
docker compose -f docker-compose.demo.yml down -v
echo "Demo verisi silindi."
read -r -p "Kapatmak için Enter..." _
