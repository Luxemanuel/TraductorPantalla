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


def excluir_de_captura(win, excluir=True):
    """La ventana se ve en el monitor pero no aparece en capturas (Windows 10 2004+).
    Con excluir=False vuelve a la normalidad."""
    if not ES_WINDOWS:
        return False
    try:
        import ctypes
        WDA_NONE, WDA_EXCLUDEFROMCAPTURE = 0x0, 0x11
        modo = WDA_EXCLUDEFROMCAPTURE if excluir else WDA_NONE
        return bool(ctypes.windll.user32.SetWindowDisplayAffinity(hwnd_de(win), modo))
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
        self.excluida = False if getattr(app, "sin_exclusion", False) else excluir_de_captura(w)
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
        aviso = getattr(self, "aviso", "") or "● EN VIVO  ·  Ctrl+Alt+X para detener"
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

    def mover(self, rect):
        """Sigue a la ventana anclada cuando se mueve o cambia de tamaño."""
        if (rect["left"], rect["top"], rect["width"], rect["height"]) == \
                (self.mon["left"], self.mon["top"], self.mon["width"], self.mon["height"]):
            return False
        self.mon = dict(rect)
        self.win.geometry(f"{rect['width']}x{rect['height']}+{rect['left']}+{rect['top']}")
        self.dibujar_marco()
        return True

    def visible(self, si):
        """Ocultar/mostrar sin destruir la capa (al cambiar a otro programa)."""
        if si == getattr(self, "_visible", True):
            return
        self._visible = si
        try:
            self.win.attributes("-alpha", self._alfa if si else 0.0)
        except tk.TclError:
            pass

    def subir(self):
        """Mantiene la capa por encima de todo (sin quitarle el foco a nadie)."""
        if not ES_WINDOWS or not self.existe():
            return
        try:
            import ctypes
            HWND_TOPMOST, SWP = -1, 0x0001 | 0x0002 | 0x0010   # NOSIZE|NOMOVE|NOACTIVATE
            ctypes.windll.user32.SetWindowPos(ctypes.c_void_p(hwnd_de(self.win)),
                                              ctypes.c_void_p(HWND_TOPMOST), 0, 0, 0, 0, SWP)
        except Exception:
            pass

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
            ancho_final = max(x1 - x0, max(f.measure(ln) for ln in lineas)) + int(alto * 0.2)
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

    def mostrar_barra(self, bloques):
        """Modo subtítulos: toda la traducción junta, grande y centrada, tapando la zona."""
        self.c.delete("trad")
        if not bloques or not self.caja:
            return
        cfg = self.app.cfg
        _, fondo, color = ESQUEMAS.get(cfg["esquema"], ESQUEMAS["blanco_negro"])
        familia = FUENTES.get(cfg["fuente"], FUENTES["Arial"])[2]
        x0, y0, x1, y1 = self.caja
        ancho = max(80, x1 - x0 - 20)
        f = self._fuente(familia, int(cfg.get("tam_subtitulo", 30)), "bold" if cfg["negrita"] else "normal")
        lineas = _envolver(" ".join(b["traduccion"] for b in bloques), f, ancho)
        alto_txt = f.metrics("linespace") * len(lineas)
        orig_txt, alto_orig, fo = "", 0, None
        if cfg.get("subtitulo_original", True):
            fo = self._fuente(familia, max(11, int(cfg.get("tam_subtitulo", 30) * 0.5)), "normal")
            orig = _envolver(" ".join(b["original"] for b in bloques), fo, ancho)[:3]
            orig_txt, alto_orig = "\n".join(orig), fo.metrics("linespace") * len(orig) + 4
        alto = max(y1 - y0, alto_txt + alto_orig + 12)
        cy = (y0 + y1) / 2
        top = max(0, cy - alto / 2)
        self.c.create_rectangle(x0, top, x1, top + alto, fill=_hex(fondo), outline="", tags="trad")
        y = top + (alto - alto_txt - alto_orig) / 2
        if orig_txt:
            self.c.create_text((x0 + x1) / 2, y, anchor="n", text=orig_txt, font=fo, justify="center",
                               fill=_hex(tuple((a + b) // 2 for a, b in zip(color, fondo))), tags="trad")
            y += alto_orig
        self.c.create_text((x0 + x1) / 2, y, anchor="n", text="\n".join(lineas), font=f,
                           justify="center", fill=_hex(color), tags="trad")
        self.c.tag_raise("marco")

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
            self.win.attributes("-alpha", self._alfa if getattr(self, "_visible", True) else 0.0)
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
# ============================================================================
# Ventanas de Windows (para anclar el traductor a un programa)
# ============================================================================
def ventana_activa():
    if not ES_WINDOWS:
        return None
    import ctypes
    return ctypes.windll.user32.GetForegroundWindow() or None


def titulo_ventana(hwnd):
    import ctypes
    u32 = ctypes.windll.user32
    n = u32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    u32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value or "programa"


def rect_cliente(hwnd):
    """Área útil de la ventana (sin bordes ni barra de título) en coordenadas de pantalla."""
    import ctypes
    import ctypes.wintypes as wt
    u32 = ctypes.windll.user32
    if not u32.IsWindow(hwnd):
        return None
    rc = wt.RECT()
    if not u32.GetClientRect(hwnd, ctypes.byref(rc)):
        return None
    p = wt.POINT(0, 0)
    u32.ClientToScreen(hwnd, ctypes.byref(p))
    if rc.right <= 0 or rc.bottom <= 0:
        return None
    return {"left": p.x, "top": p.y, "width": rc.right, "height": rc.bottom}


def estado_ventana(hwnd, propias):
    """'cerrada', 'oculta' (minimizada u otro programa delante) o 'visible'."""
    import ctypes
    u32 = ctypes.windll.user32
    if not u32.IsWindow(hwnd):
        return "cerrada"
    if u32.IsIconic(hwnd):
        return "oculta"
    fg = u32.GetForegroundWindow()
    if fg == hwnd or fg in propias or u32.GetAncestor(fg, 3) == hwnd:     # 3 = GA_ROOTOWNER
        return "visible"
    return "oculta"


# ============================================================================
# Seguidor de texto: recuerda cada línea y su traducción
# ============================================================================
import difflib
import itertools
import re as _re

_contador = itertools.count(1)


def _n(t):
    return _re.sub(r"[\s。、.,!?！？「」\"'…・:：;；()（）\-]", "", t.lower())


class Linea:
    __slots__ = ("id", "texto", "idioma", "caja", "trad", "trad_de", "visto", "fallos",
                 "confirmada", "candidato", "conf_cand", "nueva")

    def __init__(self, p, ahora):
        self.id = next(_contador)
        self.texto, self.idioma, self.caja = p["t"], p["idioma"], p["caja"]
        self.trad = None            # traducción que se muestra
        self.trad_de = None         # texto que se tradujo (para saber si hay que retraducir)
        self.visto = ahora
        self.fallos = 0
        self.confirmada = False     # vista 2 veces seguidas con el mismo texto
        self.candidato, self.conf_cand = None, 0
        self.nueva = True


class Seguidor:
    """Empareja lo que lee el lector en cada vistazo con las líneas que ya conoce:
      · la misma línea (aunque se mueva, cambie el fondo o se lea una letra distinta)
        conserva su traducción y no se vuelve a traducir;
      · si una parte queda tapada (un kanji), se mantiene la versión completa conocida;
      · si aparece una versión más completa (texto que termina de escribirse), se adopta
        cuando se confirma dos veces;
      · una línea desaparece solo cuando deja de verse un rato."""

    def __init__(self, gracia=0.6):
        self.lineas = []
        self.gracia = gracia

    def actualizar(self, pars, ahora):
        libres = list(self.lineas)
        vistas = set()
        for p in pars:
            np_ = _n(p["t"])
            if not np_:
                continue
            mejor, puntos = None, 0.0
            for l in libres:
                if l.idioma != p["idioma"]:
                    continue
                nl = _n(l.texto)
                sim = difflib.SequenceMatcher(None, np_, nl, autojunk=False).ratio()
                # también vale si una es parte de la otra (texto tapado o a medio escribir)
                if np_ in nl or nl in np_:
                    sim = max(sim, 0.75)
                cx, cy = (p["caja"][0] + p["caja"][2]) / 2, (p["caja"][1] + p["caja"][3]) / 2
                lx, ly = (l.caja[0] + l.caja[2]) / 2, (l.caja[1] + l.caja[3]) / 2
                alto = max(10, l.caja[3] - l.caja[1])
                cerca = abs(cy - ly) < alto * 1.2 and abs(cx - lx) < (l.caja[2] - l.caja[0]) * 0.8 + alto
                total = sim + (0.15 if cerca else 0)
                if sim >= 0.55 and total > puntos:
                    mejor, puntos = l, total
            if mejor is None:
                l = Linea(p, ahora)
                self.lineas.append(l)
                vistas.add(l.id)
                continue
            libres.remove(mejor)
            vistas.add(mejor.id)
            l = mejor
            if max(abs(a - b) for a, b in zip(p["caja"], l.caja)) > 3:   # ignorar temblores de 1-3 px
                l.caja = p["caja"]
            l.visto, l.fallos = ahora, 0
            nl = _n(l.texto)
            if not l.confirmada:
                # primera confirmación: el texto debe repetirse casi igual
                if difflib.SequenceMatcher(None, np_, nl, autojunk=False).ratio() >= 0.9:
                    l.confirmada = True
                    if len(np_) > len(nl):
                        l.texto = p["t"]
                else:
                    l.texto = p["t"]       # todavía se está escribiendo / estabilizando
            elif np_ != nl and len(np_) >= len(nl) + 2:
                # versión más completa: adoptarla si se ve dos veces
                if l.candidato is not None and _n(l.candidato) == np_:
                    l.conf_cand += 1
                else:
                    l.candidato, l.conf_cand = p["t"], 1
                if l.conf_cand >= 2:
                    l.texto, l.candidato, l.conf_cand = p["t"], None, 0
            # si es igual o más corta (letra tapada / mal leída): se conserva la conocida
        quitadas = False
        for l in list(self.lineas):
            if l.id not in vistas:
                l.fallos += 1
                if ahora - l.visto > self.gracia and l.fallos >= 2:
                    self.lineas.remove(l)
                    quitadas = True
        return quitadas

    def necesita_mirar(self):
        """Hay algo por confirmar (línea nueva, versión más completa o línea que se fue)."""
        return any(l.fallos > 0 or not l.confirmada or l.candidato for l in self.lineas)

    def pendientes(self):
        """Líneas confirmadas que hay que (re)traducir."""
        return [l for l in self.lineas if l.confirmada and l.trad_de != l.texto]

    def asignar(self, lineas, traducciones):
        nuevas = []
        for l, t in zip(lineas, traducciones):
            t = (t or "").strip()
            l.trad_de = l.texto
            if t and t.lower() != l.texto.lower():
                if l.nueva:
                    nuevas.append(l)
                    l.nueva = False
                l.trad = t
        return nuevas

    def visibles(self, dx=0, dy=0):
        return [{"id": l.id, "caja": (l.caja[0] + dx, l.caja[1] + dy, l.caja[2] + dx, l.caja[3] + dy),
                 "original": l.texto, "traduccion": l.trad, "idioma": l.idioma}
                for l in self.lineas if l.trad]


# ============================================================================
# Sesión en vivo
# ============================================================================
class SesionEnVivo:
    """Vigila una zona (de la pantalla o de un programa anclado) y mantiene la traducción.

    mon:    rectángulo donde se dibuja (monitor, o área de la ventana anclada)
    caja:   zona dentro de 'mon' (None = todo)
    ventana: hwnd del programa anclado (None = no anclado)"""

    def __init__(self, app, mon, caja, capturar=None, estilo="palabras", ventana=None):
        import mss
        self.app = app
        self.mon = dict(mon)
        self.caja = caja
        self.estilo = estilo                  # "palabras" (sobre cada línea) | "barra" (subtítulos)
        self.ventana = ventana
        self.tipo = "subtitulos" if estilo == "barra" else ("zona" if caja else "pantalla")
        self.umbral = 0.002 if caja else 0.0008
        self.intervalo = 100 if estilo == "barra" else 150
        self.seguidor = Seguidor(gracia=0.3 if estilo == "barra" else 0.6)
        self.activa = True
        self.procesando = False
        self.ref = None
        self.ultimo_lanzamiento = 0
        self.bloques = []
        self.firma = None
        self.ultima_duracion = None
        self.lecturas = 0
        self.oculta = False
        self._capturar_ext = capturar          # para pruebas
        self._sct = None if capturar else mss.mss()
        self.capa = CapaEnVivo(app, self.mon, caja)
        if ventana:
            self.titulo = titulo_ventana(ventana)
            self.capa.aviso = f"● EN VIVO: {self.titulo[:40]}  ·  Ctrl+Alt+X detener"
            self.capa.dibujar_marco()
        app.root.after(30, self._tick)

    # ---------- región a capturar ----------
    def region(self):
        if self.caja:
            x0, y0, x1, y1 = self.caja
            return {"left": self.mon["left"] + int(x0), "top": self.mon["top"] + int(y0),
                    "width": int(x1 - x0), "height": int(y1 - y0)}
        return dict(self.mon)

    def _capturar(self):
        if self._capturar_ext:
            return self._capturar_ext()
        reg = self.region()
        if not self.capa.excluida:
            self.capa.esconder_un_momento()
        try:
            shot = self._sct.grab(reg)
        finally:
            if not self.capa.excluida:
                self.capa.reaparecer()
        img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
        tam = (reg["width"], reg["height"])
        return img if img.size == tam else img.resize(tam, Image.LANCZOS)

    # ---------- programa anclado ----------
    def _seguir_ventana(self):
        """Devuelve False si no hay que leer ahora (programa oculto o cerrado)."""
        if not self.ventana:
            return True
        propias = {hwnd_de(w) for w in self.app._mis_ventanas()}
        estado = estado_ventana(self.ventana, propias)
        if estado == "cerrada":
            self.app.cola.put(("vivo_cerrada", self))
            self.activa = False
            return False
        if estado == "oculta":
            if not self.oculta:
                self.oculta = True
                self.capa.visible(False)     # se esconde, pero recuerda todo lo traducido
            return False
        rect = rect_cliente(self.ventana)
        if rect is None:
            return False
        if self.capa.mover(rect):
            self.mon = rect
            self.ref = None                  # se movió: volver a mirar
        if self.oculta:
            self.oculta = False
            self.capa.visible(True)          # al volver, todo sigue traducido
            self.ref = None
        return True

    # ---------- bucle ----------
    def _tick(self):
        if not self.activa:
            return
        try:
            self._ticks = getattr(self, "_ticks", 0) + 1
            visible = self._seguir_ventana()
            if visible and self._ticks % max(1, 2000 // self.intervalo) == 0:
                self.capa.subir()
            if visible and not self.procesando and time.time() - self.ultimo_lanzamiento > 0.12:
                img = self._capturar()
                peq = miniatura(img)
                # Si la imagen no cambió nada, no hace falta leer. Si cambió (aunque sea un
                # fondo animado), se lee el TEXTO: lo que ya estaba traducido se conserva.
                if (self.ref is None or diferencia(peq, self.ref) > self.umbral * 0.5
                        or self.seguidor.necesita_mirar()):
                    self._lanzar(img, peq)
        except Exception:
            log.exception("Bucle en vivo")
        if self.activa:
            self.app.root.after(self.intervalo, self._tick)

    def _lanzar(self, img, peq):
        self.procesando = True
        self.ref = peq
        self.ultimo_lanzamiento = time.time()
        cfg = self.app.cfg
        modo, omitir = cfg["idioma_origen"], cfg["omitir_espanol"]
        motor = self.app.motor

        def tarea():
            t0 = time.perf_counter()
            try:
                pars = motor.leer_parrafos(img, modo, omitir, seguir=lambda: self.activa)
                t1 = time.perf_counter()
                quitadas = self.seguidor.actualizar(pars, time.time())
                pend = self.seguidor.pendientes()
                nuevas = []
                if pend:
                    trads = motor.traducir_lote([(l.texto, l.idioma) for l in pend])
                    nuevas = self.seguidor.asignar(pend, trads)
                dx, dy = (self.caja[0], self.caja[1]) if self.caja else (0, 0)
                vis = self.seguidor.visibles(dx, dy)
                nuevos = [b for b in vis if b["id"] in {l.id for l in nuevas}]
                motor.tiempos = (t1 - t0, time.perf_counter() - t1)
                self.app.cola.put(("vivo", (self, vis, nuevos, time.perf_counter() - t0)))
            except Cancelado:
                self.procesando = False
            except Exception as e:
                log.exception("Lectura en vivo")
                self.app.cola.put(("vivo_error", (self, str(e))))
        threading.Thread(target=tarea, daemon=True).start()

    def resultado(self, bloques, nuevos, duracion):
        """Llamado en el hilo de la interfaz con las líneas que siguen visibles."""
        self.procesando = False
        if not self.activa:
            return []
        self.ultima_duracion = duracion
        self.lecturas += 1
        firma = [(b["id"], tuple(int(v) for v in b["caja"]), b["traduccion"]) for b in bloques]
        if firma != self.firma:
            if self.app.cfg["modo_vista"] == "panel" and self.estilo != "barra":
                self.capa.limpiar()
            elif self.estilo == "barra":
                self.capa.mostrar_barra(bloques)
            else:
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


def capturar_ventana(win, region):
    """Copia el contenido de UNA ventana (aunque haya otras encima) y recorta 'region'
    (coordenadas de pantalla). Se usa en la demostración grabada."""
    import ctypes
    import ctypes.wintypes as wt
    u32, g32 = ctypes.windll.user32, ctypes.windll.gdi32
    vp = ctypes.c_void_p
    u32.GetWindowDC.restype = vp
    u32.GetWindowDC.argtypes = [vp]
    u32.ReleaseDC.argtypes = [vp, vp]
    u32.PrintWindow.argtypes = [vp, vp, ctypes.c_uint]
    u32.GetWindowRect.argtypes = [vp, ctypes.POINTER(wt.RECT)]
    g32.CreateCompatibleDC.restype = vp
    g32.CreateCompatibleDC.argtypes = [vp]
    g32.CreateCompatibleBitmap.restype = vp
    g32.CreateCompatibleBitmap.argtypes = [vp, ctypes.c_int, ctypes.c_int]
    g32.SelectObject.restype = vp
    g32.SelectObject.argtypes = [vp, vp]
    g32.DeleteObject.argtypes = [vp]
    g32.DeleteDC.argtypes = [vp]
    g32.GetDIBits.argtypes = [vp, vp, ctypes.c_uint, ctypes.c_uint, vp, vp, ctypes.c_uint]

    class BIH(ctypes.Structure):
        _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                    ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                    ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG),
                    ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD)]
    hwnd = hwnd_de(win)
    rc = wt.RECT()
    u32.GetWindowRect(hwnd, ctypes.byref(rc))
    w, h = rc.right - rc.left, rc.bottom - rc.top
    hdc = u32.GetWindowDC(hwnd)
    mdc = g32.CreateCompatibleDC(hdc)
    bmp = g32.CreateCompatibleBitmap(hdc, w, h)
    viejo = g32.SelectObject(mdc, bmp)
    u32.PrintWindow(hwnd, mdc, 2)
    bi = BIH()
    bi.biSize, bi.biWidth, bi.biHeight, bi.biPlanes, bi.biBitCount = ctypes.sizeof(BIH), w, -h, 1, 32
    buf = ctypes.create_string_buffer(w * h * 4)
    g32.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bi), 0)
    g32.SelectObject(mdc, viejo)
    g32.DeleteObject(bmp)
    g32.DeleteDC(mdc)
    u32.ReleaseDC(hwnd, hdc)
    img = Image.frombuffer("RGB", (w, h), buf, "raw", "BGRX", 0, 1)
    x0, y0 = region["left"] - rc.left, region["top"] - rc.top
    return img.crop((x0, y0, x0 + region["width"], y0 + region["height"]))


# ============================================================================
# Barra de subtítulos (para la voz)
# ============================================================================
class BarraSubtitulos:
    """Franja transparente abajo de la pantalla con el último subtítulo traducido.
    Deja pasar los clics y no sale en las capturas."""

    def __init__(self, app, mon):
        self.app = app
        self.mon = mon
        self._fuentes = {}
        self._borrar = None
        alto = 260
        w = self.win = tk.Toplevel(app.root)
        w.withdraw()
        w.overrideredirect(True)
        w.geometry(f"{mon['width']}x{alto}+{mon['left']}+{mon['top'] + mon['height'] - alto - 60}")
        w.configure(bg=CLAVE)
        w.attributes("-topmost", True)
        if ES_WINDOWS:
            w.attributes("-transparentcolor", CLAVE)
        try:
            w.attributes("-alpha", max(0.3, min(1.0, app.cfg["opacidad"] / 100)))
        except tk.TclError:
            pass
        self.alto = alto
        self.c = tk.Canvas(w, bg=CLAVE, highlightthickness=0, bd=0)
        self.c.pack(fill="both", expand=True)
        w.deiconify()
        w.update_idletasks()
        hacer_traspasable(w)
        excluir_de_captura(w)
        self.estado("● Subtítulos de voz activos — escuchando…")

    def _fuente(self, familia, tam, peso):
        clave = (familia, tam, peso)
        if clave not in self._fuentes:
            self._fuentes[clave] = tkfont.Font(family=familia, size=-tam, weight=peso)
        return self._fuentes[clave]

    def estado(self, texto):
        """Aviso pequeño (cargando, escuchando…)."""
        self.c.delete("estado")
        f = self._fuente("Segoe UI" if ES_WINDOWS else "Arial", 13, "bold")
        x = self.mon["width"] // 2
        ancho = f.measure(texto) + 20
        self.c.create_rectangle(x - ancho / 2, self.alto - 26, x + ancho / 2, self.alto - 4,
                                fill="#1a1a1a", outline="#ffd400", tags="estado")
        self.c.create_text(x, self.alto - 15, text=texto, font=f, fill="#ffd400", tags="estado")

    def mostrar(self, traduccion, original=""):
        cfg = self.app.cfg
        _, fondo, color = ESQUEMAS.get(cfg["esquema"], ESQUEMAS["blanco_negro"])
        familia = FUENTES.get(cfg["fuente"], FUENTES["Arial"])[2]
        tam = int(cfg.get("tam_subtitulo", 30))
        f = self._fuente(familia, tam, "bold" if cfg["negrita"] else "normal")
        ancho = int(self.mon["width"] * 0.8)
        lineas = _envolver(traduccion, f, ancho)[-3:]
        alto_txt = f.metrics("linespace") * len(lineas)
        orig_txt, alto_orig, fo = "", 0, None
        if original and cfg.get("subtitulo_original", True):
            fo = self._fuente(familia, max(11, tam // 2), "normal")
            orig = _envolver(original, fo, ancho)[-2:]
            orig_txt, alto_orig = "\n".join(orig), fo.metrics("linespace") * len(orig) + 4
        ancho_real = max([f.measure(l) for l in lineas] + ([fo.measure(l) for l in orig_txt.split("\n")] if fo else []))
        x = self.mon["width"] / 2
        abajo = self.alto - 32
        arriba = abajo - alto_txt - alto_orig - 14
        c = self.c
        c.delete("sub")
        c.create_rectangle(x - ancho_real / 2 - 16, arriba, x + ancho_real / 2 + 16, abajo,
                           fill=_hex(fondo), outline="", tags="sub")
        y = arriba + 7
        if orig_txt:
            c.create_text(x, y, anchor="n", text=orig_txt, font=fo, justify="center",
                          fill=_hex(tuple((a + b) // 2 for a, b in zip(color, fondo))), tags="sub")
            y += alto_orig
        c.create_text(x, y, anchor="n", text="\n".join(lineas), font=f, justify="center",
                      fill=_hex(color), tags="sub")
        if self._borrar:
            self.win.after_cancel(self._borrar)
        segundos = max(3.0, min(9.0, 1.5 + len(traduccion) * 0.06))
        self._borrar = self.win.after(int(segundos * 1000), lambda: c.delete("sub"))

    def existe(self):
        try:
            return self.win.winfo_exists()
        except tk.TclError:
            return False

    def cerrar(self):
        if self.existe():
            self.win.destroy()
