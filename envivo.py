# -*- coding: utf-8 -*-
"""
Traducción EN VIVO:
  · CapaEnVivo: ventana transparente encima de todo que deja pasar los clics y el
    teclado (puedes seguir usando la aplicación de debajo) y que Windows excluye de
    las capturas de pantalla (así el programa no se lee a sí mismo).
  · SesionEnVivo: vigila una zona (o la pantalla) varias veces por segundo y solo
    vuelve a leer/traducir cuando el contenido cambió y se quedó quieto.
"""
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont

from PIL import Image, ImageChops, ImageStat

import nucleo
from nucleo import ESQUEMAS, FUENTES, Cancelado, log

ES_WINDOWS = sys.platform.startswith("win")
CLAVE = "#010203"          # color que Windows vuelve 100 % transparente


def _hex(rgb):
    return "#%02x%02x%02x" % tuple(rgb)


# ============================================================================
# Utilidades de Windows
# ============================================================================
def hwnd_de(win):
    win.update_idletasks()
    try:
        return int(win.wm_frame(), 16)
    except Exception:
        import ctypes
        return ctypes.windll.user32.GetParent(win.winfo_id())


def hacer_traspasable(win):
    """Los clics y el teclado atraviesan la ventana y nunca le roba el foco a nadie."""
    if not ES_WINDOWS:
        return False
    try:
        import ctypes
        GWL_EXSTYLE = -20
        WS_EX_LAYERED, WS_EX_TRANSPARENT = 0x00080000, 0x00000020
        WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE = 0x00000080, 0x08000000
        u32 = ctypes.windll.user32
        h = hwnd_de(win)
        estilo = u32.GetWindowLongW(h, GWL_EXSTYLE)
        u32.SetWindowLongW(h, GWL_EXSTYLE, estilo | WS_EX_LAYERED | WS_EX_TRANSPARENT
                           | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE)
        return True
    except Exception:
        log.exception("No se pudo hacer traspasable la capa")
        return False


def excluir_de_captura(win):
    """La ventana se ve en el monitor pero no aparece en capturas (Windows 10 2004+)."""
    if not ES_WINDOWS:
        return False
    try:
        import ctypes
        WDA_EXCLUDEFROMCAPTURE = 0x11
        return bool(ctypes.windll.user32.SetWindowDisplayAffinity(hwnd_de(win), WDA_EXCLUDEFROMCAPTURE))
    except Exception:
        log.exception("SetWindowDisplayAffinity")
        return False


# ============================================================================
# Detección de cambios
# ============================================================================
def miniatura(img, ancho=480):
    g = img.convert("L")
    if g.width > ancho:
        g = g.resize((ancho, max(1, round(g.height * ancho / g.width))), Image.BILINEAR)
    return g


_LUT = [255 if p > 28 else 0 for p in range(256)]


def diferencia(a, b):
    """Fracción (0-1) de píxeles que cambiaron de verdad entre dos miniaturas."""
    if a is None or b is None or a.size != b.size:
        return 1.0
    return ImageStat.Stat(ImageChops.difference(a, b).point(_LUT)).mean[0] / 255


# ============================================================================
# Capa transparente con las traducciones
# ============================================================================
class CapaEnVivo:
    def __init__(self, app, mon, caja):
        self.app = app
        self.mon = mon
        self.caja = caja                    # relativa al monitor, o None = pantalla completa
        self._fuentes = {}
        w = self.win = tk.Toplevel(app.root)
        w.withdraw()
        w.overrideredirect(True)
        w.geometry(f"{mon['width']}x{mon['height']}+{mon['left']}+{mon['top']}")
        w.configure(bg=CLAVE)
        w.attributes("-topmost", True)
        if ES_WINDOWS:
            w.attributes("-transparentcolor", CLAVE)
        self._alfa = max(0.3, min(1.0, app.cfg["opacidad"] / 100))
        try:
            w.attributes("-alpha", self._alfa)
        except tk.TclError:
            pass
        self.c = tk.Canvas(w, bg=CLAVE, highlightthickness=0, bd=0)
        self.c.pack(fill="both", expand=True)
        w.deiconify()
        w.update_idletasks()
        self.traspasable = hacer_traspasable(w)
        self.excluida = excluir_de_captura(w)
        self.dibujar_marco()

    def existe(self):
        try:
            return self.win.winfo_exists()
        except tk.TclError:
            return False

    # ---------- dibujo ----------
    def _fuente(self, familia, tam, peso):
        clave = (familia, tam, peso)
        if clave not in self._fuentes:
            self._fuentes[clave] = tkfont.Font(family=familia, size=-tam, weight=peso)
        return self._fuentes[clave]

    def dibujar_marco(self, solo_texto=None):
        c = self.c
        c.delete("marco")
        aviso = "● EN VIVO  ·  Ctrl+Alt+X para detener"
        f = self._fuente("Segoe UI" if ES_WINDOWS else "Arial", 13, "bold")
        if self.caja:
            x0, y0, x1, y1 = self.caja
            c.create_rectangle(x0 - 3, y0 - 3, x1 + 3, y1 + 3, outline="#ffd400", width=2,
                               dash=(8, 5), tags="marco")
            tx, ty = x0 - 3, max(0, y0 - 26)
        else:
            tx, ty = self.mon["width"] - f.measure(aviso) - 30, 6
        ancho = f.measure(aviso) + 16
        c.create_rectangle(tx, ty, tx + ancho, ty + 22, fill="#1a1a1a", outline="#ffd400", tags="marco")
        c.create_text(tx + 8, ty + 11, text=aviso, anchor="w", font=f, fill="#ff5a5a", tags="marco")
        if solo_texto:
            c.itemconfigure("marco", state="normal")

    def limpiar(self):
        self.c.delete("trad")

    def mostrar(self, bloques):
        """bloques con 'caja' en coordenadas del monitor."""
        cfg = self.app.cfg
        _, fondo, color = ESQUEMAS.get(cfg["esquema"], ESQUEMAS["blanco_negro"])
        familia = FUENTES.get(cfg["fuente"], FUENTES["Arial"])[2]
        peso = "bold" if cfg["negrita"] else "normal"
        escala = max(50, cfg["escala_texto"]) / 100
        minimo = max(8, int(cfg["letra_minima"]))
        W, H = self.mon["width"], self.mon["height"]
        alfa = max(0.3, min(1.0, cfg["opacidad"] / 100))
        if alfa != self._alfa:
            self._alfa = alfa
            try:
                self.win.attributes("-alpha", alfa)
            except tk.TclError:
                pass
        c = self.c
        c.delete("trad")
        colocados = []
        for b in sorted(bloques, key=lambda b: (b["caja"][1], b["caja"][0])):
            x0, y0, x1, y1 = b["caja"]
            texto = b["traduccion"]
            ancho = max(40, x1 - x0) * max(1.0, escala)
            alto = max(12, y1 - y0)
            tam = max(minimo, int(min(alto * 0.8, 48) * escala))
            while True:
                f = self._fuente(familia, tam, peso)
                lineas = _envolver(texto, f, ancho)
                alto_linea = f.metrics("linespace")
                total = alto_linea * len(lineas)
                if total <= alto * max(1.0, escala) or tam <= minimo:
                    break
                tam -= 1
            alto_final = max(alto, total)
            ancho_final = max(x1 - x0, max(f.measure(ln) for ln in lineas))
            if x0 + ancho_final > W - 4:
                x0 = max(4, W - ancho_final - 8)
            rect = [x0 - 5, y0 - 3, x0 + ancho_final + 5, y0 + alto_final + 3]
            for _ in range(30):                      # evitar que se pisen
                choque = next((k for k in colocados if rect[0] < k[2] and rect[2] > k[0]
                               and rect[1] < k[3] and rect[3] > k[1]), None)
                if not choque:
                    break
                dy = choque[3] + 2 - rect[1]
                rect[1] += dy
                rect[3] += dy
            colocados.append(rect)
            if rect[1] > H:
                continue
            c.create_rectangle(*rect, fill=_hex(fondo), outline="", tags="trad")
            c.create_text(rect[0] + 5, rect[1] + 3 + (alto_final - total) / 2, anchor="nw",
                          text="\n".join(lineas), font=f, fill=_hex(color), tags="trad")
        c.tag_raise("marco")

    # ---------- captura sin la capa (solo si Windows no la excluye) ----------
    def esconder_un_momento(self):
        try:
            self.win.attributes("-alpha", 0.0)
            self.win.update_idletasks()
            time.sleep(0.03)
        except tk.TclError:
            pass

    def reaparecer(self):
        try:
            self.win.attributes("-alpha", self._alfa)
        except tk.TclError:
            pass

    def cerrar(self):
        if self.existe():
            self.win.destroy()


def _envolver(texto, fuente, ancho):
    palabras, lineas, actual = texto.split(), [], ""
    for p in palabras:
        prueba = (actual + " " + p).strip()
        if fuente.measure(prueba) <= ancho or not actual:
            actual = prueba
        else:
            lineas.append(actual)
            actual = p
    if actual:
        lineas.append(actual)
    return lineas or [""]


# ============================================================================
# Sesión en vivo
# ============================================================================
class SesionEnVivo:
    def __init__(self, app, mon, caja, capturar=None):
        import mss
        self.app = app
        self.mon = mon
        self.caja = caja
        self.tipo = "zona" if caja else "pantalla"
        if caja:
            x0, y0, x1, y1 = caja
            self.region = {"left": mon["left"] + x0, "top": mon["top"] + y0,
                           "width": x1 - x0, "height": y1 - y0}
        else:
            self.region = dict(mon)
        # umbrales: fracción de píxeles cambiados
        self.umbral = 0.002 if caja else 0.0008
        self.intervalo = 300 if caja else 600
        self.activa = True
        self.procesando = False
        self.ref = None
        self.prev = None
        self.cambiando_desde = None
        self.ultimo_lanzamiento = 0
        self.bloques = []
        self.firma = None
        self.ultima_duracion = None
        self.lecturas = 0
        self._capturar_ext = capturar          # para pruebas
        self._sct = None if capturar else mss.mss()
        self.capa = CapaEnVivo(app, mon, caja)
        app.root.after(30, self._tick)

    # ---------- captura ----------
    def _capturar(self):
        if self._capturar_ext:
            return self._capturar_ext()
        if not self.capa.excluida:
            self.capa.esconder_un_momento()
        try:
            shot = self._sct.grab(self.region)
        finally:
            if not self.capa.excluida:
                self.capa.reaparecer()
        img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
        tam = (self.region["width"], self.region["height"])
        return img if img.size == tam else img.resize(tam, Image.LANCZOS)

    # ---------- bucle ----------
    def _tick(self):
        if not self.activa:
            return
        try:
            if not self.procesando:
                img = self._capturar()
                peq = miniatura(img)
                ahora = time.time()
                cambio = diferencia(peq, self.ref)
                quieto = diferencia(peq, self.prev) < self.umbral * 0.4
                self.prev = peq
                if self.ref is None:
                    self._lanzar(img, peq)
                elif cambio > self.umbral:
                    if self.cambiando_desde is None:
                        self.cambiando_desde = ahora
                    if cambio > 0.25 and self.bloques:     # cambió casi todo: quitar lo viejo
                        self.capa.limpiar()
                        self.firma = None
                    sigue_cambiando = ahora - self.cambiando_desde > 2.0
                    if (quieto or sigue_cambiando) and ahora - self.ultimo_lanzamiento > 0.4:
                        self._lanzar(img, peq)
                else:
                    self.cambiando_desde = None
        except Exception:
            log.exception("Bucle en vivo")
        self.app.root.after(self.intervalo, self._tick)

    def _lanzar(self, img, peq):
        self.procesando = True
        self.ref = peq
        self.cambiando_desde = None
        self.ultimo_lanzamiento = time.time()
        cfg = self.app.cfg
        modo, omitir = cfg["idioma_origen"], cfg["omitir_espanol"]

        def tarea():
            t0 = time.time()
            try:
                bloques = self.app.motor.procesar(img, modo, omitir, seguir=lambda: self.activa)
                self.app.cola.put(("vivo", (self, bloques, time.time() - t0)))
            except Cancelado:
                pass
            except Exception as e:
                log.exception("Lectura en vivo")
                self.app.cola.put(("vivo_error", (self, str(e))))
        threading.Thread(target=tarea, daemon=True).start()

    def resultado(self, bloques, duracion):
        """Llamado en el hilo de la interfaz. Devuelve las frases nuevas."""
        self.procesando = False
        if not self.activa:
            return []
        if self.caja:
            dx, dy = self.caja[0], self.caja[1]
            for b in bloques:
                a, b_, c_, d = b["caja"]
                b["caja"] = (a + dx, b_ + dy, c_ + dx, d + dy)
        self.ultima_duracion = duracion
        self.lecturas += 1
        anteriores = {x["original"] for x in self.bloques}
        nuevos = [b for b in bloques if b["original"] not in anteriores]
        firma = [(b["caja"], b["traduccion"]) for b in bloques]
        if self.app.cfg["modo_vista"] == "panel":
            self.capa.limpiar()
        elif firma != self.firma:
            self.capa.mostrar(bloques)
        self.firma = firma
        self.bloques = bloques
        return nuevos

    def error(self):
        self.procesando = False

    def detener(self):
        self.activa = False
        self.capa.cerrar()
        if self._sct is not None:
            try:
                self._sct.close()
            except Exception:
                pass
