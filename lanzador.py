# -*- coding: utf-8 -*-
"""
Lanzador del Traductor de Pantalla (lo que se convierte en el .exe).

La "base" (este .exe + librerías + modelos) cambia poco. El código del programa vive en
la carpeta "app" junto al .exe: las actualizaciones ligeras solo reemplazan esa carpeta.
Si "app" no existe, se usa la copia del código que viene dentro del .exe.
"""
import os
import sys

VERSION_BASE = 4

if getattr(sys, "frozen", False):
    _base = os.path.dirname(sys.executable)
else:
    _base = os.path.dirname(os.path.abspath(__file__))
_app = os.path.join(_base, "app")
if os.path.isfile(os.path.join(_app, "traductor_pantalla.py")):
    sys.path.insert(0, _app)
os.environ["TRADUCTOR_VERSION_BASE"] = str(VERSION_BASE)

import traductor_pantalla  # noqa: E402

if __name__ == "__main__":
    traductor_pantalla.main()
