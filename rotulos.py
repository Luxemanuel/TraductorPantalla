"""Rótulos de traducción: un cuadro oscuro con la traducción en una letra clara y común, encima
del texto original. Sin imitar la letra ni el fondo del juego: rápido, estable y legible.

Cada línea (párrafo) es su propio rótulo: cambiar una no toca las demás. El mismo texto en el
mismo sitio da exactamente la misma imagen, así que no se vuelve a subir ni parpadea.
"""
import functools
import os
import re

from PIL import Image, ImageDraw, ImageFont

import marca

FONDO = (18, 20, 26, 247)          # casi opaco: el original no se trasluce
BORDE = (255, 255, 255, 28)
TEXTO = (246, 246, 242, 255)
RELLENO = 4                     # margen dentro del cuadro
MIN_PX = 10

# letras de Windows para escrituras que la letra incluida no tiene
_POR_IDIOMA = {
    "ar": ["segoeui.ttf", "arial.ttf"], "hi": ["Nirmala.ttf", "mangal.ttf"],
    "th": ["leelawui.ttf", "tahoma.ttf"], "ko": ["malgun.ttf", "gulim.ttc"],
    "zh": ["msyh.ttc", "simsun.ttc"], "ja": ["YuGothM.ttc", "meiryo.ttc", "msgothic.ttc"],
}


_SIN_ESPACIOS = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af\u0e00-\u0e7f]")   # escrituras sin espacios

# símbolos frecuentes en los juegos que la letra incluida no tiene (saldrían como □)
_SUSTITUTOS = {"→": "›", "⇒": "›", "➔": "›", "➜": "›", "▶": "›", "►": "›", "▷": "›", "←": "‹", "⇐": "‹",
               "◀": "‹", "◄": "‹", "◁": "‹", "↑": "^", "↓": "v", "★": "*", "☆": "*", "✦": "*", "✧": "*",
               "●": "•", "○": "•", "◆": "•", "◇": "•", "■": "•", "□": "•", "※": "*", "～": "~", "〜": "~"}


@functools.lru_cache(maxsize=4096)
def _tiene(ch, idioma):
    """¿La letra de los rótulos tiene este carácter? (si no, se dibuja un □)"""
    if ch.isspace():
        return True
    f = fuente(40, idioma)
    try:
        m = f.getmask(ch)
        vacio = f.getmask("\uffff")
        return not (m.size == vacio.size and bytes(m) == bytes(vacio))
    except Exception:
        return True


def limpiar(texto, idioma="es"):
    """La traducción sin caracteres que la letra no puede dibujar."""
    out, cambio = [], False
    for ch in texto:
        if _tiene(ch, idioma):
            out.append(ch)
            continue
        cambio = True
        if ch in _SUSTITUTOS and _tiene(_SUSTITUTOS[ch], idioma):
            out.append(_SUSTITUTOS[ch])
    limpio = " ".join("".join(out).split())
    return limpio if cambio and limpio else texto


@functools.lru_cache(maxsize=96)
def fuente(px, idioma="es", ancho=100):
    """La letra de los rótulos (Noto Sans seminegrita, incluida). ancho < 100: más estrecha."""
    if idioma not in _POR_IDIOMA:
        ruta = os.path.join(marca.CARPETA_FUENTES, "NotoSans.ttf")
        try:
            f = ImageFont.truetype(ruta, px)
            try:
                f.set_variation_by_axes([600, ancho])
            except Exception:
                pass
            return f
        except Exception:
            pass
    for archivo in _POR_IDIOMA.get(idioma, []) + ["segoeuib.ttf", "segoeui.ttf", "arialbd.ttf", "arial.ttf",
                                                   "DejaVuSans-Bold.ttf", "DejaVuSans.ttf"]:
        try:
            return ImageFont.truetype(archivo, px)
        except Exception:
            continue
    try:
        return ImageFont.load_default(size=px)
    except TypeError:
        return ImageFont.load_default()


def _largo(f, t):
    memo = f.__dict__.setdefault("_largos", {})
    v = memo.get(t)
    if v is None:
        if len(memo) > 3000:
            memo.clear()
        v = memo[t] = f.getlength(t)
    return v


def _envolver(texto, f, ancho):
    """Reparte en líneas de hasta 'ancho'. Sin espacios (chino, japonés…): por caracteres. Una
    sola palabra de letras latinas no se parte nunca («Archi-vos» en dos renglones no se lee)."""
    t = texto.strip()
    if " " in t:
        trozos, sep = t.split(), " "
    elif _SIN_ESPACIOS.search(t):
        trozos, sep = list(t), ""
    else:
        trozos, sep = [t], ""
    esp = _largo(f, sep) if sep else 0.0
    lineas, actual, largo = [], [], 0.0
    for p in trozos:
        lp = _largo(f, p)
        if not actual or largo + esp + lp <= ancho:
            largo = (largo + esp + lp) if actual else lp
            actual.append(p)
        else:
            lineas.append(sep.join(actual))
            actual, largo = [p], lp
    if actual:
        lineas.append(sep.join(actual))
    return lineas or [""]


def _repartir(texto, f, filas, ancho):
    """Las líneas más parejas que quepan en 'filas' con 'ancho'. None si no cabe."""
    ls = _envolver(texto, f, ancho)
    if len(ls) > filas or max(_largo(f, l) for l in ls) > ancho + 0.5:
        return None
    lo, hi = ancho * 0.5, ancho                    # estrechar mientras quepa: renglones parejos
    for _ in range(8):
        mid = (lo + hi) / 2
        prueba = _envolver(texto, f, mid)
        if len(prueba) <= len(ls) and max(_largo(f, l) for l in prueba) <= mid + 0.5:
            ls, hi = prueba, mid
        else:
            lo = mid
    return ls


@functools.lru_cache(maxsize=256)
def maquetar(texto, ancho, alto_fila, filas, filas_max, idioma, max_ancho):
    """(px, ancho_letra, lineas). Orden: mismo tamaño que el original en su ancho (con los
    renglones que quepan sin tapar otro texto), letra algo más estrecha, letra algo más pequeña;
    solo después, un cuadro más ancho; al final, letra más pequeña."""
    px0 = max(MIN_PX, min(44, int(round(alto_fila * 0.82))))
    for px in range(px0, MIN_PX - 1, -1):
        anchos = (ancho,) if px > 0.8 * px0 or max_ancho <= ancho * 1.05 else (ancho, max_ancho)
        for a in anchos:
            for estrecha in (100, 88, 78):
                f = fuente(px, idioma, estrecha)
                for n in range(filas, max(filas, filas_max) + 1):
                    ls = _repartir(texto, f, n, a)
                    if ls:
                        return px, estrecha, tuple(ls)
    f = fuente(MIN_PX, idioma, 78)
    return MIN_PX, 78, tuple(_envolver(texto, f, max_ancho))


@functools.lru_cache(maxsize=256)
def dibujar(lineas, px, estrecha, idioma, ancho_min, alto_min):
    f = fuente(px, idioma, estrecha)
    asc, desc = f.getmetrics()
    paso = int(round((asc + desc) * 1.04))
    ancho = max(ancho_min, int(max(_largo(f, l) for l in lineas)) + 2 * RELLENO + 2)
    alto = max(alto_min, paso * len(lineas) + 2 * RELLENO)
    im = Image.new("RGBA", (ancho, alto), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((0, 0, ancho - 1, alto - 1), radius=min(7, alto // 4), fill=FONDO, outline=BORDE)
    y = (alto - paso * len(lineas)) // 2
    for l in lineas:
        d.text((RELLENO + 1, y), l, font=f, fill=TEXTO)
        y += paso
    return im


def _alto_fila(linea):
    filas = [c for _, c in (linea.filas or []) if c]
    if filas:
        altos = sorted(c[3] - c[1] for c in filas)
        return float(altos[len(altos) // 2]), len(filas)
    return float(linea.caja[3] - linea.caja[1]), 1


class Rotulador:
    """Hace los rótulos de las líneas traducidas. Misma forma que espera CapaNatural."""
    pendiente = False
    vidrio = False
    cambios = False

    def __init__(self, idioma="es"):
        self.idioma = idioma

    def componer(self, img, lineas, otras=None):
        piezas = []
        W, H = img.size
        for l in lineas:
            if not l.trad:
                continue
            x0, y0, x1, y1 = (int(round(v)) for v in l.caja)
            alto_fila, filas = _alto_fila(l)
            ancho = max(20, x1 - x0)
            max_ancho = max(ancho, min(W - x0 + RELLENO, int(ancho * 1.6) + 40))
            # renglones de más: solo hasta el siguiente texto de debajo (no taparlo)
            debajo = [c[1] for c in list(otras or []) + [m.caja for m in lineas if m is not l]
                      if c[1] >= y1 - 2 and min(c[2], x1) - max(c[0], x0) > 0]
            libre = (min(debajo) if debajo else H) - y1
            paso = max(alto_fila * 1.25, (y1 - y0) / max(1, filas))
            filas_max = filas + max(0, min(3, int(libre // paso)))
            px, estrecha, ls = maquetar(limpiar(l.trad.strip(), self.idioma), ancho, round(alto_fila), filas, filas_max,
                                        self.idioma, max_ancho)
            im = dibujar(ls, px, estrecha, self.idioma, ancho + 2 * RELLENO, (y1 - y0) + 2 * RELLENO)
            x, y = x0 - RELLENO, y0 - RELLENO
            if len(ls) == 1 and im.height > (y1 - y0) + 2 * RELLENO:
                y = int(round((y0 + y1) / 2 - im.height / 2))   # una sola línea más alta: centrada
            x = max(0, min(x, W - im.width))
            y = max(0, min(y, H - im.height))
            piezas.append({"ids": (l.id,), "x": x, "y": y, "img": im,
                           "firma": (l.trad, ls, px, estrecha, im.size, x, y)})
        return piezas
