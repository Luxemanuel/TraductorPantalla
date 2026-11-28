# -*- coding: utf-8 -*-
"""
«Grabar prueba»: graba lo que ve el traductor con sus traducciones encima.

Los rótulos no salen en las capturas ni en las grabaciones de Windows (así el traductor puede
seguir leyendo la pantalla debajo de ellos). Esta grabación pega los rótulos sobre cada captura,
tal como se ven, y lo guarda todo en un .zip: imágenes, qué decía cada rótulo y en qué momento,
y lo que fue haciendo el traductor (lecturas, comprobaciones…). Sirve para revisar errores.
"""
import io
import json
import os
import queue
import threading
import time
import zipfile

from PIL import Image

from nucleo import log

CUADROS_POR_SEG = 3
ANCHO = 1280
CALIDAD = 70
MAX_SEG = 300          # 5 minutos como mucho


def carpeta_pruebas():
    base = os.path.join(os.path.expanduser("~"), "Videos")
    if not os.path.isdir(base):
        base = os.path.expanduser("~")
    return os.path.join(base, "Traductor de Pantalla - pruebas")


class Grabadora:
    def __init__(self, carpeta=None):
        carpeta = carpeta or carpeta_pruebas()
        os.makedirs(carpeta, exist_ok=True)
        self.ruta = os.path.join(carpeta, time.strftime("prueba_%Y-%m-%d_%H-%M-%S.zip"))
        self._zip = zipfile.ZipFile(self.ruta, "w", zipfile.ZIP_STORED)
        self.t0 = time.time()
        self._ultimo = 0.0
        self._cola = queue.Queue(maxsize=8)
        self._indice = []
        self._eventos = []
        self._vistos = {}              # sesión -> hora del último de sus eventos ya guardado
        self.cuadros = 0
        self.perdidos = 0
        self.terminada = False
        self._hilo = threading.Thread(target=self._escribir, daemon=True)
        self._hilo.start()

    def segundos(self):
        return time.time() - self.t0

    def fuera_de_tiempo(self):
        return self.segundos() > MAX_SEG

    def cuadro(self, img, sesion):
        """Desde el hilo de la interfaz, con cada captura. Guarda unas pocas por segundo."""
        if self.terminada:
            return
        ahora = time.time()
        if ahora - self._ultimo < 1.0 / CUADROS_POR_SEG:
            return
        self._ultimo = ahora
        piezas, rotulos = [], []
        nat = getattr(sesion, "natural", None)
        ox, oy = sesion.origen()
        if nat is not None:
            por_id = {l.id: l for l in sesion.seguidor.lineas}
            for clave, v in nat.piezas.items():
                if v.img is None or v.saliendo or not nat.visible_:
                    continue
                piezas.append((v.img, int(v.x - ox), int(v.y - oy)))
                for i in clave.split("-"):
                    l = por_id.get(int(i)) if i.isdigit() else None
                    if l is not None:
                        rotulos.append({"id": l.id, "texto": l.texto, "trad": l.trad, "idioma": l.idioma,
                                        "caja": [int(c) for c in l.caja]})
        # lo que hizo el traductor desde el cuadro anterior
        desde = self._vistos.get(id(sesion), 0.0)
        nuevos = [e for e in list(sesion.eventos) if e[0] > desde]
        if nuevos:
            self._vistos[id(sesion)] = nuevos[-1][0]
        for t, que, lineas in nuevos:
            self._eventos.append((round(t - self.t0, 2), que, [getattr(l, "texto", str(l))[:80] for l in (lineas or [])]))
        try:
            self._cola.put_nowait((round(ahora - self.t0, 2), img, piezas, rotulos))
        except queue.Full:
            self.perdidos += 1           # el equipo va justo: mejor perder un cuadro que frenar el juego

    def _escribir(self):
        anterior = None
        while True:
            item = self._cola.get()
            if item is None:
                break
            t, img, piezas, rotulos = item
            try:
                comp = img.convert("RGBA")
                for im, x, y in piezas:
                    comp.alpha_composite(im.convert("RGBA"), (max(0, x), max(0, y)))
                comp = comp.convert("RGB")
                if comp.width > ANCHO:
                    comp = comp.resize((ANCHO, round(comp.height * ANCHO / comp.width)), Image.BILINEAR)
                peq = comp.resize((96, 54)).tobytes()
                if anterior is not None and peq == anterior[1]:
                    nombre = anterior[0]          # igual que el anterior: no se guarda otra vez
                else:
                    nombre = f"cuadros/{self.cuadros:05d}.jpg"
                    b = io.BytesIO()
                    comp.save(b, "JPEG", quality=CALIDAD)
                    self._zip.writestr(nombre, b.getvalue())
                    self.cuadros += 1
                    anterior = (nombre, peq)
                self._indice.append({"t": t, "cuadro": nombre, "escala": round(comp.width / img.width, 4),
                                     "rotulos": rotulos})
            except Exception:
                log.exception("Grabando la prueba")

    def terminar(self, info=None):
        """Cierra el .zip. Devuelve su ruta."""
        if self.terminada:
            return self.ruta
        self.terminada = True
        self._cola.put(None)
        self._hilo.join(timeout=30)
        try:
            datos = {"segundos": round(self.segundos(), 1), "cuadros": self.cuadros,
                     "perdidos": self.perdidos, **(info or {})}
            self._zip.writestr("info.json", json.dumps(datos, ensure_ascii=False, indent=1))
            self._zip.writestr("rotulos.jsonl", "\n".join(json.dumps(x, ensure_ascii=False) for x in self._indice))
            self._zip.writestr("eventos.txt", "\n".join(f"{t:8.2f}  {que}  {lineas if lineas else ''}"
                                                       for t, que, lineas in self._eventos))
            from nucleo import RUTA_REGISTRO
            if os.path.exists(RUTA_REGISTRO):
                with open(RUTA_REGISTRO, "rb") as f:
                    f.seek(max(0, os.path.getsize(RUTA_REGISTRO) - 200_000))
                    self._zip.writestr("registro.txt", f.read())
            self._zip.close()
        except Exception:
            log.exception("Cerrando la grabación de prueba")
        return self.ruta
