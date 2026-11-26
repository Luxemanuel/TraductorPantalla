# -*- coding: utf-8 -*-
"""
Traducción EN VIVO:
  · CapaEnVivo: ventana transparente encima de todo que deja pasar los clics y el
    teclado (puedes seguir usando la aplicación de debajo) y que Windows excluye de
    las capturas de pantalla (así el programa no se lee a sí mismo).
  · SesionEnVivo: vigila una zona (o la pantalla) varias veces por segundo y solo
    vuelve a leer/traducir cuando el contenido cambió y se quedó quieto.
"""
import queue
import sys
import threading
import math
import time
import tkinter as tk
import tkinter.font as tkfont

from PIL import Image, ImageChops, ImageStat

import nucleo
from nucleo import ESQUEMAS, FUENTES, Cancelado, log, log_limitado

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


def huella(img, caja):
    """Firma de los TRAZOS del texto en 'caja' (lo que tiene contraste con su entorno cercano), no
    del fondo: sirve para saber, sin volver a leer, si el texto de ese sitio sigue igual. Un fondo
    animado o un brillo suave casi no la cambian; otro texto, o que el texto se vaya, sí."""
    import numpy as np
    from PIL import ImageFilter
    x0, y0, x1, y1 = (int(round(v)) for v in caja)
    x0, y0 = max(0, x0 - 2), max(0, y0 - 2)
    x1, y1 = min(img.width, x1 + 2), min(img.height, y1 + 2)
    if x1 - x0 < 4 or y1 - y0 < 4:
        return None
    g = img.crop((x0, y0, x1, y1)).convert("L")
    radio = max(3, (y1 - y0) // 3)
    d = np.abs(np.asarray(g, np.int16) - np.asarray(g.filter(ImageFilter.BoxBlur(radio)), np.int16))
    # solo la tinta: lo que se aparta MUCHO de su fondo (el "halo" que deja el desenfoque
    # alrededor de las letras se aparta poco y no cuenta)
    return d > max(22, 0.45 * float(np.percentile(d, 99.5)))


def cambio_huella(h0, h1):
    """0 = mismos trazos; 1 = nada que ver (otro texto, o ya no hay texto)."""
    if h0 is None or h1 is None or h0.shape != h1.shape:
        return 1.0
    n0, n1 = int(h0.sum()), int(h1.sum())
    if n0 < MIN_TRAZOS:
        return 1.0                        # no había trazos que comparar: no se puede dar por igual
    if not 0.6 <= n1 / n0 <= 1.6:
        return 1.0                        # mucha más o mucha menos tinta: otro texto o ya no hay
    return int((h0 ^ h1).sum()) / max(30, int((h0 | h1).sum()))


MIN_TRAZOS = 40


def cambio_local(h0, h1):
    """El mayor cambio en un trozo del ancho de una letra. Un número que cambia (12:00 -> 12:01)
    apenas mueve el total de la línea, pero su trozo cambia casi entero; un fondo que se mueve
    cambia un poco por todas partes."""
    if h0 is None or h1 is None or h0.shape != h1.shape:
        return 1.0
    r = h0.shape[0]
    w = max(8, int(r * 0.6))
    mayor = 0.0
    for i in range(0, h0.shape[1], max(4, w // 2)):
        u = int((h0[:, i:i + w] | h1[:, i:i + w]).sum())
        if u >= max(15, 0.08 * r * w):
            mayor = max(mayor, int((h0[:, i:i + w] ^ h1[:, i:i + w]).sum()) / u)
    return mayor


def otro_texto(leido, conocido):
    """La comprobación de una línea dudosa: ¿lo leído ahora es claramente OTRO texto? Con un fondo
    que se mueve el lector se equivoca en alguna letra, así que solo cuenta como otro texto si
    cambian los números (fecha, contador) o casi todo. Si no se leyó nada, no se da por cambiado:
    un texto que se va se nota antes en sus trazos."""
    import unicodedata
    na, nb = _n(leido), _n(conocido)
    if not na or not nb:
        return False
    cifras = lambda t: _re.sub(r"\D", "", unicodedata.normalize("NFKC", t))
    return cifras(leido) != cifras(conocido) or _parecido(na, nb)[0] < 0.7


# medido: fondo animado ~0.06 (hasta ~0.3 detrás de un cuadro casi transparente), texto resaltado
# de otro color ~0.21, otra palabra ~0.5, otro texto ~0.4-0.9, sin texto 1.
# Hasta UMBRAL_HUELLA: igual. Desde UMBRAL_SEGURO: otro texto seguro, se quita al momento.
# Entre medias: se lee SOLO esa línea para saber si de verdad cambió (no parpadea por el fondo).
UMBRAL_HUELLA = 0.30
UMBRAL_SEGURO = 0.6
UMBRAL_LOCAL = 0.5          # cambio en un trozo del ancho de una letra (ver cambio_local)
UMBRAL_LOCAL_SEGURO = 0.8   # medido: otra palabra ~0.96, otro número ~0.85; fondo animado <= ~0.52


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


def exe_de_ventana(hwnd):
    """Ruta del programa (.exe) dueño de una ventana, o None."""
    if not ES_WINDOWS or not hwnd:
        return None
    import ctypes
    import ctypes.wintypes as wt
    u32, k32 = ctypes.windll.user32, ctypes.windll.kernel32
    pid = wt.DWORD()
    u32.GetWindowThreadProcessId(ctypes.c_void_p(hwnd), ctypes.byref(pid))
    k32.OpenProcess.restype = ctypes.c_void_p
    h = k32.OpenProcess(0x1000, False, pid.value)          # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return None
    try:
        buf = ctypes.create_unicode_buffer(1024)
        n = wt.DWORD(1024)
        if k32.QueryFullProcessImageNameW(ctypes.c_void_p(h), 0, buf, ctypes.byref(n)):
            return buf.value
    finally:
        k32.CloseHandle(ctypes.c_void_p(h))
    return None


def clase_ventana(hwnd):
    import ctypes
    buf = ctypes.create_unicode_buffer(256)
    ctypes.windll.user32.GetClassNameW(ctypes.c_void_p(hwnd), buf, 256)
    return buf.value


_ESCRITORIO = {"Shell_TrayWnd", "Shell_SecondaryTrayWnd", "NotifyIconOverflowWindow", "Progman",
               "WorkerW", "TopLevelWindowForOverflowXamlIsland", "Windows.UI.Core.CoreWindow"}


def es_del_escritorio(hwnd):
    """Barra de tareas, escritorio, menú Inicio…: no son programas que se puedan anclar."""
    try:
        return clase_ventana(hwnd) in _ESCRITORIO
    except Exception:
        return False


def ventana_en(x, y):
    """Ventana principal que hay en un punto de la pantalla."""
    if not ES_WINDOWS:
        return None
    import ctypes
    import ctypes.wintypes as wt
    u32 = ctypes.windll.user32
    u32.WindowFromPoint.restype = ctypes.c_void_p
    u32.GetAncestor.restype = ctypes.c_void_p
    hw = u32.WindowFromPoint(wt.POINT(int(x), int(y)))
    return u32.GetAncestor(ctypes.c_void_p(hw), 2) if hw else None     # GA_ROOT


_VK = {"ctrl": 0x11, "alt": 0x12, "shift": 0x10}


def atajo_pulsado(at):
    """¿Está pulsada AHORA la combinación (para «mantener para ver el original»)?"""
    if not ES_WINDOWS or not at:
        return False
    import ctypes
    est = ctypes.windll.user32.GetAsyncKeyState
    t = at.get("tecla", "")
    if t.startswith("F") and len(t) > 1 and t[1:].isdigit():
        vk = 0x6F + int(t[1:])
    elif t == "Espacio":
        vk = 0x20
    elif len(t) == 1:
        vk = ord(t.upper())
    else:
        return False
    teclas = [vk] + [v for k, v in _VK.items() if at.get(k)]
    return all(est(v) & 0x8000 for v in teclas)


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
    __slots__ = ("id", "texto", "idioma", "caja", "filas", "trad", "trad_de", "visto", "fallos",
                 "confirmada", "candidato", "conf_cand", "nueva", "creada", "huella")

    def __init__(self, p, ahora):
        self.id = next(_contador)
        self.texto, self.idioma, self.caja = p["t"], p["idioma"], p["caja"]
        self.filas = p.get("filas")
        self.creada = ahora
        self.trad = None            # traducción que se muestra
        self.trad_de = None         # texto que se tradujo (para saber si hay que retraducir)
        self.visto = ahora
        self.fallos = 0
        self.confirmada = False     # vista 2 veces seguidas con el mismo texto
        self.candidato, self.conf_cand = None, 0
        self.nueva = True
        self.huella = None          # trazos de su sitio cuando se confirmó (ver huella())


def _parecido(a, b):
    """(ratio, cubierto): parecido general y fracción de 'a' que aparece, en orden, en 'b'."""
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    m = sum(bl.size for bl in sm.get_matching_blocks())
    return 2.0 * m / max(1, len(a) + len(b)), m / max(1, len(a))


def _pisa(a, b):
    """¿Las dos cajas ocupan casi el mismo sitio?"""
    ix = min(a[2], b[2]) - max(a[0], b[0])
    iy = min(a[3], b[3]) - max(a[1], b[1])
    return ix > 0 and iy > 0 and ix * iy > 0.3 * min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))


def _desplazar(p, dy):
    """Párrafo leído en una franja -> coordenadas de la imagen entera."""
    x0, y0, x1, y1 = p["caja"]
    q = dict(p, caja=(x0, y0 + dy, x1, y1 + dy))
    if p.get("filas"):
        q["filas"] = [(t, (c[0], c[1] + dy, c[2], c[3] + dy)) for t, c in p["filas"]]
    return q


def en_franjas(caja, franjas):
    """¿La caja quedó entera dentro de lo leído? (None = se leyó toda la pantalla)"""
    return franjas is None or any(caja[1] >= y0 - 3 and caja[3] <= y1 + 3 for y0, y1 in franjas)


def franjas_de_cambio(peq, ref, tapar, k, minimo=6):
    """Franjas horizontales (y0, y1, en píxeles reales) donde la imagen cambió de verdad respecto a
    la referencia, sin contar las cajas de 'tapar' (lo ya traducido, que se vigila por sus trazos).
    k = ancho de la miniatura / ancho real."""
    import numpy as np
    if ref is None or ref.size != peq.size:
        return None
    m = np.abs(np.asarray(peq, np.int16) - np.asarray(ref, np.int16)) > 28
    for x0, y0, x1, y1 in tapar:
        m[max(0, int(y0 * k) - 2):int(y1 * k) + 3, max(0, int(x0 * k) - 2):int(x1 * k) + 3] = False
    ys = np.nonzero(m.sum(1) >= 2)[0]
    franjas = []
    if len(ys):
        ini = prev = int(ys[0])
        for y in list(ys[1:]) + [None]:
            if y is not None and y - prev <= 3:
                prev = int(y)
                continue
            if int(m[ini:prev + 1].sum()) >= minimo:
                franjas.append((ini / k, (prev + 1) / k))
            if y is not None:
                ini = prev = int(y)
    return franjas


def unir_franjas(franjas, alto, separacion=12):
    franjas = sorted((max(0.0, a), min(float(alto), b)) for a, b in franjas)
    unidas = []
    for a, b in franjas:
        if unidas and a <= unidas[-1][1] + separacion:
            unidas[-1] = (unidas[-1][0], max(unidas[-1][1], b))
        else:
            unidas.append((a, b))
    return unidas


class Seguidor:
    """Empareja lo que lee el lector en cada vistazo con las líneas que ya conoce:
      · la misma línea (aunque se mueva, cambie el fondo o se lea una letra distinta)
        conserva su traducción y no se vuelve a traducir;
      · si una parte queda tapada (un kanji), se mantiene la versión completa conocida,
        pero solo si se sigue viendo al menos la mitad: unas pocas letras de una frase
        NUEVA en el mismo sitio no se confunden con la anterior;
      · cuando aparece otro texto en el sitio de una línea, la anterior se quita al momento;
      · una línea que se fue y vuelve enseguida recupera su traducción sin esperar."""

    RECIENTES_SEG = 30.0

    def __init__(self, gracia=0.35):
        self.lineas = []
        self.gracia = gracia
        self.recientes = {}          # texto normalizado -> (Linea, cuándo se fue)

    def _misma(self, np_, p, l):
        nl = _n(l.texto)
        if not nl:
            return None
        ratio, cubre = _parecido(np_, nl)
        cx, cy = (p["caja"][0] + p["caja"][2]) / 2, (p["caja"][1] + p["caja"][3]) / 2
        lx, ly = (l.caja[0] + l.caja[2]) / 2, (l.caja[1] + l.caja[3]) / 2
        alto = max(10, l.caja[3] - l.caja[1])
        cerca = abs(cy - ly) < alto * 1.2 and abs(cx - lx) < (l.caja[2] - l.caja[0]) * 0.8 + alto
        if (ratio >= 0.72                                                     # la misma, con algún error
                or (cerca and cubre >= 0.85 and len(np_) >= 0.5 * len(nl))    # una parte tapada
                or (cerca and not l.confirmada and np_.startswith(nl[:max(1, len(nl) - 2)]))):   # escribiéndose
            return ratio + (0.15 if cerca else 0)
        return None

    def _retirar(self, l, ahora):
        self.lineas.remove(l)
        if l.trad:
            self.recientes[_n(l.texto)] = (l, ahora)

    def fijas(self):
        """Líneas traducidas cuyo sitio se vigila por sus trazos: mientras no cambien, se quedan
        tal cual, lea lo que lea el lector (así una lectura con ruido no las hace parpadear)."""
        return [l for l in self.lineas if l.trad and l.huella is not None and l.huella.sum() >= MIN_TRAZOS]

    def quitar(self, lineas, ahora):
        for l in lineas:
            if l in self.lineas:
                self._retirar(l, ahora)

    def actualizar(self, pars, ahora, franjas=None):
        """franjas: si se leyó solo una parte de la pantalla, las franjas (y0, y1) leídas. Lo que
        está fuera no se ha mirado en esta lectura: se queda como estaba."""
        for k, (l, t) in list(self.recientes.items()):
            if ahora - t > self.RECIENTES_SEG:
                del self.recientes[k]
        fijas = self.fijas()
        libres = [l for l in self.lineas if l not in fijas]
        vistas = {l.id for l in fijas}
        for p in pars:
            np_ = _n(p["t"])
            if not np_:
                continue
            encima = [l for l in fijas if _pisa(p["caja"], l.caja)]
            if encima:
                l = encima[0]
                nl = _n(l.texto)
                alto = max(8, l.caja[3] - l.caja[1])
                crece = (p["caja"][2] > l.caja[2] + 0.6 * alto or p["caja"][3] > l.caja[3] + 0.6 * alto)
                if crece and len(np_) >= len(nl) + 2 and _parecido(nl, np_)[1] >= 0.85:
                    # el texto siguió escribiéndose después de traducirse: la versión completa
                    self.lineas.remove(l)             # (no a "recientes": no debe volver la corta)
                    fijas.remove(l)
                    vistas.discard(l.id)
                else:
                    continue                  # ese sitio ya está traducido y no ha cambiado
            mejor, puntos = None, 0.0
            for l in libres:
                if l.idioma == p["idioma"]:
                    pt = self._misma(np_, p, l)
                    if pt is not None and pt > puntos:
                        mejor, puntos = l, pt
            if mejor is None:
                # ¿es un trozo de una línea ya vista en este mismo vistazo (renglones mal agrupados)?
                if any(l.id in vistas and _pisa(p["caja"], l.caja) and _parecido(np_, _n(l.texto))[1] >= 0.85
                       for l in self.lineas):
                    continue
                # ¿una línea que se acaba de ir y vuelve? (recupera su traducción al instante)
                for k, (l, t) in list(self.recientes.items()):
                    if l.idioma == p["idioma"] and _parecido(np_, k)[0] >= 0.9:
                        del self.recientes[k]
                        l.caja, l.filas, l.visto, l.fallos = p["caja"], p.get("filas"), ahora, 0
                        l.huella = None           # sus trazos se toman de nuevo en esta lectura
                        self.lineas.append(l)
                        vistas.add(l.id)
                        break
                else:
                    l = Linea(p, ahora)
                    self.lineas.append(l)
                    vistas.add(l.id)
                continue
            libres.remove(mejor)
            vistas.add(mejor.id)
            l = mejor
            nl = _n(l.texto)
            completa = len(np_) >= 0.9 * len(nl)
            if completa and max(abs(a - b) for a, b in zip(p["caja"], l.caja)) > 3:   # ignorar temblores
                l.caja = p["caja"]
                l.filas = p.get("filas")
            l.visto, l.fallos = ahora, 0
            if not l.confirmada:
                # primera confirmación: el texto debe repetirse casi igual
                if difflib.SequenceMatcher(None, np_, nl, autojunk=False).ratio() >= 0.9:
                    l.confirmada = True
                    if len(np_) > len(nl):
                        l.texto, l.filas = p["t"], p.get("filas")
                else:
                    l.texto, l.caja, l.filas = p["t"], p["caja"], p.get("filas")     # aún se está escribiendo
            elif np_ != nl and len(np_) >= len(nl) + 2:
                # versión más completa: adoptarla si se ve dos veces
                if l.candidato is not None and _n(l.candidato) == np_:
                    l.conf_cand += 1
                else:
                    l.candidato, l.conf_cand = p["t"], 1
                if l.conf_cand >= 2:
                    l.texto, l.candidato, l.conf_cand = p["t"], None, 0
                    l.caja, l.filas = p["caja"], p.get("filas")
            # si es igual o más corta (letra tapada / mal leída): se conserva la conocida
        quitadas = False
        vistas_l = [l for l in self.lineas if l.id in vistas]
        for l in list(self.lineas):
            if l.id in vistas or not en_franjas(l.caja, franjas):
                continue
            if any(_pisa(l.caja, v.caja) for v in vistas_l):
                self._retirar(l, ahora)          # otro texto ocupa su sitio: fue reemplazada
                quitadas = True
                continue
            l.fallos += 1
            if ahora - l.visto > self.gracia and l.fallos >= 2:
                self._retirar(l, ahora)
                quitadas = True
        return quitadas

    def necesita_mirar(self):
        """Hay algo por confirmar (línea nueva, versión más completa o línea que se fue). Lo que
        lleva más de 3 s sin confirmarse es ruido (algo animado), no texto: no obliga a mirar."""
        ahora = time.time()
        return any(l.fallos > 0 or l.candidato or (not l.confirmada and ahora - l.creada < 3.0)
                   for l in self.lineas)

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

    def olvidar_traducciones(self, original=None):
        """Volver a traducir (todas, o las de un texto) — p. ej. tras corregir una traducción."""
        for l in self.lineas:
            if original is None or _n(l.texto) == _n(original):
                l.trad_de = None

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

    def __init__(self, app, mon, caja, capturar=None, estilo="palabras", ventana=None,
                 ruta_exe=None, fuente=None, letras=None):
        import mss
        self.app = app
        self.ruta_exe = ruta_exe
        self.guion = None                      # juegos Ren'Py: su guion, para leer la frase exacta
        if ruta_exe:
            try:
                import guion
                guion.cargar_en_segundo_plano(ruta_exe, self._poner_guion)
            except Exception:
                log.exception("Guion del juego")
        self.fuente_juego = fuente
        self.letras = letras                   # letras ya decididas para este juego (perfil)
        self.natural = None                    # CapaNatural (traducción integrada en el juego)
        self.pintor = None
        self.viendo_original = False
        self.mon = dict(mon)
        self.caja = caja
        self.estilo = estilo                  # "palabras" (sobre cada línea) | "barra" (subtítulos)
        self.ventana = ventana
        self.tipo = "subtitulos" if estilo == "barra" else ("zona" if caja else "pantalla")
        self.umbral = 0.002 if caja else 0.0008
        self.intervalo = 100 if estilo == "barra" else 150
        self.seguidor = Seguidor(gracia=0.25 if estilo == "barra" else 0.35)
        # la traducción va en su propio hilo: leer la pantalla nunca espera a internet
        self.cerrojo = threading.RLock()
        self._pedidos = {}                    # id de línea -> texto que se está traduciendo
        self._cola_trad = queue.Queue()
        self._nuevos = []                     # líneas recién traducidas (historial y voz)
        self.repintar = False                 # llegaron traducciones: componer aunque nada cambie
        self.activa = True
        self.procesando = False
        self.ref = None
        self.ultimo_lanzamiento = 0
        self._ultima_captura = 0
        self._invalidadas = []
        self._verificando = set()             # líneas que se están comprobando leyéndolas
        self._por_quitar = []                 # comprobadas: era otro texto
        self.verificaciones = 0
        self._pista = None                    # "ja"/"zh": idioma del juego, visto en una lectura completa
        self._otras = []                      # cajas del texto a la vista
        self.ultimas_franjas = None                 # [(caja, cuándo)] sitios cuyo texto cambió
        self.bloques = []
        self.firma = None
        self.ultima_duracion = None
        self.lecturas = 0
        self.oculta = False
        self._capturar_ext = capturar          # para pruebas
        self._sct = None if capturar else mss.mss()
        self._contexto = {}                    # idioma -> última frase de diálogo (contexto)
        self._metodo = "pantalla"              # "ventana": se lee la ventana del juego (PrintWindow)
        self._negras = 0                       # capturas seguidas que salieron negras
        self.aviso_negro = False
        self.capa = CapaEnVivo(app, self.mon, caja)
        self.aplicar_modo()
        threading.Thread(target=self._traductor, daemon=True).start()
        if ventana:
            self.titulo = titulo_ventana(ventana)
            self.capa.aviso = f"● EN VIVO: {self.titulo[:40]}  ·  Ctrl+Alt+X detener"
            self.capa.dibujar_marco()
        app.root.after(30, self._tick)

    # ---------- modo de dibujo ----------
    def modo_texto(self):
        """Subtítulos: franja clásica. Todo lo demás: la traducción integrada en el juego."""
        return "caja" if self.estilo == "barra" else "natural"

    def aplicar_modo(self):
        if self.modo_texto() == "natural" and self.natural is None:
            import capa_natural
            self.natural = capa_natural.CapaNatural(self.app)
            self.pintor = capa_natural.Pintor(self.ruta_exe, self.fuente_juego, self.letras)
            if self.oculta or self.viendo_original:
                self.natural.visible(False)
            self.capa.limpiar()
        self.firma = None
        self.ref = None

    # ---------- traducción en segundo plano ----------
    def _traductor(self):
        while self.activa:
            try:
                lote = self._cola_trad.get(timeout=0.5)
            except queue.Empty:
                continue
            while True:                                   # juntar todo lo que esté esperando
                try:
                    lote += self._cola_trad.get_nowait()
                except queue.Empty:
                    break
            t0 = time.perf_counter()
            try:
                # una o dos frases nuevas (diálogo): se traducen con la anterior como contexto
                ctx = self._contexto if len(lote) <= 2 else None
                trads = self.app.motor.traducir_lote([(t, i) for _, t, i in lote], contexto=ctx)
            except Exception:
                log_limitado("traducir", "Traduciendo en segundo plano")
                trads = [None] * len(lote)
            for (_, texto, idioma), tr in zip(lote, trads):
                if tr and (len(texto) >= 8 if idioma in ("ja", "zh") else len(texto.split()) >= 4):
                    self._contexto[idioma] = texto
            self.app.motor.tiempos = (self.app.motor.tiempos[0], time.perf_counter() - t0)
            with self.cerrojo:
                validas = []
                for (l, texto, _), tr in zip(lote, trads):
                    if self._pedidos.get(l.id) == texto:
                        del self._pedidos[l.id]
                    if l.texto == texto:                  # sigue siendo el mismo texto
                        validas.append((l, tr))
                self._nuevos += self.seguidor.asignar([l for l, _ in validas], [tr for _, tr in validas])
            self.repintar = True

    def _poner_guion(self, g):
        self.guion = g
        self.app.cola.put(("estado", f"Juego Ren'Py: se usan las {len(g)} frases de su guion para leer "
                                     f"el texto exacto."))

    def ocupada(self):
        """¿Hay texto cambiando o traducciones por llegar? (entonces se mira más a menudo)"""
        return bool(self.repintar or self._pedidos or self.seguidor.necesita_mirar()
                    or (self.pintor is not None and self.pintor.pendiente))

    def ver_original(self, si):
        """Mientras se mantiene la tecla, se ve el texto del juego sin traducir."""
        self.viendo_original = si
        if self.natural is not None:
            self.natural.visible(not si and not self.oculta)
        try:
            self.capa.c.itemconfigure("trad", state="hidden" if si else "normal")
        except tk.TclError:
            pass

    def origen(self):
        reg = self.region()
        return reg["left"], reg["top"]

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
        if self._metodo == "ventana" and self.ventana:
            img = self._capturar_ventana(reg)
            if img is not None:
                return img
            self._metodo = "pantalla"
        if not self.capa.excluida:            # Windows antiguo: esconder la capa un instante
            self.capa.esconder_un_momento()
            if self.natural is not None and self.natural.visible_:
                self.natural.visible(False)
                self._natural_escondida = True
        try:
            shot = self._sct.grab(reg)
        finally:
            if not self.capa.excluida:
                self.capa.reaparecer()
                if getattr(self, "_natural_escondida", False):
                    self._natural_escondida = False
                    self.natural.visible(True)
        img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
        tam = (reg["width"], reg["height"])
        img = img if img.size == tam else img.resize(tam, Image.LANCZOS)
        return self._revisar_negra(img, reg)

    def _capturar_ventana(self, reg):
        try:
            img = capturar_hwnd(self.ventana, reg)
        except Exception:
            log_limitado("printwindow", "Leyendo la ventana del juego")
            return None
        return None if img is None or es_negra(img) else img

    def _revisar_negra(self, img, reg):
        """Algunos juegos (pantalla completa exclusiva, DirectX) salen negros en una captura
        normal. Entonces se lee la ventana del juego directamente; si tampoco se puede, se avisa."""
        if not self.ventana or not es_negra(img):
            self._negras = 0
            self.aviso_negro = False
            return img
        self._negras += 1
        if self._negras in (3, 30):
            alt = self._capturar_ventana(reg)
            if alt is not None:
                log.info("La captura sale negra: se lee la ventana del juego directamente")
                self._metodo = "ventana"
                self._negras = 0
                return alt
        if self._negras >= 45 and not self.aviso_negro:          # ~6 s seguidos sin imagen
            self.aviso_negro = True
            self.app.cola.put(("aviso_juego", self))
        return img

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
                if self.natural is not None:
                    self.natural.visible(False)
            return False
        rect = rect_cliente(self.ventana)
        if rect is None:
            return False
        viejo = (self.mon["left"], self.mon["top"])
        if self.capa.mover(rect):
            self.mon = rect
            self.ref = None                  # se movió: volver a mirar
            if self.natural is not None:
                self.natural.mover(rect["left"] - viejo[0], rect["top"] - viejo[1])
        if self.oculta:
            self.oculta = False
            self.capa.visible(True)          # al volver, todo sigue traducido
            if self.natural is not None and not self.viendo_original:
                self.natural.visible(True)
            self.ref = None
        return True

    # ---------- bucle ----------
    def _tick(self):
        if not self.activa:
            return
        try:
            self._ticks = getattr(self, "_ticks", 0) + 1
            visible = self._seguir_ventana()
            if visible and self._ticks % 15 == 0:
                self.capa.subir()
                if self.natural is not None:
                    self.natural.subir()
            ocupada = self.ocupada()
            if visible and time.time() - self._ultima_captura > (0.05 if ocupada else 0.1):
                self._ultima_captura = time.time()
                img = self._capturar()
                # 1) ¿Siguen igual los trazos de lo ya traducido? Si no, se quita al momento.
                self._revisar_fijas(img)
                capturable = self.natural is not None and getattr(self.natural, "capturable", False)
                if not self.procesando and not capturable:   # con la traducción en la captura no se lee
                    # 2) Leer solo las franjas donde algo cambió FUERA de lo ya traducido (texto
                    #    nuevo, o algo que se fue). Lo traducido y quieto no se vuelve a leer, y una
                    #    zona que cambia no toca a las demás.
                    self._decidir_lectura(img, miniatura(img))
        except Exception:
            log_limitado("tick", "Bucle en vivo")
        if self.activa:
            self.app.root.after(60 if self.ocupada() else self.intervalo, self._tick)

    def _revisar_fijas(self, img):
        """Compara los trazos de cada traducción fija con los de ahora. Las que cambiaron (otro
        texto, o ya no hay texto) se quitan en este mismo instante."""
        if self.natural is not None and getattr(self.natural, "capturable", False):
            return False                       # la traducción sale en la captura: no se puede comparar
        with self.cerrojo:
            cambiadas, dudosas = [], []
            for l in self.seguidor.fijas():
                h = huella(img, l.caja)
                c = cambio_huella(l.huella, h)
                cl = cambio_local(l.huella, h)
                if c <= UMBRAL_HUELLA and cl <= UMBRAL_LOCAL:
                    continue
                if (c >= UMBRAL_SEGURO or cl >= UMBRAL_LOCAL_SEGURO or h is None
                        or h.sum() < max(MIN_TRAZOS, 0.3 * l.huella.sum())):
                    cambiadas.append(l)                  # otro texto, o ya no hay: fuera ya
                elif l.id not in self._verificando:
                    dudosas.append(l)
            # las que se comprobaron leyéndolas y resultaron ser otro texto
            cambiadas += [l for l in self._por_quitar if l in self.seguidor.lineas and l not in cambiadas]
            self._por_quitar = []
            if dudosas:
                self._verificar(img, dudosas)
            if not cambiadas:
                return False
            ahora = time.time()
            self.seguidor.quitar(cambiadas, ahora)
            self._invalidadas = [(c, t) for c, t in self._invalidadas if ahora - t < 5]
            self._invalidadas += [(l.caja, ahora) for l in cambiadas]
            dx, dy = (self.caja[0], self.caja[1]) if self.caja else (0, 0)
            vis = self.seguidor.visibles(dx, dy)
        ids = {l.id for l in cambiadas}
        if self.natural is not None:
            self.natural.quitar_ids(ids)
        elif self.firma is not None:
            self.firma = None
            if self.estilo == "barra":
                self.capa.mostrar_barra(vis)
            elif self.app.cfg["modo_vista"] != "panel":
                self.capa.mostrar(vis)
        self.bloques = vis
        return True

    def _verificar(self, img, lineas):
        """Lee solo esas líneas (un recorte pequeño, muy rápido) para saber si su texto sigue
        siendo el mismo. Mientras tanto la traducción se queda."""
        ids = {l.id for l in lineas}
        self._verificando |= ids
        motor, cfg = self.app.motor, self.app.cfg
        modo, omitir = cfg["idioma_origen"], cfg["omitir_espanol"]

        def tarea():
            try:
                for l in lineas:
                    x0, y0, x1, y1 = l.caja
                    m = max(6.0, 0.4 * (y1 - y0))
                    caja = (int(max(0, x0 - m)), int(max(0, y0 - m)),
                            int(min(img.width, x1 + m)), int(min(img.height, y1 + m)))
                    pars = motor.leer_parrafos(img.crop(caja), modo, omitir, seguir=lambda: self.activa,
                                               pista=self._pista)
                    igual = not otro_texto(" ".join(p["t"] for p in pars), l.texto)
                    with self.cerrojo:
                        if igual:
                            l.huella = huella(img, l.caja)   # el mismo texto: sus trazos de ahora
                        else:
                            self._por_quitar.append(l)
                    self.verificaciones += 1
            except Cancelado:
                pass
            except Exception:
                log_limitado("verificar", "Comprobando una línea")
            finally:
                self._verificando -= ids
        threading.Thread(target=tarea, daemon=True).start()

    def _decidir_lectura(self, img, peq):
        ahora = time.time()
        if self.ref is None or self.ref.size != peq.size:
            return self._lanzar(img, peq)                     # primera vez: toda la pantalla
        if not self.seguidor.lineas and ahora - self.ultimo_lanzamiento > 3.0:
            return self._lanzar(img, peq)                     # nada a la vista: un vistazo general
        franjas = []
        if self._cambio_fuera(peq) > self.umbral * 0.5:
            k = peq.width / max(1, img.width)
            franjas = franjas_de_cambio(peq, self.ref, [l.caja for l in self.seguidor.fijas()], k)
            if franjas is None:
                return self._lanzar(img, peq)
        # lo que hay que volver a mirar aunque la imagen no cambie: líneas por confirmar
        # (se leen dos veces antes de traducir) o que en la última lectura no se vieron
        for l in self.seguidor.lineas:
            if l.fallos > 0 or l.candidato or (not l.confirmada and ahora - l.creada < 3.0):
                franjas.append((l.caja[1], l.caja[3]))
        if not franjas:
            if self.repintar or (self.pintor is not None and self.pintor.pendiente):
                self._lanzar(img, peq, [])                    # llegaron traducciones: solo pintar
            return
        franjas = self._ampliar(franjas, img.height)
        if sum(b - a for a, b in franjas) > 0.6 * img.height:
            return self._lanzar(img, peq)                     # cambió casi todo: otra pantalla
        self._lanzar(img, peq, franjas)

    def _ampliar(self, franjas, alto):
        """Un margen alrededor de cada cambio, y que ninguna línea conocida quede cortada."""
        out = unir_franjas([(a - max(16.0, min(60.0, 0.6 * (b - a))), b + max(16.0, min(60.0, 0.6 * (b - a))))
                            for a, b in franjas], alto)
        for _ in range(4):
            cambio, nuevas = False, []
            for a, b in out:
                for l in self.seguidor.lineas:
                    y0, y1 = l.caja[1], l.caja[3]
                    if y1 > a and y0 < b and (y0 < a or y1 > b):
                        a, b, cambio = min(a, y0 - 6), max(b, y1 + 6), True
                nuevas.append((a, b))
            out = unir_franjas(nuevas, alto)
            if not cambio:
                break
        return out

    def _leer_franjas(self, motor, img, franjas, modo, omitir):
        """Lee cada franja por separado. Si un texto toca el borde de la franja (quedó cortado),
        se amplía ese lado y se vuelve a leer."""
        pars, leidas = [], []
        for a, b in franjas:
            a, b = int(a), int(math.ceil(b))
            for _ in range(3):
                ps = motor.leer_parrafos(img.crop((0, a, img.width, b)), modo, omitir,
                                         seguir=lambda: self.activa, pista=self._pista)
                ps = [_desplazar(p, a) for p in ps]
                arriba = a > 0 and any(p["caja"][1] <= a + 2 for p in ps)
                abajo = b < img.height and any(p["caja"][3] >= b - 2 for p in ps)
                if not (arriba or abajo):
                    break
                paso = int(max(16, min(60, max(p["caja"][3] - p["caja"][1] for p in ps))))
                a = max(0, a - paso) if arriba else a
                b = min(img.height, b + paso) if abajo else b
            for p in ps:            # dos franjas que acabaron solapándose: sin repetir
                if not any(q["t"] == p["t"] and _pisa(q["caja"], p["caja"]) for q in pars):
                    pars.append(p)
            leidas.append((a, b))
        return pars, leidas

    def _cambio_fuera(self, peq):
        """Cuánto cambió la imagen sin contar los sitios ya traducidos (que se vigilan aparte)."""
        if self.ref is None or self.ref.size != peq.size:
            return 1.0
        fijas = self.seguidor.fijas()
        if not fijas:
            return diferencia(peq, self.ref)
        from PIL import ImageDraw
        k = peq.width / max(1, self.region()["width"])
        a, b = peq.copy(), self.ref.copy()
        for im in (a, b):
            d = ImageDraw.Draw(im)
            for l in fijas:
                x0, y0, x1, y1 = l.caja
                d.rectangle((x0 * k - 2, y0 * k - 2, x1 * k + 2, y1 * k + 2), fill=0)
        return diferencia(a, b)

    def _lanzar(self, img, peq, franjas=None):
        """franjas None: leer toda la pantalla. Lista de (y0, y1): leer solo esas franjas.
        Lista vacía: no leer, solo pintar las traducciones que llegaron."""
        self.procesando = True
        if franjas is None:
            self.ref = peq
        elif franjas:
            # la referencia se pone al día SOLO en lo que se lee; el resto sigue esperando su turno
            k = peq.width / max(1, img.width)
            ref = self.ref.copy()
            for a, b in franjas:
                caja = (0, max(0, int(a * k) - 1), peq.width, min(peq.height, int(math.ceil(b * k)) + 1))
                ref.paste(peq.crop(caja), caja[:2])
            self.ref = ref
        self.repintar = False
        if franjas != []:
            self.ultimas_franjas = franjas     # lo último que se leyó (None = toda la pantalla)
        self.ultimo_lanzamiento = time.time()
        cfg = self.app.cfg
        modo, omitir = cfg["idioma_origen"], cfg["omitir_espanol"]
        motor = self.app.motor
        pintor, modo_texto = self.pintor, self.modo_texto()

        t_img = time.time()

        def tarea():
            t0 = time.perf_counter()
            try:
                if franjas is None:
                    pars, leidas = motor.leer_parrafos(img, modo, omitir, seguir=lambda: self.activa), None
                    cjk = [p["idioma"] for p in pars if p["idioma"] in ("ja", "zh")]
                    if cjk:                                # el idioma del juego, para leer por trozos
                        self._pista = max(set(cjk), key=cjk.count)
                elif franjas:
                    pars, leidas = self._leer_franjas(motor, img, franjas, modo, omitir)
                else:
                    pars, leidas = [], []
                if self.guion is not None and pars:        # Ren'Py: la frase exacta del guion
                    pars = self.guion.corregir(pars)
                t1 = time.perf_counter()
                dx, dy = (self.caja[0], self.caja[1]) if self.caja else (0, 0)
                with self.cerrojo:
                    # lo que se quitó mientras se leía esta imagen (ya no está) no vuelve
                    pars = [p for p in pars if not any(t > t_img and _pisa(p["caja"], c)
                                                       for c, t in self._invalidadas)]
                    ahora = time.time()
                    if leidas != []:
                        self.seguidor.actualizar(pars, ahora, leidas)
                        # cajas de todo el texto a la vista (lo que no se releyó sigue igual)
                        self._otras = [c for c in self._otras if leidas is not None
                                       and not en_franjas(c, leidas)] + [p["caja"] for p in pars]
                    otras = list(self._otras)
                    for l in self.seguidor.lineas:
                        # los trazos de cada línea confirmada, tal como se ven en esta lectura
                        if l.confirmada and l.visto == ahora and not (l.trad and l.huella is not None):
                            l.huella = huella(img, l.caja)
                    pend = [l for l in self.seguidor.pendientes() if self._pedidos.get(l.id) != l.texto]
                    for l in pend:
                        self._pedidos[l.id] = l.texto
                    if pend:
                        self._cola_trad.put([(l, l.texto, l.idioma) for l in pend])
                    vis = self.seguidor.visibles(dx, dy)
                    traducidas = [l for l in self.seguidor.lineas if l.trad]
                    nuevas, self._nuevos = self._nuevos, []
                ids_nuevas = {l.id for l in nuevas}
                nuevos = [b for b in vis if b["id"] in ids_nuevas]
                motor.tiempos = (t1 - t0, motor.tiempos[1])
                piezas = None
                if pintor is not None and modo_texto == "natural":
                    try:
                        piezas = pintor.componer(img, traducidas, otras)
                    except Exception:
                        log_limitado("componer", "Componiendo la traducción natural")
                        piezas = None
                self.app.cola.put(("vivo", (self, vis, nuevos, time.perf_counter() - t0, piezas)))
            except Cancelado:
                self.procesando = False
            except Exception as e:
                log_limitado("lectura", "Lectura en vivo")
                self.app.cola.put(("vivo_error", (self, str(e))))
        threading.Thread(target=tarea, daemon=True).start()

    def resultado(self, bloques, nuevos, duracion, piezas=None):
        """Llamado en el hilo de la interfaz con las líneas que siguen visibles."""
        self.procesando = False
        if not self.activa:
            return []
        self.ultima_duracion = duracion
        self.lecturas += 1
        if self.natural is not None and piezas is not None:
            self.natural.actualizar(piezas, self.origen())
            self.bloques = bloques
            return nuevos
        firma = [(b["id"], tuple(int(v) for v in b["caja"]), b["traduccion"]) for b in bloques]
        if firma != self.firma:
            if self.app.cfg["modo_vista"] == "panel" and self.estilo != "barra":
                self.capa.limpiar()
            elif self.estilo == "barra":
                self.capa.mostrar_barra(bloques)
            else:
                self.capa.mostrar(bloques)
            if self.viendo_original:
                self.capa.c.itemconfigure("trad", state="hidden")
            self.firma = firma
        self.bloques = bloques
        return nuevos

    def error(self):
        self.procesando = False

    def detener(self):
        self.activa = False
        self.capa.cerrar()
        if self.natural is not None:
            self.natural.cerrar()
        if self._sct is not None:
            try:
                self._sct.close()
            except Exception:
                pass


def es_negra(img):
    """¿La imagen es (casi) toda negra? (la captura no vio el juego)"""
    peq = img.convert("L").resize((64, 36), Image.BILINEAR)
    return peq.getextrema()[1] < 14


def capturar_hwnd(hwnd, region):
    """Lee el contenido de la ventana 'hwnd' con PrintWindow (aunque tenga otras encima o el
    juego dibuje con DirectX) y recorta 'region' (coordenadas de pantalla). None si falla."""
    if not ES_WINDOWS or not hwnd:
        return None
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
    rc = wt.RECT()
    if not u32.GetWindowRect(vp(hwnd), ctypes.byref(rc)):
        return None
    w, h = rc.right - rc.left, rc.bottom - rc.top
    if w <= 0 or h <= 0 or w * h > 80_000_000:
        return None
    hdc = u32.GetWindowDC(vp(hwnd))
    if not hdc:
        return None
    mdc = g32.CreateCompatibleDC(hdc)
    bmp = g32.CreateCompatibleBitmap(hdc, w, h)
    viejo = g32.SelectObject(mdc, bmp)
    try:
        if not u32.PrintWindow(vp(hwnd), mdc, 2):                # PW_RENDERFULLCONTENT
            return None
        bi = _BIH()
        bi.biSize, bi.biWidth, bi.biHeight, bi.biPlanes, bi.biBitCount = ctypes.sizeof(_BIH), w, -h, 1, 32
        buf = ctypes.create_string_buffer(w * h * 4)
        if not g32.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bi), 0):
            return None
    finally:
        g32.SelectObject(mdc, viejo)
        g32.DeleteObject(bmp)
        g32.DeleteDC(mdc)
        u32.ReleaseDC(vp(hwnd), hdc)
    img = Image.frombuffer("RGB", (w, h), buf, "raw", "BGRX", 0, 1)
    x0, y0 = region["left"] - rc.left, region["top"] - rc.top
    return img.crop((x0, y0, x0 + region["width"], y0 + region["height"]))


if ES_WINDOWS:
    import ctypes as _ct
    import ctypes.wintypes as _wt

    class _BIH(_ct.Structure):
        _fields_ = [("biSize", _wt.DWORD), ("biWidth", _wt.LONG), ("biHeight", _wt.LONG),
                    ("biPlanes", _wt.WORD), ("biBitCount", _wt.WORD), ("biCompression", _wt.DWORD),
                    ("biSizeImage", _wt.DWORD), ("biXPelsPerMeter", _wt.LONG),
                    ("biYPelsPerMeter", _wt.LONG), ("biClrUsed", _wt.DWORD), ("biClrImportant", _wt.DWORD)]


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
