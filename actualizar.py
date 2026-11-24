# -*- coding: utf-8 -*-
"""
Actualizaciones desde el propio programa (sin pasar por el navegador):
  · ligeras: solo el código nuevo (menos de 1 MB), reemplaza la carpeta "app";
  · completas: cuando la versión trae librerías nuevas, descarga el paquete entero, lo
    comprueba y lo instala al cerrar (tus ajustes, memoria y perfiles se quedan).
Todo se verifica con la huella SHA-256 que publica la compilación junto a cada versión.
"""
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile

import nucleo
import version
from nucleo import log

REPO = "Luxemanuel/TraductorPantalla"
PAQUETE = "TraductorPantalla-Windows.zip"
# lo que es tuyo y nunca se toca al instalar una versión completa. "modelos" (diccionarios sin
# internet y voz) se descarga una vez y se queda: los paquetes completos ya no lo llevan.
DATOS = ["config.json", "glosario.json", "historial.json", "memoria.sqlite", "memoria.sqlite-wal",
         "memoria.sqlite-shm", "perfiles.json", "fuentes_usuario", "registro.txt", "registro_anterior.txt",
         "modelos", "_actualizacion", "_anterior"]
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
        tamanos = {a["name"]: a.get("size", 0) for a in rel.get("assets", [])}
        if not m or "version.json" not in assets or "actualizacion.zip" not in assets:
            continue
        clave = (_num(m.group(1)), int(m.group(2)))
        if mejor is None or clave > mejor[0]:
            mejor = (clave, rel, assets, tamanos)
    if mejor is None:
        return None
    _, rel, assets, tamanos = mejor
    info = requests.get(assets["version.json"], timeout=10).json()
    if _num(info["version"]) <= _num(version.VERSION):
        return None
    return {"version": info["version"], "base_minima": int(info.get("base_minima", 0)),
            "url_app": assets["actualizacion.zip"], "pagina": rel.get("html_url", PAGINA),
            "notas": rel.get("body", ""), "sha256_app": info.get("sha256_app"),
            "url_paquete": assets.get(PAQUETE), "sha256_paquete": info.get("sha256_paquete"),
            "tam_paquete": tamanos.get(PAQUETE, 0),
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


def _comprobar(datos_o_ruta, esperado):
    """Compara la huella SHA-256 con la publicada. Sin huella publicada (versiones antiguas) se acepta."""
    if not esperado:
        return
    h = hashlib.sha256()
    if isinstance(datos_o_ruta, (bytes, bytearray)):
        h.update(datos_o_ruta)
    else:
        with open(datos_o_ruta, "rb") as f:
            for trozo in iter(lambda: f.read(1 << 20), b""):
                h.update(trozo)
    if h.hexdigest().lower() != str(esperado).lower():
        raise RuntimeError("La descarga no coincide con la publicada (huella SHA-256 distinta). "
                           "No se instaló nada.")


def descargar_y_aplicar(novedad):
    import requests
    r = requests.get(novedad["url_app"], timeout=60)
    r.raise_for_status()
    _comprobar(r.content, novedad.get("sha256_app"))
    return aplicar(r.content)


# ---------------------------------------------------------------------------
# Versión completa (cuando cambian las librerías)
# ---------------------------------------------------------------------------
def descargar_paquete(novedad, progreso=lambda hecho, total: None, base=None):
    """Descarga el paquete completo, lo comprueba y lo descomprime en _actualizacion.
    Devuelve la carpeta con el programa nuevo."""
    import requests
    base = base or nucleo.BASE
    if not novedad.get("url_paquete"):
        raise RuntimeError("Esta versión no tiene paquete completo publicado.")
    trabajo = os.path.join(base, "_actualizacion")
    shutil.rmtree(trabajo, ignore_errors=True)
    os.makedirs(trabajo, exist_ok=True)
    destino = os.path.join(trabajo, PAQUETE)
    with requests.get(novedad["url_paquete"], stream=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length") or novedad.get("tam_paquete") or 0)
        hecho = 0
        with open(destino, "wb") as f:
            for trozo in r.iter_content(1 << 20):
                f.write(trozo)
                hecho += len(trozo)
                progreso(hecho, total)
    _comprobar(destino, novedad.get("sha256_paquete"))
    nueva = os.path.join(trabajo, "nueva")
    with zipfile.ZipFile(destino) as z:
        for n in z.namelist():                       # nada fuera de la carpeta de trabajo
            if n.startswith(("/", "\\")) or ".." in n.replace("\\", "/").split("/"):
                raise RuntimeError("El paquete tiene rutas no válidas.")
        z.extractall(nueva)
    os.remove(destino)
    for raiz, dirs, archivos in os.walk(nueva):
        if "Traductor de Pantalla.exe" in archivos:
            return raiz
    raise RuntimeError("El paquete no contiene el programa.")


_GUION = r"""
$ErrorActionPreference = 'Stop'
$base = '{base}'
$nueva = '{nueva}'
$exe = Join-Path $base 'Traductor de Pantalla.exe'
$datos = @({datos})
try {{ Wait-Process -Id {pid} -Timeout 90 }} catch {{}}
Start-Sleep -Milliseconds 700
$anterior = Join-Path $base '_anterior'
if (Test-Path -LiteralPath $anterior) {{ Remove-Item -LiteralPath $anterior -Recurse -Force }}
New-Item -ItemType Directory -Path $anterior | Out-Null
try {{
  Get-ChildItem -LiteralPath $base -Force | Where-Object {{ $datos -notcontains $_.Name }} |
    ForEach-Object {{ Move-Item -LiteralPath $_.FullName -Destination $anterior }}
  Get-ChildItem -LiteralPath $nueva -Force | Where-Object {{ $datos -notcontains $_.Name }} |
    ForEach-Object {{ Move-Item -LiteralPath $_.FullName -Destination $base }}
  if (-not (Test-Path -LiteralPath $exe)) {{ throw 'falta el programa nuevo' }}
}} catch {{
  Get-ChildItem -LiteralPath $base -Force | Where-Object {{ $datos -notcontains $_.Name }} |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
  Get-ChildItem -LiteralPath $anterior -Force |
    ForEach-Object {{ Move-Item -LiteralPath $_.FullName -Destination $base }}
}}
Remove-Item -LiteralPath (Join-Path $base '_actualizacion') -Recurse -Force -ErrorAction SilentlyContinue
$env:TRADUCTOR_REINICIO = '1'
Start-Process -FilePath $exe -WorkingDirectory $base
"""


def escribir_guion(carpeta_nueva, base=None, pid=None, nombre="traductor_instalar.ps1"):
    """El guion de PowerShell que cambia los archivos del programa por los nuevos."""
    base = base or nucleo.BASE
    comillas = lambda t: t.replace("'", "''")
    guion = _GUION.format(base=comillas(base), nueva=comillas(carpeta_nueva), pid=pid or os.getpid(),
                          datos=", ".join("'" + d + "'" for d in DATOS))
    ruta = os.path.join(tempfile.gettempdir(), nombre)
    with open(ruta, "w", encoding="utf-8-sig") as f:
        f.write(guion)
    return ruta


def instalar_paquete(carpeta_nueva, base=None):
    """Al cerrar el programa, cambia sus archivos por los nuevos (conservando tus datos) y lo
    vuelve a abrir. Si algo falla, deja la versión anterior tal cual estaba."""
    ruta = escribir_guion(carpeta_nueva, base)
    subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
                      "-File", ruta], creationflags=0x08000000 | 0x00000008, close_fds=True)   # sin ventana, aparte
    os._exit(0)


def limpiar_anterior(base=None):
    """Tras abrir bien la versión nueva, borra la copia de seguridad de la anterior."""
    shutil.rmtree(os.path.join(base or nucleo.BASE, "_anterior"), ignore_errors=True)


def reiniciar():
    """Vuelve a abrir el programa (para cargar el código nuevo)."""
    import subprocess
    os.environ["TRADUCTOR_REINICIO"] = "1"
    if getattr(sys, "frozen", False):
        subprocess.Popen([sys.executable], close_fds=True)
    else:
        subprocess.Popen([sys.executable] + sys.argv, close_fds=True)
    os._exit(0)
