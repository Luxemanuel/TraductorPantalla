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
import math
import os
import sys
import time

import numpy as np
from PIL import Image

import estilo
from nucleo import log, log_limitado

ES_WINDOWS = sys.platform.startswith("win")


# ============================================================================
# Pintor: análisis y composición (fuera del hilo de la interfaz)
# ============================================================================
class Pintor:
    """Analiza y compone la traducción de cada línea. La LETRA se decide una sola vez por aspecto
    de texto (color, píxeles, japonés): todas las líneas que se parecen usan la misma, y la
    decisión se guarda en el perfil del juego para no tener que volver a buscarla."""
    PRESUPUESTO = 0.45        # segundos de análisis por vuelta (lo demás, en la siguiente)
    DIST_COLOR = 70           # colores más cercanos que esto = mismo aspecto de texto

    def __init__(self, ruta_exe=None, preferida=None, letras=None):
        self.ruta_exe = ruta_exe
        self.preferida = preferida if (preferida and os.path.isfile(preferida)) else None
        self._catalogo = None
        self.respaldo = None
        self.fuentes_juego = []
        self.lista_corta = []
        self.lineas = {}               # id de línea -> análisis guardado
        self.vidrio = set()            # líneas con el fondo en movimiento: vidrio hasta que se vayan
        self.pendiente = False         # quedaron líneas sin analizar por falta de tiempo
        self.decisiones = []           # letras decididas (ver _nueva_decision)
        self.cambios = False           # hay decisiones nuevas para guardar en el perfil
        for d in (letras or []):
            try:
                if d.get("ruta") and os.path.isfile(d["ruta"]):
                    self.decisiones.append({
                        "jp": bool(d["jp"]), "pixel": bool(d["pixel"]), "color": np.array(d["color"], float),
                        "alto": float(d["alto"]), "ruta": d["ruta"], "inst": d.get("inst"),
                        "puntos": float(d.get("puntos", 0.7)), "ratio": float(d.get("ratio", 0.85)),
                        "tams": [float(d["tam"])] if d.get("tam") else []})
            except (KeyError, TypeError, ValueError):
                continue

    def exportar(self):
        """Decisiones de letra para guardar en el perfil del juego."""
        return [{"jp": d["jp"], "pixel": d["pixel"], "color": [round(float(c), 1) for c in d["color"]],
                 "alto": round(d["alto"], 1), "ruta": d["ruta"], "inst": d["inst"], "puntos": round(d["puntos"], 3),
                 "ratio": round(d["ratio"], 4), "tam": round(float(np.median(d["tams"])), 1) if d["tams"] else None}
                for d in self.decisiones if not d.get("preferida") and d.get("ruta")]

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

    @staticmethod
    def _se_mueve(arr, e):
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

    # ---------- letra: una decisión por aspecto de texto ----------
    @staticmethod
    def _texto_fila(linea):
        return (linea.filas[0][0] if getattr(linea, "filas", None) else linea.texto).strip()

    def _decision_para(self, est, jp):
        """La decisión que corresponde a este aspecto (color parecido; si hay varias, la de
        tamaño más cercano). None si es un aspecto nuevo."""
        col = np.array(est["relleno"], float)
        alto = estilo._alto_fila(est)
        mejor = None
        for d in self.decisiones:
            if d["jp"] != jp or d["pixel"] != bool(est["pixel"]):
                continue
            dc = float(np.linalg.norm(col - d["color"]))
            if dc > self.DIST_COLOR:
                continue
            da = abs(math.log(max(alto, 1.0) / max(d["alto"], 1.0)))
            clave = dc / self.DIST_COLOR + da
            if mejor is None or clave < mejor[0]:
                mejor = (clave, d, da)
        return mejor

    def _nueva_decision(self, grupo, jp):
        """grupo: [(est, linea)] con el mismo aspecto → decide la letra con todas a la vez."""
        cat = self.catalogo()
        muestras = []
        for est, l in sorted(grupo, key=lambda x: -len(self._texto_fila(x[1])))[:2]:
            obj, txt = estilo._objetivo(est), self._texto_fila(l)
            if obj is not None and txt:
                muestras.append((obj, txt, estilo._alto_fila(est)))
        est0 = grupo[0][0]
        alto = float(np.median([estilo._alto_fila(e) for e, _ in grupo]))
        ruta = inst = None
        puntos, tams = 0.0, []
        if muestras:
            r = estilo.elegir_fuente_varias([(o, t) for o, t, _ in muestras], cat, es_jp=jp)
            if r:
                ruta, inst, nombre, puntos, origen, tams = r
                if jp:                                  # kanji → la letra latina equivalente
                    categoria = "sans_b" if (origen == "sans" and estilo._es_negrita(inst)) else origen
                    ruta, inst = estilo.equivalente_latina(categoria)
                    tams = [t * 0.95 for t in tams]
                elif puntos < 0.45:                     # nada se parece: una sans limpia
                    ruta, inst = estilo.equivalente_latina("sans_b" if est0["contorno"] else "sans")
                    tams = []
        if ruta is None:
            ruta, inst = estilo.equivalente_latina("sans_b" if (jp or est0["contorno"]) else "sans")
        ratio = (float(np.median([t / max(1.0, a) for t, (_, _, a) in zip(tams, muestras)]))
                 if tams else 0.85)
        d = {"jp": jp, "pixel": bool(est0["pixel"]), "ruta": ruta, "inst": inst, "puntos": float(puntos),
             "color": np.median([np.array(e["relleno"], float) for e, _ in grupo], 0), "alto": alto,
             "ratio": ratio, "tams": list(tams)}
        self.decisiones.append(d)
        self.cambios = True
        if not jp and puntos >= 0.62 and (ruta, inst) not in self.lista_corta:
            self.lista_corta.insert(0, (ruta, inst))
        log.info("Letra decidida: %s %s (parecido %.2f, %d línea(s))", os.path.basename(ruta or "?"),
                 inst or "", puntos, len(muestras))
        return d

    def _tam(self, d, est, linea, jp):
        """Tamaño para esta línea con la letra decidida, estable entre líneas del mismo aspecto."""
        alto = estilo._alto_fila(est)
        propio = None
        if not jp and not est.get("basico"):
            obj, txt = estilo._objetivo(est), self._texto_fila(linea)
            r = estilo._puntuar(obj, d["ruta"], d["inst"], txt) if (obj is not None and txt) else None
            if r:
                propio = r[1]
        if propio is None:
            propio = d["ratio"] * alto
        if d["tams"]:
            med = float(np.median(d["tams"]))
            if abs(propio - med) / med < 0.12:          # mismo tamaño de letra: el de siempre
                return med
        d["tams"] = (d["tams"] + [propio])[-15:]
        return propio

    def _letra(self, est, linea, jp, d):
        if est.get("basico") or d is None:
            ruta, inst = estilo.equivalente_latina("sans_b")
            return ruta, inst, estilo._alto_fila(est) * 0.85
        return d["ruta"], d["inst"], self._tam(d, est, linea, jp)

    # ---------- composición ----------
    def componer(self, img, lineas, otras_cajas):
        """lineas: Linea del seguidor ya traducidas (coordenadas de la captura).
        Devuelve [{'ids', 'img', 'x', 'y', 'firma'}] en coordenadas de la captura."""
        arr = np.asarray(img.convert("RGB"))
        t0 = time.perf_counter()
        self.pendiente = False
        otras = [tuple(int(round(v)) for v in c) for c in otras_cajas]
        vivos = {l.id for l in lineas if l.trad}
        for k in list(self.lineas):
            if k not in vivos:
                del self.lineas[k]
        self.vidrio &= vivos
        # 1) análisis de lo nuevo (o de lo que cambió)
        nuevas = []
        for l in lineas:
            if not l.trad:
                continue
            caja = tuple(int(round(v)) for v in l.caja)
            ajenas = [o for o in otras if o != caja]
            e = self.lineas.get(l.id)
            if (e is not None and l.id not in self.vidrio and max(abs(a - b) for a, b in zip(e["caja"], caja)) <= 8
                    and self._se_mueve(arr, e)):
                self.vidrio.add(l.id)                    # el fondo se mueve: vidrio, sin dudar
            if e is not None and e["caja"] == caja and e["texto"] == l.texto:
                continue
            if time.perf_counter() - t0 > self.PRESUPUESTO:
                self.pendiente = True                    # sigue en la próxima vuelta (sin cambios a medias)
                continue
            est = None
            try:
                est = estilo.analizar(arr, caja, ajenas, getattr(l, "filas", None))
            except Exception:
                log.exception("Analizando el estilo de «%s»", l.texto[:40])
            if est is None:
                est = estilo.est_basico(caja, getattr(l, "filas", None))
            e = {"caja": caja, "texto": l.texto, "est": est, "zona": self._zona(arr, caja, ajenas), "fuente": None}
            self.lineas[l.id] = e
            nuevas.append((l, e))
        # 2) letra: aspectos nuevos se deciden juntos (con varias líneas si las hay)
        sin_decidir = []
        for l, e in nuevas:
            est, jp = e["est"], l.idioma == "ja"
            if est.get("basico"):
                e["fuente"] = self._letra(est, l, jp, None)
                continue
            if self.preferida and not jp:
                d = self._preferida_decision()
            else:
                m = self._decision_para(est, jp)
                d = None
                if m is not None:
                    _, d, da = m
                    if da > math.log(1.3) and not self._encaja(d, est, l, jp):
                        d = None                         # mismo color pero otro tamaño y otra letra
            if d is None:
                sin_decidir.append((l, e, jp))
            else:
                e["fuente"] = self._letra(est, l, jp, d)
        while sin_decidir:
            l0, e0, jp0 = sin_decidir[0]
            grupo = [(l, e, jp) for l, e, jp in sin_decidir
                     if jp == jp0 and bool(e["est"]["pixel"]) == bool(e0["est"]["pixel"])
                     and np.linalg.norm(np.subtract(e["est"]["relleno"], e0["est"]["relleno"])) <= self.DIST_COLOR]
            d = self._nueva_decision([(e["est"], l) for l, e, _ in grupo], jp0)
            for l, e, jp in grupo:
                e["fuente"] = self._letra(e["est"], l, jp, d)
            sin_decidir = [x for x in sin_decidir if x not in grupo]
        # 3) piezas (solo de las líneas ya analizadas)
        piezas = []
        for l in lineas:
            e = self.lineas.get(l.id)
            if not l.trad or e is None or e.get("fuente") is None:
                continue
            est = e["est"]
            modo = "suave" if (est.get("basico") or l.id in self.vidrio) else estilo.modo_automatico(est)
            piezas.append({"id": l.id, "est": est, "trad": l.trad, "fuente": e["fuente"], "modo": modo,
                           "jp": l.idioma == "ja", "caja_ocr": e["caja"],
                           "otras": [o for o in otras if o != e["caja"]]})
        res = estilo.componer_piezas(arr, piezas, lambda p: p["fuente"], self.respaldo)
        for r in res:
            r["firma"] = (r["x"], r["y"], r["img"].size, hashlib.md5(r["img"].tobytes()).hexdigest())
        return res

    def _preferida_decision(self):
        for d in self.decisiones:
            if d.get("preferida"):
                return d
        d = {"jp": False, "pixel": False, "color": np.zeros(3), "alto": 20.0, "ruta": self.preferida,
             "inst": None, "puntos": 1.0, "ratio": 0.85, "tams": [], "preferida": True}
        self.decisiones.append(d)
        return d

    def _encaja(self, d, est, linea, jp):
        """¿La letra ya decidida sirve también para este texto de otro tamaño?"""
        if jp:
            return True
        obj, txt = estilo._objetivo(est), self._texto_fila(linea)
        if obj is None or not txt:
            return True
        r = estilo._puntuar(obj, d["ruta"], d["inst"], txt)
        return bool(r) and r[0] >= 0.4


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
