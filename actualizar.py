# -*- coding: utf-8 -*-
"""
Actualizaciones ligeras: descarga solo el código nuevo (menos de 1 MB) desde
GitHub Releases y reemplaza la carpeta "app". Si la actualización necesita una
base nueva (librerías), avisa para descargar el paquete completo.
"""
import io
import json
import os
import shutil
import sys
import zipfile

import nucleo
import version
from nucleo import log

REPO = "Luxemanuel/TraductorPantalla"
API = f"https://api.github.com/repos/{REPO}/releases/latest"
PAGINA = f"https://github.com/{REPO}/releases/latest"


def _num(v):
    return tuple(int(x) for x in str(v).split(".") if x.isdigit())


def version_base():
    try:
        return int(os.environ.get("TRADUCTOR_VERSION_BASE", "0"))
    except ValueError:
        return 0


def buscar():
    """Devuelve dict con la novedad, o None si ya está al día.
    {'version', 'base_minima', 'url_app', 'necesita_base', 'pagina'}
    Revisa las últimas publicaciones y elige la de versión más alta."""
    import re
    import requests
    r = requests.get(API.replace("/latest", "?per_page=15"), timeout=10,
                     headers={"Accept": "application/vnd.github+json"})
    r.raise_for_status()
    mejor = None
    for rel in r.json():
        m = re.match(r"v(\d+\.\d+\.\d+)-build(\d+)", rel.get("tag_name", ""))
        assets = {a["name"]: a["browser_download_url"] for a in rel.get("assets", [])}
        if not m or "version.json" not in assets or "actualizacion.zip" not in assets:
            continue
        clave = (_num(m.group(1)), int(m.group(2)))
        if mejor is None or clave > mejor[0]:
            mejor = (clave, rel, assets)
    if mejor is None:
        return None
    _, rel, assets = mejor
    info = requests.get(assets["version.json"], timeout=10).json()
    if _num(info["version"]) <= _num(version.VERSION):
        return None
    return {"version": info["version"], "base_minima": int(info.get("base_minima", 0)),
            "url_app": assets["actualizacion.zip"], "pagina": rel.get("html_url", PAGINA),
            "notas": rel.get("body", ""),
            "necesita_base": int(info.get("base_minima", 0)) > version_base()}


def aplicar(datos_zip, carpeta_base=None):
    """Reemplaza la carpeta app con el contenido del zip (bytes). Guarda una copia de la anterior."""
    base = carpeta_base or nucleo.BASE
    app = os.path.join(base, "app")
    nueva = os.path.join(base, "app_nueva")
    vieja = os.path.join(base, "app_anterior")
    shutil.rmtree(nueva, ignore_errors=True)
    with zipfile.ZipFile(io.BytesIO(datos_zip)) as z:
        z.extractall(nueva)
    if not os.path.isfile(os.path.join(nueva, "traductor_pantalla.py")):
        shutil.rmtree(nueva, ignore_errors=True)
        raise RuntimeError("La actualización está incompleta")
    shutil.rmtree(vieja, ignore_errors=True)
    if os.path.isdir(app):
        os.replace(app, vieja)
    os.replace(nueva, app)
    return app


def descargar_y_aplicar(novedad):
    import requests
    r = requests.get(novedad["url_app"], timeout=60)
    r.raise_for_status()
    return aplicar(r.content)


def reiniciar():
    """Vuelve a abrir el programa (para cargar el código nuevo)."""
    import subprocess
    os.environ["TRADUCTOR_REINICIO"] = "1"
    if getattr(sys, "frozen", False):
        subprocess.Popen([sys.executable], close_fds=True)
    else:
        subprocess.Popen([sys.executable] + sys.argv, close_fds=True)
    os._exit(0)
