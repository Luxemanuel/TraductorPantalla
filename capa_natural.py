# -*- coding: utf-8 -*-
"""
Capa de rótulos en vivo: cada traducción en su propia ventana de Windows con transparencia real
por píxel (UpdateLayeredWindow). Deja pasar los clics, no sale en las capturas y aparece o
desaparece con un fundido corto. Los rótulos los dibuja rotulos.Rotulador.
"""
import sys

import numpy as np

from nucleo import log_limitado

ES_WINDOWS = sys.platform.startswith("win")


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
    ENTRA, SALE = 52, 64          # cuánto sube/baja la opacidad por paso (≈ 80 ms: sigue el ritmo)

    def __init__(self, app):
        self.app = app
        self.piezas = {}
        self.visible_ = True
        self.excluir = not getattr(app, "sin_exclusion", False)
        self._animando = False

    fundido = True

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
                log_limitado("pieza", "Mostrando una pieza")
        self._animar()

    def quitar_ids(self, ids):
        """Quita ya las piezas de esas líneas (su texto cambió o se fue)."""
        for clave, v in list(self.piezas.items()):
            if any(int(i) in ids for i in clave.split("-") if i.isdigit()):
                self._quitar(clave, v)
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
        self.capturable = si          # mientras salgan en las capturas, no sirven para comparar
        for v in self.piezas.values():
            v.permitir_captura(si)

    def instantanea(self, fondo, origen):
        """Pega las piezas actuales sobre una captura (pruebas y vista previa)."""
        img = fondo.convert("RGBA")
        for v in self.piezas.values():
            if v.img is not None and not v.saliendo:
                img.alpha_composite(v.img.convert("RGBA"), (max(0, int(v.x - origen[0])), max(0, int(v.y - origen[1]))))
        return img.convert("RGB")

    def cerrar(self):
        for v in self.piezas.values():
            v.destruir()
        self.piezas.clear()
