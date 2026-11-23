# -*- coding: utf-8 -*-
"""
Modo NATURAL en vivo:
  · Pintor       (hilo de trabajo) analiza cada línea UNA vez (color, contorno, sombra, letra
                 más parecida…), detecta si el fondo se mueve y compone una imagen por cuadro
                 de texto: el original borrado (o un vidrio esmerilado) + la traducción.
  · CapaNatural  (hilo de la interfaz) muestra cada imagen en su propia ventana de Windows con
                 transparencia real por píxel (UpdateLayeredWindow): deja pasar los clics, no sale
                 en las capturas y aparece/desaparece con un fundido suave.
"""
import hashlib
import os
import sys
import time

import numpy as np
from PIL import Image

import estilo
from nucleo import log

ES_WINDOWS = sys.platform.startswith("win")


# ============================================================================
# Pintor: análisis y composición (fuera del hilo de la interfaz)
# ============================================================================
class Pintor:
    PRESUPUESTO = 0.35        # segundos de análisis por vuelta (lo demás, en la siguiente)

    def __init__(self, ruta_exe=None, preferida=None):
        self.ruta_exe = ruta_exe
        self.preferida = preferida if (preferida and os.path.isfile(preferida)) else None
        self._catalogo = None
        self.respaldo = None
        self.fuentes_juego = []
        self.lista_corta = []          # fuentes que ya acertaron en esta sesión
        self.lineas = {}               # id de línea -> análisis guardado
        self.pendiente = False         # quedaron líneas sin analizar por falta de tiempo

    def catalogo(self):
        if self._catalogo is None:
            try:
                self.fuentes_juego = estilo.buscar_fuentes_juego(self.ruta_exe) if self.ruta_exe else []
            except Exception:
                log.exception("Buscando las fuentes del juego")
                self.fuentes_juego = []
            if self.fuentes_juego:
                log.info("Fuentes del juego: %s", [os.path.basename(f) for f in self.fuentes_juego])
            self._catalogo = estilo.CatalogoFuentes(self.fuentes_juego)
            r = estilo._buscar_archivo("NotoSans.ttf") or estilo.equivalente_latina("sans")[0]
            self.respaldo = estilo.cargar(r, 30, None) if r else None
        return self._catalogo

    # ---------- ¿se mueve el fondo alrededor del texto? ----------
    @staticmethod
    def _zona(arr, caja, otras):
        H, W = arr.shape[:2]
        x0, y0, x1, y1 = caja
        m = max(4, int((y1 - y0) * 0.5))
        rx0, ry0, rx1, ry1 = max(0, x0 - m), max(0, y0 - m), min(W, x1 + m), min(H, y1 + m)
        if rx1 - rx0 < 4 or ry1 - ry0 < 4:
            return None
        g = arr[ry0:ry1:2, rx0:rx1:2].astype(np.int16).mean(2)
        mascara = np.ones(g.shape, bool)
        for o in list(otras) + [caja]:          # el texto (propio y ajeno) no cuenta
            a0, b0 = max(0, (int(o[0]) - 2 - rx0) // 2), max(0, (int(o[1]) - 2 - ry0) // 2)
            a1, b1 = max(0, (int(o[2]) + 3 - rx0) // 2), max(0, (int(o[3]) + 3 - ry0) // 2)
            mascara[b0:b1, a0:a1] = False
        if mascara.sum() < 20:
            return None
        return (rx0, ry0, rx1, ry1), g, mascara

    def _se_mueve(self, arr, e, otras):
        ref = e.get("zona")
        if ref is None:
            return False
        (rx0, ry0, rx1, ry1), g0, mascara = ref
        if ry1 > arr.shape[0] or rx1 > arr.shape[1]:
            return True
        g = arr[ry0:ry1:2, rx0:rx1:2].astype(np.int16).mean(2)
        if g.shape != g0.shape:
            return True
        d = np.abs(g - g0)[mascara]
        return d.mean() > 9 and (d > 24).mean() > 0.15

    # ---------- fuente ----------
    def _fuente(self, est, linea, cat):
        texto_fila = linea.filas[0][0] if getattr(linea, "filas", None) else linea.texto
        es_jp = linea.idioma == "ja"
        try:
            r = estilo.decidir_fuente(est, texto_fila, cat, es_jp=es_jp, preferida=self.preferida,
                                      lista_corta=self.lista_corta)
        except Exception:
            log.exception("Eligiendo la fuente")
            ruta, inst = estilo.equivalente_latina("sans_b")
            return (ruta, inst, estilo._alto_fila(est) * 0.85), 0.0
        ruta, inst, tam, nombre, puntos = r
        if not es_jp and puntos >= 0.62 and (ruta, inst) not in self.lista_corta:
            self.lista_corta.insert(0, (ruta, inst))
            del self.lista_corta[4:]
        return (ruta, inst, tam), puntos

    @staticmethod
    def _firma_estilo(est, jp):
        """Líneas con el mismo aspecto (alto, color, contorno, píxel) usan la misma letra."""
        return (round(estilo._alto_fila(est) / 3), tuple(int(c) // 40 for c in est["relleno"]),
                bool(est["pixel"]), bool(est.get("contorno")), jp)

    # ---------- composición ----------
    def componer(self, img, lineas, otras_cajas, modo_cfg="natural"):
        """lineas: Linea del seguidor ya traducidas (coordenadas de la captura).
        Devuelve [{'ids', 'img', 'x', 'y', 'firma'}] en coordenadas de la captura."""
        arr = np.asarray(img.convert("RGB"))
        cat = self.catalogo()
        t0 = time.perf_counter()
        ahora = time.time()
        self.pendiente = False
        otras = [tuple(int(round(v)) for v in c) for c in otras_cajas]
        piezas, vivos = [], set()
        for l in lineas:
            if not l.trad:
                continue
            vivos.add(l.id)
            caja = tuple(int(round(v)) for v in l.caja)
            ajenas = [o for o in otras if o != caja]
            e = self.lineas.get(l.id)
            if e is not None and e["provisional"] and time.perf_counter() - t0 < self.PRESUPUESTO:
                e = None                                   # ahora sí hay tiempo: analizar bien
            if e is None or e["caja"] != caja or e["texto"] != l.texto:
                provisional = time.perf_counter() - t0 > self.PRESUPUESTO
                est = None
                if not provisional:
                    try:
                        est = estilo.analizar(arr, caja, ajenas, getattr(l, "filas", None))
                    except Exception:
                        log.exception("Analizando el estilo de «%s»", l.texto[:40])
                if est is None:
                    est = estilo.est_basico(caja, getattr(l, "filas", None))
                if provisional:
                    self.pendiente = True
                fuente, puntos = self._fuente(est, l, cat)
                e = {"caja": caja, "texto": l.texto, "est": est, "provisional": provisional,
                     "fuente": fuente, "puntos": puntos, "zona": self._zona(arr, caja, ajenas),
                     "animado": 0.0}
                self.lineas[l.id] = e
            elif self._se_mueve(arr, e, ajenas):
                e["animado"] = ahora + 4.0                 # fondo en movimiento: vidrio un rato
                e["zona"] = self._zona(arr, caja, ajenas)
            est = e["est"]
            if modo_cfg == "vidrio" or est.get("basico") or ahora < e["animado"]:
                modo = "suave"
            else:
                modo = estilo.modo_automatico(est)
            piezas.append({"id": l.id, "est": est, "trad": l.trad, "fuente": e["fuente"],
                           "modo": modo, "jp": l.idioma == "ja", "caja_ocr": caja, "otras": ajenas})
        for k in list(self.lineas):
            if k not in vivos:
                del self.lineas[k]
        # una lectura con errores puede elegir mal la letra: entre líneas del mismo aspecto
        # manda la que más se parece al original
        mejores = {}
        for p in piezas:
            e = self.lineas[p["id"]]
            f = self._firma_estilo(e["est"], p["jp"])
            if f not in mejores or e["puntos"] > mejores[f][0]:
                mejores[f] = (e["puntos"], e["fuente"])
        for p in piezas:
            p["fuente"] = mejores[self._firma_estilo(self.lineas[p["id"]]["est"], p["jp"])][1]
        res = estilo.componer_piezas(arr, piezas, lambda p: p["fuente"], self.respaldo)
        for r in res:
            r["firma"] = (r["x"], r["y"], r["img"].size, hashlib.md5(r["img"].tobytes()).hexdigest())
        return res


# ============================================================================
# Ventanas con transparencia por píxel (Windows)
# ============================================================================
if ES_WINDOWS:
    import ctypes
    import ctypes.wintypes as wt

    _u32 = ctypes.WinDLL("user32", use_last_error=True)
    _g32 = ctypes.WinDLL("gdi32", use_last_error=True)
    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _vp = ctypes.c_void_p

    class _WNDCLASSEXW(ctypes.Structure):
        _fields_ = [("cbSize", wt.UINT), ("style", wt.UINT), ("lpfnWndProc", _vp),
                    ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int), ("hInstance", _vp),
                    ("hIcon", _vp), ("hCursor", _vp), ("hbrBackground", _vp),
                    ("lpszMenuName", wt.LPCWSTR), ("lpszClassName", wt.LPCWSTR), ("hIconSm", _vp)]

    class _BLEND(ctypes.Structure):
        _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                    ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]

    class _BIH(ctypes.Structure):
        _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                    ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                    ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG),
                    ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD)]

    _k32.GetModuleHandleW.restype = _vp
    _k32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
    _u32.RegisterClassExW.restype = wt.ATOM
    _u32.RegisterClassExW.argtypes = [ctypes.POINTER(_WNDCLASSEXW)]
    _u32.CreateWindowExW.restype = _vp
    _u32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int, ctypes.c_int,
                                     ctypes.c_int, ctypes.c_int, _vp, _vp, _vp, _vp]
    _u32.DestroyWindow.argtypes = [_vp]
    _u32.ShowWindow.argtypes = [_vp, ctypes.c_int]
    _u32.SetWindowPos.argtypes = [_vp, _vp, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.UINT]
    _u32.UpdateLayeredWindow.restype = wt.BOOL
    _u32.UpdateLayeredWindow.argtypes = [_vp, _vp, ctypes.POINTER(wt.POINT), ctypes.POINTER(wt.SIZE), _vp,
                                         ctypes.POINTER(wt.POINT), wt.DWORD, ctypes.POINTER(_BLEND), wt.DWORD]
    _u32.GetDC.restype = _vp
    _u32.GetDC.argtypes = [_vp]
    _u32.ReleaseDC.argtypes = [_vp, _vp]
    _u32.SetWindowDisplayAffinity.argtypes = [_vp, wt.DWORD]
    _u32.DefWindowProcW.restype = ctypes.c_ssize_t
    _u32.DefWindowProcW.argtypes = [_vp, wt.UINT, wt.WPARAM, wt.LPARAM]
    _g32.CreateCompatibleDC.restype = _vp
    _g32.CreateCompatibleDC.argtypes = [_vp]
    _g32.CreateDIBSection.restype = _vp
    _g32.CreateDIBSection.argtypes = [_vp, ctypes.POINTER(_BIH), wt.UINT, ctypes.POINTER(_vp), _vp, wt.DWORD]
    _g32.SelectObject.restype = _vp
    _g32.SelectObject.argtypes = [_vp, _vp]
    _g32.DeleteObject.argtypes = [_vp]
    _g32.DeleteDC.argtypes = [_vp]

    _CLASE = "TraductorCapaNatural"
    _EX = 0x00080000 | 0x00000020 | 0x00000080 | 0x08000000 | 0x00000008   # LAYERED|TRANSPARENT|TOOL|NOACTIVATE|TOPMOST
    _WS_POPUP = 0x80000000
    _clase_lista = False

    def _registrar_clase():
        global _clase_lista
        if _clase_lista:
            return
        wc = _WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(_WNDCLASSEXW)
        wc.lpfnWndProc = ctypes.cast(_u32.DefWindowProcW, _vp).value    # sin código Python por mensaje
        wc.hInstance = _k32.GetModuleHandleW(None)
        wc.lpszClassName = _CLASE
        if not _u32.RegisterClassExW(ctypes.byref(wc)) and ctypes.get_last_error() != 1410:
            raise ctypes.WinError(ctypes.get_last_error())
        _clase_lista = True


def _premultiplicar(img):
    """RGBA (PIL) → bytes BGRA premultiplicados, como los quiere UpdateLayeredWindow."""
    a = np.asarray(img, dtype=np.uint16)
    al = a[..., 3]
    out = np.empty(a.shape, np.uint8)
    out[..., 0] = (a[..., 2] * al + 127) // 255
    out[..., 1] = (a[..., 1] * al + 127) // 255
    out[..., 2] = (a[..., 0] * al + 127) // 255
    out[..., 3] = al
    return out.tobytes()


class _Pieza:
    """Una ventana transparente con la imagen de un cuadro de texto."""

    def __init__(self, capa):
        self.capa = capa
        self.hwnd = None
        self.alfa, self.objetivo = 0, 255
        self.saliendo = False
        self.firma = None
        self.x = self.y = 0
        self.img = None
        self.mostrada = False
        if ES_WINDOWS:
            _registrar_clase()
            self.hwnd = _u32.CreateWindowExW(_EX, _CLASE, "Traducción", _WS_POPUP, 0, 0, 1, 1,
                                             None, None, _k32.GetModuleHandleW(None), None)
            if not self.hwnd:
                raise ctypes.WinError(ctypes.get_last_error())
            if capa.excluir:
                _u32.SetWindowDisplayAffinity(self.hwnd, 0x11)

    def subir(self, img, x, y, alfa):
        self.img, self.x, self.y, self.alfa = img, x, y, alfa
        if not ES_WINDOWS:
            return
        w, h = img.size
        datos = _premultiplicar(img)
        bih = _BIH()
        bih.biSize, bih.biWidth, bih.biHeight = ctypes.sizeof(_BIH), w, -h
        bih.biPlanes, bih.biBitCount = 1, 32
        pantalla = _u32.GetDC(None)
        mdc = _g32.CreateCompatibleDC(pantalla)
        bits = _vp()
        bmp = _g32.CreateDIBSection(mdc, ctypes.byref(bih), 0, ctypes.byref(bits), None, 0)
        try:
            ctypes.memmove(bits, datos, len(datos))
            viejo = _g32.SelectObject(mdc, bmp)
            mezcla = _BLEND(0, 0, max(0, min(255, int(alfa))), 1)
            _u32.UpdateLayeredWindow(self.hwnd, pantalla, ctypes.byref(wt.POINT(int(x), int(y))),
                                     ctypes.byref(wt.SIZE(w, h)), mdc, ctypes.byref(wt.POINT(0, 0)),
                                     0, ctypes.byref(mezcla), 2)
            _g32.SelectObject(mdc, viejo)
        finally:
            _g32.DeleteObject(bmp)
            _g32.DeleteDC(mdc)
            _u32.ReleaseDC(None, pantalla)
        if not self.mostrada and self.capa.visible_:
            _u32.ShowWindow(self.hwnd, 4)            # SW_SHOWNOACTIVATE
            self.mostrada = True

    def poner_alfa(self, alfa):
        self.alfa = max(0, min(255, int(alfa)))
        if not ES_WINDOWS or self.img is None:
            return
        mezcla = _BLEND(0, 0, self.alfa, 1)
        if not _u32.UpdateLayeredWindow(self.hwnd, None, None, None, None, None, 0, ctypes.byref(mezcla), 2):
            self.subir(self.img, self.x, self.y, self.alfa)

    def mover(self, x, y):
        self.x, self.y = x, y
        if ES_WINDOWS:
            _u32.SetWindowPos(self.hwnd, None, int(x), int(y), 0, 0, 0x0001 | 0x0004 | 0x0010)

    def mostrar(self, si):
        if ES_WINDOWS and self.hwnd:
            _u32.ShowWindow(self.hwnd, 4 if si else 0)
            self.mostrada = si

    def arriba(self):
        if ES_WINDOWS and self.hwnd:
            _u32.SetWindowPos(self.hwnd, _vp(-1), 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)

    def permitir_captura(self, si):
        if ES_WINDOWS and self.hwnd:
            _u32.SetWindowDisplayAffinity(self.hwnd, 0x0 if si else 0x11)

    def destruir(self):
        if ES_WINDOWS and self.hwnd:
            _u32.DestroyWindow(self.hwnd)
        self.hwnd = None


class CapaNatural:
    """Todas las piezas de una sesión. Se usa SOLO desde el hilo de la interfaz (Tk)."""
    PASO_MS = 16
    ENTRA, SALE = 36, 46          # cuánto sube/baja la opacidad por paso (≈ 120 ms)

    def __init__(self, app):
        self.app = app
        self.piezas = {}
        self.visible_ = True
        self.excluir = not getattr(app, "sin_exclusion", False)
        self._animando = False

    @property
    def fundido(self):
        return bool(self.app.cfg.get("fundido", True))

    def actualizar(self, piezas, origen):
        """piezas en coordenadas de la captura; origen = esquina de la captura en la pantalla."""
        ox, oy = origen
        nuevas = {"-".join(map(str, p["ids"])): p for p in piezas}
        for clave, v in list(self.piezas.items()):
            if clave not in nuevas:
                self._quitar(clave, v)
        for clave, p in nuevas.items():
            x, y = ox + p["x"], oy + p["y"]
            v = self.piezas.get(clave)
            try:
                if v is None:
                    v = _Pieza(self)
                    self.piezas[clave] = v
                    v.subir(p["img"], x, y, 0 if self.fundido else 255)
                    v.objetivo = 255
                elif v.firma != p["firma"] or (v.x, v.y) != (x, y):
                    v.subir(p["img"], x, y, v.alfa)
                if v.saliendo:
                    v.saliendo, v.objetivo = False, 255
                v.firma = p["firma"]
            except Exception:
                log.exception("Mostrando una pieza")
        self._animar()

    def _quitar(self, clave, v):
        if self.fundido and self.visible_ and v.alfa > 0:
            v.saliendo, v.objetivo = True, 0
        else:
            v.destruir()
            self.piezas.pop(clave, None)

    def _animar(self):
        if not self._animando and any(v.alfa != v.objetivo for v in self.piezas.values()):
            self._animando = True
            self.app.root.after(self.PASO_MS, self._paso)

    def _paso(self):
        self._animando = False
        for clave, v in list(self.piezas.items()):
            if v.alfa < v.objetivo:
                v.poner_alfa(min(v.objetivo, v.alfa + self.ENTRA))
            elif v.alfa > v.objetivo:
                v.poner_alfa(max(v.objetivo, v.alfa - self.SALE))
            if v.saliendo and v.alfa <= 0:
                v.destruir()
                self.piezas.pop(clave, None)
        self._animar()

    def visible(self, si):
        """Ocultar o mostrar todo sin perder nada (programa en segundo plano, ver el original)."""
        if si == self.visible_:
            return
        self.visible_ = si
        for clave, v in list(self.piezas.items()):
            if v.saliendo:
                v.destruir()
                self.piezas.pop(clave, None)
            else:
                v.mostrar(si)

    def mover(self, dx, dy):
        for v in self.piezas.values():
            v.mover(v.x + dx, v.y + dy)

    def subir(self):
        for v in self.piezas.values():
            v.arriba()

    def limpiar(self):
        for clave, v in list(self.piezas.items()):
            self._quitar(clave, v)
        self._animar()

    def permitir_captura(self, si):
        """Solo para pruebas: que las piezas salgan en una captura de pantalla."""
        for v in self.piezas.values():
            v.permitir_captura(si)

    def instantanea(self, fondo, origen):
        """Pega las piezas actuales sobre una captura (pruebas y vista previa)."""
        img = fondo.convert("RGBA")
        for v in self.piezas.values():
            if v.img is not None and not v.saliendo:
                estilo._pegar(img, v.img, int(v.x - origen[0]), int(v.y - origen[1]))
        return img.convert("RGB")

    def cerrar(self):
        for v in self.piezas.values():
            v.destruir()
        self.piezas.clear()
