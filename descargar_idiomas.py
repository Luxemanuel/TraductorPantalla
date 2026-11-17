# -*- coding: utf-8 -*-
"""
Descarga los diccionarios SIN INTERNET (respaldo cuando no hay conexión):
inglés→español y japonés→inglés (japonés→español pasa por el inglés).
Para otros idiomas de destino el programa los descarga solo cuando los eliges.
El lector de texto es el de Windows: no hay que descargar nada para él.
"""
import os

import nucleo
from traduccion import ArgosLite


def carpeta_argos():
    return os.environ.get("ARGOS_PACKAGES_DIR") or os.path.join(nucleo.RUTA_MODELOS, "argos")


def descargar_traductores(avisar=print, destino="es"):
    argos = ArgosLite(carpeta_argos())
    ok = True
    for origen in ("en", "ja"):
        if not argos.descargar(origen, destino, avisar):
            ok = False
    return ok


def probar(avisar=print):
    argos = ArgosLite(carpeta_argos())
    for origen, frase in (("en", "Welcome to the village, traveler."), ("ja", "ありがとうございます。")):
        try:
            avisar(f"  Prueba sin internet {origen}->es:  {frase}  =>  {argos.lote([frase], origen, 'es')[0]}")
        except Exception as e:
            avisar(f"  ✗ Prueba {origen}->es falló: {e}")


if __name__ == "__main__":
    descargar_traductores()
    probar()
    print("\nListo.")
