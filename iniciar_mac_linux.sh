#!/usr/bin/env bash
# Traductor de Pantalla - inicio para macOS y Linux
cd "$(dirname "$0")" || exit 1
VERSION=2

PY=""
for c in python3.12 python3.11 python3.13 python3; do
  if command -v "$c" >/dev/null 2>&1; then PY=$(command -v "$c"); break; fi
done
if [ -z "$PY" ]; then
  echo "No encontré Python 3. Instálalo (3.12 recomendado) desde https://www.python.org/downloads/"
  read -r -p "Pulsa Enter para salir"; exit 1
fi

if [ ! -x venv/bin/python ]; then
  echo "Creando entorno por primera vez…"
  "$PY" -m venv venv || { echo "En Linux quizá necesites: sudo apt install python3-venv python3-tk"; exit 1; }
fi

if [ "$(cat venv/.instalado 2>/dev/null)" != "$VERSION" ]; then
  echo "=== Primera vez: instalando programa e idiomas (1-2 GB, 5-15 min) ==="
  venv/bin/python -m pip install --upgrade pip
  if [ "$(uname)" = "Linux" ]; then
    # versión solo-CPU de PyTorch (evita descargar 2 GB de CUDA)
    venv/bin/python -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu || exit 1
  fi
  venv/bin/python -m pip install -r requirements.txt || exit 1
  venv/bin/python descargar_idiomas.py || exit 1
  echo "$VERSION" > venv/.instalado
fi

exec venv/bin/python traductor_pantalla.py
