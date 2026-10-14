#!/bin/bash
# ServisZeka demo başlatıcı (macOS): Finder'da çift tıklayın.
# Docker Desktop açık olmalı. Durdurmak için bu pencerede Ctrl+C.
cd "$(dirname "$0")" || exit 1

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker bulunamadı. Docker Desktop'ı kurun: https://www.docker.com/products/docker-desktop/"
  read -r -p "Kapatmak için Enter..." _; exit 1
fi
if ! docker info >/dev/null 2>&1; then
  echo "Docker Desktop çalışmıyor; başlatılıyor..."
  open -a Docker
  for i in $(seq 1 60); do docker info >/dev/null 2>&1 && break; sleep 2; done
  if ! docker info >/dev/null 2>&1; then
    echo "Docker Desktop açılamadı. Elle açıp bu dosyaya tekrar çift tıklayın."
    read -r -p "Kapatmak için Enter..." _; exit 1
  fi
fi

echo "ServisZeka derleniyor ve başlatılıyor (ilk seferde birkaç dakika sürer)..."
# Portal hazır olunca tarayıcıyı aç
( for i in $(seq 1 600); do
    if curl -fsS http://localhost:8080 >/dev/null 2>&1 && curl -fsS http://localhost:8000/health >/dev/null 2>&1; then
      open http://localhost:8080; exit 0
    fi
    sleep 2
  done ) &

docker compose -f docker-compose.demo.yml up --build
