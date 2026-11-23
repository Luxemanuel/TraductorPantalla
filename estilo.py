# -*- coding: utf-8 -*-
"""
Estilo NATURAL: la traducción reemplaza al texto original como si fuera parte del juego.

  analizar()        separa letra y fondo: color de la letra, contorno, sombra, letra de
                    píxeles, filas, alineación y tipo de fondo (liso/degradado o con textura).
  CatalogoFuentes   fuentes disponibles (las del propio juego, las de Windows y las incluidas).
  elegir_fuente()   dibuja el mismo texto en cada fuente y se queda con la más parecida.
  reconstruir()     borra SOLO los trazos de las letras rellenando con el fondo de alrededor.
  componer_piezas() una imagen por cuadro de texto: borrado (o vidrio) + traducción con el
                    mismo color/contorno/sombra, en el mismo hueco.

Todo trabaja sobre recortes pequeños (una frase), con numpy y Pillow: unos milisegundos.
"""
import functools
import hashlib
import io
import math
import os
import pickle
import re
import sys
import tempfile
import zlib

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ES_WINDOWS = sys.platform.startswith("win")


# ============================================================================
# Utilidades de máscaras (sin OpenCV)
# ============================================================================
def _erosion(m, k=1):
    for _ in range(k):
        p = np.pad(m, 1, constant_values=False)
        m = p[1:-1, 1:-1] & p[:-2, 1:-1] & p[2:, 1:-1] & p[1:-1, :-2] & p[1:-1, 2:]
    return m


def _dilatar(m, k=1):
    for _ in range(k):
        p = np.pad(m, 1, constant_values=False)
        m = (p[1:-1, 1:-1] | p[:-2, 1:-1] | p[2:, 1:-1] | p[1:-1, :-2] | p[1:-1, 2:]
             | p[:-2, :-2] | p[2:, 2:] | p[:-2, 2:] | p[2:, :-2])
    return m


def _desplazar(m, dx, dy):
    out = np.zeros_like(m)
    h, w = m.shape
    ys, yd = (slice(0, h - dy), slice(dy, h)) if dy >= 0 else (slice(-dy, h), slice(0, h + dy))
    xs, xd = (slice(0, w - dx), slice(dx, w)) if dx >= 0 else (slice(-dx, w), slice(0, w + dx))
    out[yd, xd] = m[ys, xs]
    return out


def _otsu(v):
    v = v[np.isfinite(v)]
    if v.size < 10:
        return float(v.mean()) if v.size else 0.0
    hist, bordes = np.histogram(v, bins=64)
    p = hist / max(1, hist.sum())
    w0 = np.cumsum(p)
    mu = np.cumsum(p * np.arange(64))
    sb = (mu[-1] * w0 - mu) ** 2 / (w0 * (1 - w0) + 1e-9)
    return float(bordes[int(np.argmax(sb)) + 1])


def _kmeans(x, k, iters=10, semilla=3):
    """k-medias pequeño (x: N x 3). Inicio por puntos lejanos, robusto y determinista."""
    if len(x) == 0:
        return np.zeros((0, 3)), np.zeros(0, int)
    rng = np.random.default_rng(semilla)
    if len(x) > 4000:
        x_init = x[rng.choice(len(x), 4000, replace=False)]
    else:
        x_init = x
    k = min(k, len(x_init))
    c = [np.median(x_init, 0)]
    for _ in range(1, k):
        d = np.min(((x_init[:, None, :] - np.array(c)[None]) ** 2).sum(2), 1)
        c.append(x_init[int(np.argmax(d))])
    c = np.array(c, float)
    et = np.zeros(len(x), int)
    for _ in range(iters):
        d = ((x[:, None, :] - c[None]) ** 2).sum(2)
        et = d.argmin(1)
        for j in range(k):
            sel = x[et == j]
            if len(sel):
                c[j] = sel.mean(0)
    return c, et


def _grosor_contorno(m_rell, m_seg):
    """Cuántos píxeles hay que crecer desde el relleno para cubrir el contorno."""
    total = max(1, m_seg.sum())
    for k in range(1, 9):
        if (_dilatar(m_rell, k) & m_seg).sum() >= 0.85 * total:
            return k
    return 8


def _huecos(m, max_iter=400):
    """Píxeles encerrados por la máscara (no alcanzables desde el borde del recorte)."""
    libre = ~m
    alcanzado = np.zeros_like(m)
    alcanzado[0, :], alcanzado[-1, :], alcanzado[:, 0], alcanzado[:, -1] = libre[0, :], libre[-1, :], libre[:, 0], libre[:, -1]
    for _ in range(max_iter):
        nuevo = _dilatar(alcanzado, 1) & libre
        nuevo = nuevo | alcanzado
        if (nuevo == alcanzado).all():
            break
        alcanzado = nuevo
    return libre & ~alcanzado


def _plano(crop, mascara):
    """Ajusta un degradado lineal (por canal) a los píxeles de 'mascara'. Devuelve (coef, rms)."""
    ys, xs = np.nonzero(mascara)
    if len(xs) < 20:
        return None, 999.0
    if len(xs) > 6000:
        sel = np.random.default_rng(1).choice(len(xs), 6000, replace=False)
        ys, xs = ys[sel], xs[sel]
    A = np.stack([np.ones_like(xs), xs, ys], 1).astype(np.float32)
    v = crop[ys, xs]
    coef, *_ = np.linalg.lstsq(A, v, rcond=None)
    res = np.abs(v - A @ coef).mean(1)
    buenos = res < max(6.0, 3 * np.median(res))           # 2ª pasada sin valores raros
    if buenos.sum() > 20:
        coef, *_ = np.linalg.lstsq(A[buenos], v[buenos], rcond=None)
        rms = float(np.sqrt(((v[buenos] - A[buenos] @ coef) ** 2).mean()))
    else:
        rms = float(np.sqrt(((v - A @ coef) ** 2).mean()))
    return coef, rms


def _evaluar_plano(coef, h, w):
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    return coef[0] + xx[..., None] * coef[1] + yy[..., None] * coef[2]


def _bandas(perfil, hueco_max=2):
    """Filas de texto a partir del perfil horizontal (une huecos pequeños: tildes, puntos)."""
    filas, ini, vacio = [], None, 0
    for y, v in enumerate(perfil):
        if v:
            if ini is None:
                ini = y
            vacio = 0
            fin = y
        elif ini is not None:
            vacio += 1
            if vacio > hueco_max:
                filas.append((ini, fin + 1))
                ini = None
    if ini is not None:
        filas.append((ini, fin + 1))
    if not filas:
        return []
    alto = max(b - a for a, b in filas)
    return [(a, b) for a, b in filas if b - a >= 0.3 * alto]


# ============================================================================
# 1) Análisis del texto original
# ============================================================================
def _muestra(x, n, semilla=2):
    if len(x) <= n:
        return x
    return x[np.random.default_rng(semilla).choice(len(x), n, replace=False)]


def analizar(arr, caja, otras=(), filas_ocr=None):
    """arr: captura (H x W x 3, uint8). caja: (x0,y0,x1,y1) del párrafo. otras: cajas de los
    demás textos (no se usan como fondo). Devuelve un dict con el estilo, o None."""
    H, W = arr.shape[:2]
    x0, y0, x1, y1 = [float(v) for v in caja]
    alto_fila = (min(b[3] - b[1] for _, b in filas_ocr) if filas_ocr else (y1 - y0))
    m = int(max(5, min(0.6 * alto_fila, 26)))
    mv = int(max(m, min(1.5 * alto_fila + 6, 70)))     # vertical: sitio para copiar textura
    cx0, cy0 = max(0, int(x0) - m), max(0, int(y0) - mv)
    cx1, cy1 = min(W, int(math.ceil(x1)) + m), min(H, int(math.ceil(y1)) + mv)
    if cx1 - cx0 < 8 or cy1 - cy0 < 8:
        return None
    crop = arr[cy0:cy1, cx0:cx1].astype(np.float32)
    hh, ww = crop.shape[:2]
    en_caja = np.zeros((hh, ww), bool)
    en_caja[max(0, int(y0) - cy0 - 1):min(hh, int(math.ceil(y1)) - cy0 + 2),
            max(0, int(x0) - cx0 - 1):min(ww, int(math.ceil(x1)) - cx0 + 2)] = True
    libre = np.ones((hh, ww), bool)
    for o in otras:                                     # otros textos no cuentan como fondo
        ox0, oy0, ox1, oy1 = [int(v) for v in o]
        libre[max(0, oy0 - cy0 - 3):max(0, oy1 - cy0 + 3), max(0, ox0 - cx0 - 3):max(0, ox1 - cx0 + 3)] = False
    # anillo interior (pegado al texto: mismo cuadro/botón) y exterior (respaldo)
    k_in = int(np.clip(round(0.3 * alto_fila), 3, 10))
    anillo = _dilatar(en_caja, k_in) & ~_dilatar(en_caja, 1) & libre
    if anillo.sum() < 40:
        anillo = ~en_caja & libre
        if anillo.sum() < 40:
            return None
    anillo_ext = ~_dilatar(en_caja, 1) & libre

    # --- modelo del fondo: degradado + paleta de colores del borde ---
    coef, rms = _plano(crop, anillo)
    fondo_plano = _evaluar_plano(coef, hh, ww) if coef is not None else np.broadcast_to(
        np.median(crop[anillo], 0), crop.shape).astype(np.float32)
    muestra_anillo = _muestra(crop[anillo], 1500)
    paleta, et_p = _kmeans(muestra_anillo, 3, iters=6)
    # un color que ocupa poco del borde (un marco, una línea) no es "el fondo"
    paleta = np.array([c for j, c in enumerate(paleta) if (et_p == j).mean() >= 0.2] or [np.median(muestra_anillo, 0)])
    d_plano = np.sqrt(((crop - fondo_plano) ** 2).sum(2))
    d_pal = np.min(np.sqrt(((crop[..., None, :] - paleta[None, None]) ** 2).sum(3)), 2)
    d = np.minimum(d_plano, d_pal)
    ruido = float(np.percentile(d[anillo], 92))
    if rms < 9:
        # fondo liso o degradado: todo lo que se aparte claramente del fondo es letra
        # (incluye contorno y bordes suavizados; luego se separan por color)
        umbral = max(ruido * 1.6, 30.0)
    else:
        umbral = max(_otsu(d[en_caja]), ruido * 1.25, 24.0)
    texto = (d > umbral) & en_caja
    if rms >= 9:
        # Fondo recargado: la letra son los colores que se concentran DENTRO de la frase y casi
        # no aparecen en el borde (el fondo, en cambio, está en ambos sitios).
        dentro = _muestra(crop[en_caja], 3000)
        cent, _ = _kmeans(dentro, 5, iters=8)
        asig = lambda px: ((px[:, None, :] - cent[None]) ** 2).sum(2).argmin(1)
        e_in, e_out = asig(crop[en_caja]), asig(crop[anillo_ext])
        texto_cl = []
        for j in range(len(cent)):
            f_in, f_out = (e_in == j).mean(), (e_out == j).mean()
            if f_in > 0.03 and f_in > 3.0 * max(f_out, 0.004):
                texto_cl.append(j)
        if texto_cl:
            m2 = np.zeros_like(texto)
            m2[en_caja] = np.isin(e_in, texto_cl)
            texto = m2
    if texto.sum() < 10:
        return None

    # --- color de la letra: el centro del trazo (lo más alejado del fondo) ---
    dm = d[texto]
    nucleo_m = texto & (d >= np.percentile(dm, 55))
    cols = crop[nucleo_m]
    relleno = np.median(cols, 0)
    contorno, grosor, sombra = None, 0, None
    m_rell = texto.copy()
    c2, et = _kmeans(_muestra(crop[texto], 3000), 2, iters=8)
    if len(c2) == 2 and np.linalg.norm(c2[0] - c2[1]) > 70:
        et_all = ((crop[texto][:, None, :] - c2[None]) ** 2).sum(2).argmin(1)
        tam = [(et_all == j).mean() for j in range(2)]
        borde = texto & ~_erosion(texto, 1)
        en_borde = borde[texto]
        fb = [en_borde[et_all == j].mean() if (et_all == j).any() else 0 for j in range(2)]
        # dos tonos de verdad (no solo el borde suavizado entre letra y fondo)
        lejos = [np.median(d[texto][et_all == j]) if (et_all == j).any() else 0 for j in range(2)]
        jc_ = int(np.argmax(fb))
        f_ = c2[1 - jc_]
        b_ = np.asarray(est_fondo := np.median(crop[anillo], 0), np.float32)
        v_ = f_ - b_
        s_ = float(np.dot(c2[jc_] - b_, v_) / (np.dot(v_, v_) + 1e-6))
        dist_linea = float(np.linalg.norm((c2[jc_] - b_) - s_ * v_))
        es_suavizado = 0.08 < s_ < 0.92 and dist_linea < 35
        if min(tam) > 0.12 and abs(fb[0] - fb[1]) > 0.10 and not es_suavizado:
            jc = int(np.argmax(fb))
            jr = 1 - jc
            m_rell = np.zeros_like(texto)
            m_rell[texto] = et_all == jr
            m_seg = np.zeros_like(texto)
            m_seg[texto] = et_all == jc
            relleno = np.median(crop[m_rell & _erosion(m_rell, 1)] if (m_rell & _erosion(m_rell, 1)).sum() > 10
                                else crop[m_rell], 0)
            segundo = np.median(crop[m_seg], 0)
            # ¿sombra (desplazada hacia un lado) o contorno (alrededor)?
            mejor, solape_mejor = (0, 0), 0.0
            for dx in range(-4, 5):
                for dy in range(-4, 5):
                    if dx == dy == 0:
                        continue
                    sol = (_desplazar(m_rell, dx, dy) & m_seg).sum() / max(1, m_seg.sum())
                    if sol > solape_mejor:
                        mejor, solape_mejor = (dx, dy), sol
            opuesto = (_desplazar(m_rell, -mejor[0], -mejor[1]) & m_seg).sum() / max(1, m_seg.sum())
            if solape_mejor > 0.45 and opuesto < 0.5 * solape_mejor:
                sombra = (mejor, tuple(int(v) for v in segundo))
            else:
                contorno = tuple(int(v) for v in segundo)
                grosor = _grosor_contorno(m_rell, m_seg)

    # --- letra con contorno sobre fondo recargado: el relleno queda ENCERRADO por el contorno ---
    if contorno is None and sombra is None:
        huecos = _huecos(texto) & en_caja
        if huecos.sum() > 0.2 * texto.sum():
            cols_h = crop[huecos]
            ch, eh = _kmeans(_muestra(cols_h, 3000), 2, iters=8)
            eh_all = ((cols_h[:, None, :] - ch[None]) ** 2).sum(2).argmin(1)
            dispersion = [cols_h[eh_all == j].std(0).mean() if (eh_all == j).sum() > 5 else 999
                          for j in range(len(ch))]
            j = int(np.argmin(dispersion))
            color_h = ch[j]
            m_rell_h = np.zeros_like(texto)
            m_rell_h[huecos] = np.sqrt(((cols_h - color_h) ** 2).sum(1)) < 60
            if (m_rell_h.sum() > max(50, 0.08 * texto.sum()) and dispersion[j] < 32
                    and np.linalg.norm(color_h - np.median(crop[texto], 0)) > 70):
                m_seg = texto.copy()
                contorno = tuple(int(v) for v in np.median(crop[texto], 0))
                m_rell = m_rell_h
                relleno = np.median(crop[m_rell], 0)
                texto = texto | m_rell
                grosor = _grosor_contorno(m_rell, m_seg)

    # --- intensidad de tinta 0..1 (para comparar fuentes en gris, no en blanco y negro) ---
    vec = np.asarray(relleno, np.float32) - fondo_plano
    norma = (vec ** 2).sum(2) + 1e-6
    tinta_suave = np.clip(((crop - fondo_plano) * vec).sum(2) / norma, 0, 1)
    if contorno is not None:
        tinta_suave = np.where(m_seg, 0, tinta_suave)
    tinta_suave = np.where(en_caja, tinta_suave, 0)

    # --- ¿letra de píxeles? (bordes nítidos, sin tonos intermedios) ---
    banda = _dilatar(m_rell, 1) & ~_erosion(m_rell, 1) & en_caja
    if contorno is None and sombra is None and banda.any():
        inter = ((tinta_suave > 0.2) & (tinta_suave < 0.8))[banda].mean()
    else:
        inter = 1.0
    pixel = bool(inter < 0.045 and rms < 9 and np.sqrt(norma[banda]).mean() > 60)

    # --- geometría: tinta, filas y alineación ---
    ys, xs = np.nonzero(texto)
    tinta = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
    filas = _bandas(m_rell.any(1)) or [(tinta[1], tinta[3])]
    alineacion = "izq"
    if len(filas) > 1:
        izq, cen = [], []
        for a, b in filas:
            xx = np.nonzero(m_rell[a:b].any(0))[0]
            if len(xx):
                izq.append(xx.min())
                cen.append((xx.min() + xx.max()) / 2)
        if len(izq) > 1 and np.std(cen) < 0.5 * np.std(izq):
            alineacion = "centro"
    elif abs((x0 + x1) / 2 - W / 2) < 0.04 * W and (x1 - x0) < 0.8 * W:
        alineacion = "centro"          # una sola línea centrada (títulos, botones, subtítulos)

    fondo = "plano" if rms < 9 else "textura"
    return {
        "coef": coef, "origen": (cx0, cy0),
        "recorte": (cx0, cy0, cx1, cy1), "crop": crop, "libre": libre, "texto": texto, "relleno_m": m_rell,
        "tinta_suave": tinta_suave,
        "relleno": tuple(int(v) for v in relleno), "contorno": contorno, "grosor": grosor,
        "sombra": sombra, "pixel": pixel, "tinta": tinta, "filas": filas, "alineacion": alineacion,
        "fondo": fondo, "rms": rms, "fondo_plano": fondo_plano,
        "color_fondo": tuple(int(v) for v in np.median(crop[anillo], 0)),
        "ruido_std": crop[anillo_ext].std(0) if fondo == "textura" else np.zeros(3),
    }


# ============================================================================
# 2) Fuentes: catálogo y búsqueda de la más parecida
# ============================================================================
def _base():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    aqui = os.path.dirname(os.path.abspath(__file__))
    if os.path.basename(aqui) == "app" and os.path.isdir(os.path.join(os.path.dirname(aqui), "fuentes")):
        return os.path.dirname(aqui)
    return aqui


CARPETA_FUENTES = os.path.join(_base(), "fuentes")

# Fuentes de Windows que se prueban (archivo, instancia) — las que existan
_WINDOWS = ["arial.ttf", "arialbd.ttf", "ariali.ttf", "segoeui.ttf", "segoeuib.ttf", "segoeuisl.ttf",
            "verdana.ttf", "verdanab.ttf", "tahoma.ttf", "tahomabd.ttf", "trebuc.ttf", "trebucbd.ttf",
            "calibri.ttf", "calibrib.ttf", "candara.ttf", "corbel.ttf", "georgia.ttf", "georgiab.ttf",
            "times.ttf", "timesbd.ttf", "pala.ttf", "palab.ttf", "BKANT.TTF", "constan.ttf",
            "cambria.ttc", "GARA.TTF", "cour.ttf", "courbd.ttf", "consola.ttf", "comic.ttf",
            "comicbd.ttf", "impact.ttf", "framd.ttf", "LeelawUI.ttf", "bahnschrift.ttf",
            "gadugi.ttf", "ebrima.ttf", "malgun.ttf", "Candarab.ttf", "segoepr.ttf", "segoesc.ttf"]
_WINDOWS_JA = {"YuGothR.ttc": "sans", "YuGothB.ttc": "sans_b", "meiryo.ttc": "sans", "meiryob.ttc": "sans_b",
               "msgothic.ttc": "sans", "BIZ-UDGothicR.ttc": "sans", "BIZ-UDGothicB.ttc": "sans_b",
               "yumin.ttf": "serif", "yuminl.ttf": "serif", "msmincho.ttc": "serif", "BIZ-UDMinchoM.ttc": "serif"}
# fuentes japonesas incluidas → categoría
_INCLUIDAS_JA = {"NotoSansJP.ttf": "sans", "NotoSerifJP.ttf": "serif", "DotGothic16-Regular.ttf": "pixel",
                 "ZenMaruGothic-Medium.ttf": "redonda"}
# categoría japonesa → fuente latina equivalente (archivo incluido o de Windows)
_EQUIVALENTE = {"sans": ["segoeui.ttf", "NotoSans.ttf"], "sans_b": ["segoeuib.ttf", "NotoSans.ttf@Bold"],
                "serif": ["georgia.ttf", "NotoSerif.ttf"], "pixel": ["DotGothic16-Regular.ttf"],
                "redonda": ["Nunito.ttf@Bold", "Nunito.ttf"]}


def _carpetas_sistema():
    if ES_WINDOWS:
        win = os.environ.get("WINDIR", r"C:\Windows")
        return [os.path.join(win, "Fonts"),
                os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Windows", "Fonts")]
    return ["/usr/share/fonts", os.path.expanduser("~/.fonts")]


def _buscar_archivo(nombre):
    if os.path.isabs(nombre) and os.path.isfile(nombre):
        return nombre
    for c in [CARPETA_FUENTES] + _carpetas_sistema():
        r = os.path.join(c, nombre)
        if os.path.isfile(r):
            return r
    return None


@functools.lru_cache(maxsize=512)
def cargar(ruta, tam, instancia=None):
    f = ImageFont.truetype(ruta, max(4, int(round(tam))))
    if instancia:
        try:
            f.set_variation_by_name(instancia)
        except Exception:
            pass
    return f


@functools.lru_cache(maxsize=256)
def cobertura(ruta):
    """Caracteres que la fuente sabe dibujar (para no mostrar cuadritos)."""
    try:
        from fontTools.ttLib import TTFont, TTCollection
        if ruta.lower().endswith(".ttc"):
            fuente = TTCollection(ruta).fonts[0]
        else:
            fuente = TTFont(ruta, lazy=True)
        return frozenset(fuente.getBestCmap() or {})
    except Exception:
        return None


_EXT_FUENTE = (".ttf", ".otf", ".ttc")
_EXT_WEB = (".woff", ".woff2")


def _carpeta_cache():
    d = os.path.join(tempfile.gettempdir(), "TraductorPantalla_fuentes")
    os.makedirs(d, exist_ok=True)
    return d


def _nombre_cache(origen, nombre):
    h = hashlib.md5(origen.encode("utf-8", "replace")).hexdigest()[:10]
    limpio = re.sub(r"[^A-Za-z0-9._-]", "_", os.path.basename(nombre))[:60]
    return os.path.join(_carpeta_cache(), f"{h}_{limpio}")


def _woff_a_ttf(ruta, datos=None):
    """RPG Maker MZ y los juegos web guardan la letra en .woff: se pasa a .ttf (una vez)."""
    destino = os.path.splitext(_nombre_cache(ruta, os.path.basename(ruta)))[0] + ".ttf"
    if os.path.isfile(destino):
        return destino
    from fontTools.ttLib import TTFont
    f = TTFont(io.BytesIO(datos) if datos is not None else ruta)
    f.flavor = None
    f.save(destino)
    return destino


_PERMITIDOS = {("_codecs", "encode"), ("builtins", "bytes"), ("builtins", "bytearray"), ("builtins", "str"),
               ("builtins", "int"), ("builtins", "set"), ("builtins", "frozenset"), ("__builtin__", "str"),
               ("__builtin__", "unicode"), ("__builtin__", "long"), ("__builtin__", "bytearray"),
               ("__builtin__", "set"), ("__builtin__", "frozenset"), ("__builtin__", "bytes"),
               ("__builtin__", "int")}


class _SoloDatos(pickle.Unpickler):
    """El índice de un .rpa es un pickle: se lee permitiendo SOLO datos simples (texto, números,
    bytes); cualquier otro objeto se rechaza, así un archivo manipulado no puede ejecutar nada."""

    def find_class(self, module, name):
        if (module, name) in _PERMITIDOS:
            if module == "_codecs":
                import _codecs
                return _codecs.encode
            import builtins
            return getattr(builtins, {"unicode": "str", "long": "int"}.get(name, name))
        raise pickle.UnpicklingError("contenido no permitido en el índice")


def fuentes_de_rpa(ruta, maximo=12, tam_max=40 << 20):
    """Saca SOLO las fuentes de un archivo de Ren'Py (archive.rpa) a la carpeta temporal."""
    salida = []
    with open(ruta, "rb") as f:
        cab = f.readline(300).split()
        if not cab or not cab[0].startswith(b"RPA-"):
            return []
        version = cab[0]
        try:
            offset = int(cab[1], 16)
            clave = 0
            if version == b"RPA-3.0":
                for k in cab[2:]:
                    clave ^= int(k, 16)
            elif version == b"RPA-3.2":
                for k in cab[3:]:
                    clave ^= int(k, 16)
            elif version != b"RPA-2.0":
                return []
        except (IndexError, ValueError):
            return []
        f.seek(offset)
        indice = _SoloDatos(io.BytesIO(zlib.decompress(f.read())), encoding="bytes").load()
        if not isinstance(indice, dict):
            return []
        for nombre, entradas in indice.items():
            if isinstance(nombre, bytes):
                nombre = nombre.decode("utf-8", "replace")
            if not isinstance(nombre, str) or not nombre.lower().endswith(_EXT_FUENTE + _EXT_WEB):
                continue
            try:
                e = entradas[0]
                off, largo = int(e[0]) ^ clave, int(e[1]) ^ clave
                prefijo = e[2] if len(e) > 2 else b""
                if isinstance(prefijo, str):
                    prefijo = prefijo.encode("latin-1")
                if not 0 < largo <= tam_max:
                    continue
                f.seek(off)
                datos = prefijo + f.read(largo - len(prefijo))
                if nombre.lower().endswith(_EXT_WEB):
                    salida.append(_woff_a_ttf(ruta + "/" + nombre, datos))
                else:
                    destino = _nombre_cache(ruta, nombre)
                    if not os.path.isfile(destino):
                        with open(destino, "wb") as g:
                            g.write(datos)
                    salida.append(destino)
            except Exception:
                continue
            if len(salida) >= maximo:
                break
    return salida


def _se_puede_usar(ruta):
    try:
        ImageFont.truetype(ruta, 20).getbbox("Ab")
        return True
    except Exception:
        return False


def buscar_fuentes_juego(ruta_exe, limite=6000):
    """Fuentes que trae el propio juego: sueltas en su carpeta (Ren'Py, RPG Maker MV/MZ,
    muchos indies), en .woff (RPG Maker MZ) o dentro de archive.rpa (Ren'Py)."""
    if not ruta_exe:
        return []
    raiz = os.path.dirname(ruta_exe)
    if not os.path.isdir(raiz) or os.path.normcase(raiz).startswith(
            os.path.normcase(os.environ.get("WINDIR", r"C:\Windows"))):
        return []
    encontradas, vistos, rpas = [], 0, []
    for base, dirs, archivos in os.walk(raiz):
        if base[len(raiz):].count(os.sep) > 4:
            dirs[:] = []
            continue
        for a in archivos:
            vistos += 1
            al = a.lower()
            r = os.path.join(base, a)
            try:
                if al.endswith(_EXT_FUENTE):
                    encontradas.append(r)
                elif al.endswith(_EXT_WEB):
                    encontradas.append(_woff_a_ttf(r))
                elif al.endswith(".rpa") and len(rpas) < 6:
                    rpas.append(r)
            except Exception:
                continue
        if vistos > limite or len(encontradas) > 40:
            break
    for r in rpas:
        try:
            encontradas.extend(fuentes_de_rpa(r))
        except Exception:
            continue
    unicas, firmas = [], set()
    for r in encontradas:
        try:
            firma = (os.path.basename(r).lower().split("_", 1)[-1], os.path.getsize(r))
        except OSError:
            continue
        if firma not in firmas and _se_puede_usar(r):
            firmas.add(firma)
            unicas.append(r)
    return unicas[:40]


class CatalogoFuentes:
    """Candidatas para comparar: (ruta, instancia, nombre visible, origen)."""

    def __init__(self, fuentes_juego=()):
        self.latinas, self.japonesas = [], []
        for r in fuentes_juego:
            self.latinas.append((r, None, os.path.splitext(os.path.basename(r))[0], "juego"))
        if os.path.isdir(CARPETA_FUENTES):
            for a in sorted(os.listdir(CARPETA_FUENTES)):
                r = os.path.join(CARPETA_FUENTES, a)
                if a in _INCLUIDAS_JA:
                    self.japonesas.append((r, None, a.split("-")[0].split(".")[0], _INCLUIDAS_JA[a]))
                    if _INCLUIDAS_JA[a] != "pixel":
                        continue
                if a.lower().endswith((".ttf", ".otf")):
                    inst = "Regular" if "Regular" in self._instancias(r) else None
                    self.latinas.append((r, inst, a.split(".")[0].split("-")[0], "incluida"))
        if ES_WINDOWS:
            for a in _WINDOWS:
                r = _buscar_archivo(a)
                if r:
                    self.latinas.append((r, None, os.path.splitext(a)[0], "windows"))
            for a, cat in _WINDOWS_JA.items():
                r = _buscar_archivo(a)
                if r:
                    self.japonesas.append((r, None, os.path.splitext(a)[0], cat))
        else:
            for a in ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf", "DejaVuSerif.ttf", "FreeSerif.ttf",
                      "FreeSans.ttf", "Carlito-Regular.ttf", "Caladea-Regular.ttf"):
                for c in _carpetas_sistema():
                    for base, _, archivos in os.walk(c):
                        if a in archivos:
                            self.latinas.append((os.path.join(base, a), None, a.split(".")[0], "sistema"))
                            break

    @staticmethod
    def _instancias(ruta):
        try:
            f = ImageFont.truetype(ruta, 20)
            nombres = [n.decode() if isinstance(n, bytes) else n for n in f.get_variation_names()]
        except Exception:
            return [None]
        quiero = [n for n in nombres if n in ("Regular", "Bold", "Medium")]
        return quiero or [None]


def _render_gris(ruta, inst, texto, tam):
    f = cargar(ruta, tam, inst)
    l, t, r, b = f.getbbox(texto)
    if r - l < 2 or b - t < 2:
        return None
    im = Image.new("L", (int(r - l) + 4, int(b - t) + 4), 0)
    ImageDraw.Draw(im).text((2 - l, 2 - t), texto, font=f, fill=255)
    a = np.asarray(im, np.float32) / 255
    ys, xs = np.nonzero(a > 0.12)
    if len(xs) == 0:
        return None
    return a[ys.min():ys.max() + 1, xs.min():xs.max() + 1]


def _correlacion(objetivo, cand):
    """Correlación (−1..1) entre la tinta real y la dibujada, al mismo tamaño y algo suavizadas."""
    h, w = objetivo.shape
    c = np.asarray(Image.fromarray((cand * 255).astype(np.uint8)).resize((w, h), Image.BILINEAR), np.float32) / 255
    a = np.asarray(Image.fromarray((objetivo * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(0.7)),
                   np.float32) / 255
    c = np.asarray(Image.fromarray((c * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(0.7)),
                   np.float32) / 255
    a, c = a - a.mean(), c - c.mean()
    return float((a * c).sum() / (np.sqrt((a * a).sum() * (c * c).sum()) + 1e-6))


def _variantes(texto):
    """El lector a veces devuelve TODO EN MAYÚSCULAS: probar también otras formas."""
    if texto.isupper() and len(texto) > 3:
        return [texto, texto.title(), texto.lower()]
    return [texto]


def _puntuar(objetivo, ruta, inst, texto):
    alto_t, ancho_t = objetivo.shape
    mejor = None
    for t in _variantes(texto):
        ref = cargar(ruta, 100, inst)
        l, tp, r, b = ref.getbbox(t)
        if b - tp < 2:
            continue
        tam = 100 * alto_t / (b - tp)
        if tam < 5 or tam > 400:
            continue
        m = _render_gris(ruta, inst, t, tam)
        if m is None:
            continue
        proporcion = m.shape[1] / max(1, ancho_t)
        p = _correlacion(objetivo, m) - 0.9 * abs(math.log(max(1e-3, proporcion)))
        if mejor is None or p > mejor[0]:
            mejor = (p, tam)
    return mejor


def elegir_fuente(est, texto_fila, catalogo, es_jp=False, limite=None):
    """Devuelve (ruta, instancia, nombre, tam_em, puntuación, origen/categoría).
    Fase 1: la familia más parecida. Fase 2: el grosor (instancias) dentro de las mejores."""
    a, b = est["filas"][0]
    fila = est["tinta_suave"][a:b]
    xs = np.nonzero((fila > 0.3).any(0))[0]
    if len(xs) < 3 or not texto_fila.strip():
        return None
    objetivo = fila[:, xs.min():xs.max() + 1]
    ys = np.nonzero((objetivo > 0.3).any(1))[0]
    objetivo = objetivo[ys.min():ys.max() + 1]
    candidatas = catalogo.japonesas if es_jp else catalogo.latinas
    # fase 1: una instancia por archivo
    vistos, fase1 = set(), []
    for c in candidatas[:limite]:
        if c[0] in vistos:
            continue
        vistos.add(c[0])
        try:
            r = _puntuar(objetivo, c[0], c[1], texto_fila)
        except Exception:
            r = None
        if r:
            p = r[0] + (0.04 if c[3] == "juego" else 0)
            fase1.append((p, r[1], c))
    if not fase1:
        return None
    fase1.sort(key=lambda x: -x[0])
    mejor = fase1[0]
    # fase 2: probar los grosores (instancias) de las 3 mejores familias
    for _, _, c in fase1[:3]:
        try:
            f = ImageFont.truetype(c[0], 20)
            nombres = [n.decode() if isinstance(n, bytes) else n for n in f.get_variation_names()]
        except Exception:
            nombres = []
        for inst in nombres:
            r = _puntuar(objetivo, c[0], inst, texto_fila)
            if r and r[0] > mejor[0]:
                mejor = (r[0], r[1], (c[0], inst, f"{c[2].split(' ')[0]} {inst}", c[3]))
    p, tam, c = mejor
    return (c[0], c[1], c[2], tam, p, c[3])


def _objetivo(est):
    """Tinta de la primera fila (0..1), recortada, para comparar con las fuentes."""
    if est.get("basico") or est.get("tinta_suave") is None:
        return None
    a, b = est["filas"][0]
    fila = est["tinta_suave"][a:b]
    xs = np.nonzero((fila > 0.3).any(0))[0]
    if len(xs) < 3:
        return None
    obj = fila[:, xs.min():xs.max() + 1]
    ys = np.nonzero((obj > 0.3).any(1))[0]
    return obj[ys.min():ys.max() + 1] if len(ys) else None


def _alto_fila(est):
    return float(np.median([b - a for a, b in est["filas"]])) if est["filas"] else 20.0


def _es_negrita(inst):
    return bool(inst) and any(k in inst for k in ("Bold", "Black", "Heavy"))


def decidir_fuente(est, texto_fila, catalogo, es_jp=False, preferida=None, lista_corta=()):
    """La letra con la que se escribe la traducción → (ruta, instancia, tamaño, nombre, parecido).
    preferida: archivo elegido por el usuario para este juego. lista_corta: fuentes que ya
    acertaron en esta sesión (se prueban primero: mucho más rápido)."""
    alto = _alto_fila(est)
    obj = _objetivo(est)
    texto_fila = (texto_fila or "").strip()

    def tam_para(ruta, inst):
        r = _puntuar(obj, ruta, inst, texto_fila) if (obj is not None and texto_fila) else None
        return (r[1], r[0]) if r else (alto * 0.85, 0.0)

    if preferida and not es_jp:
        tam, p = tam_para(preferida, None)
        return preferida, None, tam, os.path.splitext(os.path.basename(preferida))[0], p
    if est.get("basico") or obj is None or not texto_fila:
        r, inst = equivalente_latina("sans_b")
        return r, inst, alto * 0.85, "sans", 0.0
    if es_jp:
        m = elegir_fuente(est, texto_fila, catalogo, es_jp=True)
        if m:
            cat = m[5]
            if cat == "sans" and _es_negrita(m[1]):
                cat = "sans_b"
            tam = m[3] * 0.95
        else:
            cat, tam = ("sans_b" if est["contorno"] else "sans"), alto * 0.85
        r, inst = equivalente_latina(cat)
        return r, inst, tam, cat, (m[4] if m else 0.0)
    mejor = None
    for ruta, inst in lista_corta:
        r = _puntuar(obj, ruta, inst, texto_fila)
        if r and (mejor is None or r[0] > mejor[0]):
            mejor = (r[0], r[1], ruta, inst)
    if mejor and mejor[0] >= 0.62:
        return mejor[2], mejor[3], mejor[1], os.path.basename(mejor[2]), mejor[0]
    m = elegir_fuente(est, texto_fila, catalogo)
    if m is None or m[4] < 0.45:            # nada se parece: una sans limpia
        r, inst = equivalente_latina("sans_b" if est["contorno"] else "sans")
        tam, _ = tam_para(r, inst)
        return r, inst, tam, "sans", (m[4] if m else 0.0)
    return m[0], m[1], m[3], m[2], m[4]


def equivalente_latina(categoria):
    for nombre in _EQUIVALENTE.get(categoria, ["segoeui.ttf", "NotoSans.ttf"]):
        archivo, _, inst = nombre.partition("@")
        r = _buscar_archivo(archivo)
        if r:
            return r, (inst or None)
    return None, None


# ============================================================================
# 3) Borrar el texto original reconstruyendo el fondo
# ============================================================================
def _difusion(img, quitar, pasos=10):
    """Rellena 'quitar' difundiendo los colores de alrededor (pirámide grueso→fino)."""
    piramide = [(img, quitar)]
    while min(piramide[-1][0].shape[:2]) > 6 and piramide[-1][1].any() and len(piramide) < 7:
        a, q = piramide[-1]
        h2, w2 = a.shape[0] // 2, a.shape[1] // 2
        a4 = a[:h2 * 2, :w2 * 2].reshape(h2, 2, w2, 2, 3)
        conocido = ~q[:h2 * 2, :w2 * 2].reshape(h2, 2, w2, 2)
        n = conocido.sum((1, 3))
        s = (a4 * conocido[..., None]).sum((1, 3))
        piramide.append((np.where(n[..., None] > 0, s / np.maximum(n, 1)[..., None], 0), n == 0))
    a, q = piramide[-1]
    a = a.copy()
    if q.any():
        a[q] = a[~q].mean(0) if (~q).any() else 128
    for nivel in range(len(piramide) - 2, -1, -1):
        alto, qa = piramide[nivel]
        sub = np.repeat(np.repeat(a, 2, 0), 2, 1)
        sub = np.pad(sub, ((0, max(0, alto.shape[0] - sub.shape[0])), (0, max(0, alto.shape[1] - sub.shape[1])), (0, 0)),
                     mode="edge")[:alto.shape[0], :alto.shape[1]]
        r = alto.copy()
        r[qa] = sub[qa]
        for _ in range(pasos):
            p = np.pad(r, ((1, 1), (1, 1), (0, 0)), mode="edge")
            prom = (p[:-2, 1:-1] + p[2:, 1:-1] + p[1:-1, :-2] + p[1:-1, 2:]) / 4
            r[qa] = prom[qa]
        a = r
    return a


def _copiar_textura(crop, quitar, est):
    """Para fondos con textura: copia el trozo de fondo de justo encima (o debajo) de cada fila,
    corrigiendo el degradado, para conservar el grano/dibujo del fondo."""
    out = crop.copy()
    pendiente = quitar.copy()
    plano = est["fondo_plano"]
    h = crop.shape[0]
    # solo se copia lo que parece fondo: ni otros textos, ni bordes de cuadros, ni etiquetas
    parecido = np.sqrt(((crop - plano) ** 2).sum(2)) < max(30.0, 3 * est["rms"])
    fondo_ok = est.get("libre", np.ones(quitar.shape, bool)) & parecido
    for a, b in est["filas"]:
        alto = b - a + 4
        filas = np.arange(max(0, a - 3), min(h, b + 3))
        for desp in (-alto, alto, -2 * alto, 2 * alto):
            origen = filas + desp
            ok = (origen >= 0) & (origen < h)
            if not ok.any():
                continue
            fo, fd = origen[ok], filas[ok]
            q = pendiente[fd]
            fuente_libre = ~quitar[fo] & fondo_ok[fo]
            usar = q & fuente_libre
            if usar.any():
                bloque = out[fd]
                correccion = plano[fd] - plano[fo]
                nuevo = crop[fo] + correccion
                bloque[usar] = nuevo[usar]
                out[fd] = bloque
                q[usar] = False
                pendiente[fd] = q
            if not pendiente[filas].any():
                break
    if pendiente.any():
        out = np.where(pendiente[..., None], _difusion(out, pendiente), out)
    return out


def reconstruir(est):
    """Devuelve (rgba uint8 del tamaño del recorte) que tapa SOLO las letras originales."""
    crop, texto = est["crop"], est["texto"]
    extra = 1 + (0 if est["pixel"] else 1) + (est["grosor"] // 2)
    if est["sombra"]:
        extra += 1
    if est["contorno"] and est["fondo"] == "textura":
        extra += 2          # el borde suavizado del contorno se mezcla con el fondo
    quitar = _dilatar(texto, extra)
    if est["fondo"] == "plano" and est["rms"] < 3.5:     # liso de verdad: degradado + grano
        relleno = est["fondo_plano"] + np.random.default_rng(5).normal(0, est["rms"] * 0.5, crop.shape)
    elif est["fondo"] == "plano":                         # cuadro semitransparente sobre dibujo:
        relleno = _difusion(crop, quitar)                 # seguir las manchas del fondo
    else:
        relleno = _copiar_textura(crop, quitar, est)
    rgb = np.where(quitar[..., None], relleno, crop).clip(0, 255).astype(np.uint8)
    alfa = Image.fromarray((_dilatar(quitar, 1) * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(0.8))
    alfa = np.maximum(np.asarray(alfa), quitar * 255).astype(np.uint8)
    return np.dstack([rgb, alfa])


def parche_suave(est, pad=None):
    """"Vidrio esmerilado": la misma zona muy desenfocada, con bordes suaves y algo más oscura
    (o clara) para que se lea. Oculta el original sin parecer una caja; sirve sobre fondos
    muy recargados o en movimiento (se puede refrescar en cada lectura)."""
    crop = est["crop"]
    hh, ww = est["texto"].shape
    x0, y0, x1, y1 = est["tinta"]
    alto = (y1 - y0) / max(1, len(est["filas"]))
    pad = pad or max(6, int(alto * 0.5))
    radio = max(4, alto * 0.7)
    borroso = np.asarray(Image.fromarray(crop.clip(0, 255).astype(np.uint8)).filter(
        ImageFilter.GaussianBlur(radio)), np.float32)
    # oscurecer (texto claro) o aclarar (texto oscuro) un poco para dar contraste
    claro = np.mean(est["relleno"]) > 128
    borroso = borroso * 0.62 if claro else borroso * 0.7 + 255 * 0.3
    a = Image.new("L", (ww, hh), 0)
    ImageDraw.Draw(a).rounded_rectangle((x0 - pad, y0 - pad * 0.7, x1 + pad, y1 + pad * 0.7),
                                        radius=pad * 1.2, fill=255)
    a = a.filter(ImageFilter.GaussianBlur(pad * 0.5))
    return np.dstack([borroso.clip(0, 255).astype(np.uint8), np.asarray(a)])


def modo_automatico(est, animado=False):
    """natural (reconstruir el fondo) o suave (vidrio esmerilado) según el fondo."""
    if animado or est["rms"] >= 28:
        return "suave"
    return "natural"


# ============================================================================
# 4) Espacio disponible y agrupación de líneas
# ============================================================================
def medir_espacio(arr, est):
    """Fondo libre a la izquierda, derecha y abajo del texto (cuadros y botones lisos)."""
    if est["fondo"] != "plano" or est["coef"] is None:
        return (0, 0, 0)
    H, W = arr.shape[:2]
    cx0, cy0 = est["origen"]
    x0, y0, x1, y1 = [v + o for v, o in zip(est["tinta"], (cx0, cy0, cx0, cy0))]
    coef = est["coef"]
    tol = max(20.0, 3 * est["rms"] + 12)
    alto = max(4, y1 - y0)
    filas = np.linspace(y0 + alto * 0.2, y1 - alto * 0.2, 5).astype(int).clip(0, H - 1)

    def libre_horizontal(desde, hasta):
        if hasta <= desde:
            return 0
        xs = np.arange(desde, hasta)
        tramo = arr[filas][:, xs].astype(np.float32)
        pred = coef[0] + (xs[None, :, None] - cx0) * coef[1] + (filas[:, None, None] - cy0) * coef[2]
        malo = np.sqrt(((tramo - pred) ** 2).sum(2)) > tol
        primero = np.where(malo.any(1), malo.argmax(1), len(xs))
        return int(np.min(primero))

    largo = int(1.5 * (x1 - x0)) + 40
    der = libre_horizontal(x1 + 1, min(W, x1 + 1 + largo))
    # para la izquierda se mide al revés (desde el texto hacia fuera)
    xs = np.arange(max(0, x0 - largo), x0)[::-1]
    if len(xs):
        tramo = arr[filas][:, xs].astype(np.float32)
        pred = coef[0] + (xs[None, :, None] - cx0) * coef[1] + (filas[:, None, None] - cy0) * coef[2]
        malo = np.sqrt(((tramo - pred) ** 2).sum(2)) > tol
        izq = int(np.min(np.where(malo.any(1), malo.argmax(1), len(xs))))
    else:
        izq = 0
    # abajo: hasta dónde sigue el fondo liso bajo el texto
    cols = np.linspace(x0, x1 - 1, 9).astype(int).clip(0, W - 1)
    ys = np.arange(y1 + 1, min(H, y1 + 1 + 3 * alto))
    if len(ys):
        tramo = arr[ys][:, cols].astype(np.float32)
        pred = coef[0] + (cols[None, :, None] - cx0) * coef[1] + (ys[:, None, None] - cy0) * coef[2]
        malo = np.sqrt(((tramo - pred) ** 2).sum(2)) > tol
        abajo = int(np.where(malo.any(0), malo.argmax(0), len(ys)).min())
    else:
        abajo = 0
    margen = max(4, int(alto * 0.4))
    return (max(0, izq - margen), max(0, der - margen), max(0, abajo - 2))


def geometria(est):
    """Caja de tinta y filas en coordenadas absolutas (de la captura)."""
    cx0, cy0 = est["origen"]
    x0, y0, x1, y1 = est["tinta"]
    return ((cx0 + x0, cy0 + y0, cx0 + x1, cy0 + y1), [(cy0 + a, cy0 + b) for a, b in est["filas"]])


def agrupar(piezas):
    """Junta líneas del mismo cuadro de diálogo para repartir la traducción entre ellas.
    piezas: [{'est', 'trad', ...}] → lista de grupos (listas de piezas). No junta menús."""
    orden = sorted(piezas, key=lambda p: geometria(p["est"])[0][1])
    grupos = []
    for p in orden:
        (x0, y0, x1, y1), filas = geometria(p["est"])
        alto = (y1 - y0) / max(1, len(filas))
        ok = False
        if grupos:
            g = grupos[-1]
            (gx0, gy0, gx1, gy1), gfilas = geometria(g[-1]["est"])
            galto = (gy1 - gy0) / max(1, len(gfilas))
            ancho_max = max(geometria(q["est"])[0][2] - geometria(q["est"])[0][0] for q in g)
            ok = (abs(x0 - gx0) < max(alto, galto) and 0 <= y0 - gy1 < 1.1 * max(alto, galto)
                  and abs(alto - galto) < 0.3 * max(alto, galto)
                  and np.linalg.norm(np.subtract(p["est"]["relleno"], g[-1]["est"]["relleno"])) < 45
                  and p["est"]["pixel"] == g[-1]["est"]["pixel"]
                  and (gx1 - gx0) >= 0.55 * ancho_max            # la de arriba es larga (no es un menú)
                  and (gx1 - gx0) >= 6 * galto)
        if ok:
            grupos[-1].append(p)
        else:
            grupos.append([p])
    return grupos


# ============================================================================
# 5) Escribir la traducción en el hueco
# ============================================================================
def _envolver(palabras, f, ancho):
    lineas, actual = [], ""
    for p in palabras:
        prueba = (actual + " " + p).strip()
        if not actual or f.getlength(prueba) <= ancho:
            actual = prueba
        else:
            lineas.append(actual)
            actual = p
    if actual:
        lineas.append(actual)
    return lineas


def _equilibrar(texto, f, filas_max, ancho_max):
    """Reparte el texto en ≤ filas_max líneas lo más parejas posible. None si no cabe."""
    palabras = texto.split()
    if not palabras:
        return [""]
    total = f.getlength(texto)
    if total <= ancho_max and filas_max >= 1:
        if filas_max == 1 or total <= ancho_max * 0.65:
            return [texto]
    lo, hi = max(f.getlength(p) for p in palabras), max(total, 1)
    if lo > ancho_max:
        return None
    mejor = None
    for _ in range(14):                          # búsqueda binaria del ancho mínimo
        mid = (lo + hi) / 2
        ls = _envolver(palabras, f, mid)
        if len(ls) <= filas_max:
            mejor, hi = ls, mid
        else:
            lo = mid
    if mejor is None:
        ls = _envolver(palabras, f, ancho_max)
        return ls if len(ls) <= filas_max else None
    return mejor if max(f.getlength(l) for l in mejor) <= ancho_max + 0.5 else None


def maquetar(trad, ruta, inst, tam, ancho, filas, espacio, centrado, alto_fila):
    """Elige tamaño, líneas y ancho para que la traducción quepa en el hueco del original.
    Prioridad: mismo tamaño → usar el ancho libre del cuadro → achicar → una fila más (si hay
    sitio abajo) → estrechar un poco las letras. Devuelve (fuente, lineas, compresion, ancho_usado)."""
    izq, der, abajo = espacio
    libre = (2 * min(izq, der)) if centrado else der
    anchos = [ancho * 1.04, max(ancho * 1.04, ancho + libre)]
    frases = [x for x in re.split(r"(?<=[.!?…。！？])\s+", trad.strip()) if x]
    if 1 < len(frases) <= filas:                 # una frase por línea, como el original
        for escala in (1.0, 0.95, 0.9):
            f = cargar(ruta, tam * escala, inst)
            if all(f.getlength(x) <= anchos[-1] for x in frases):
                return f, frases, 1.0, anchos[-1]
    filas_extra = [filas] + ([filas + 1] if abajo >= alto_fila * 0.9 else [])
    for escala in (1.0, 0.95, 0.9, 0.85, 0.8):
        f = cargar(ruta, tam * escala, inst)
        for a in anchos:
            ls = _equilibrar(trad, f, filas, a)
            if ls:
                return f, ls, 1.0, a
    for escala in (0.95, 0.88, 0.8):
        f = cargar(ruta, tam * escala, inst)
        for n in filas_extra[1:]:
            ls = _equilibrar(trad, f, n, anchos[-1])
            if ls:
                return f, ls, 1.0, anchos[-1]
    for escala in (0.8, 0.72, 0.65):
        f = cargar(ruta, tam * escala, inst)
        ls = _envolver(trad.split(), f, anchos[-1] * 1.18) or [trad]
        if len(ls) <= filas_extra[-1]:
            largo = max(f.getlength(l) for l in ls)
            return f, ls, max(0.82, min(1.0, anchos[-1] / max(1, largo))), anchos[-1]
    f = cargar(ruta, tam * 0.65, inst)
    ls = _envolver(trad.split(), f, anchos[-1]) or [trad]
    return f, ls, 1.0, anchos[-1]


def est_basico(caja, filas_ocr=None):
    """Estilo genérico (letra clara con borde sobre vidrio) para cuando no hay análisis."""
    x0, y0, x1, y1 = [int(round(v)) for v in caja]
    filas = []
    for _, c in (filas_ocr or []):
        a, b = int(round(c[1])) - y0, int(round(c[3])) - y0
        if b > a:
            filas.append((a, b))
    return {"origen": (x0, y0), "tinta": (0, 0, max(1, x1 - x0), max(1, y1 - y0)),
            "filas": filas or [(0, max(1, y1 - y0))], "relleno": (246, 242, 234), "contorno": None,
            "grosor": 0, "sombra": None, "pixel": False, "alineacion": "izq", "fondo": "textura",
            "rms": 99.0, "coef": None, "color_fondo": (0, 0, 0), "basico": True}


def borrado(est, modo="natural"):
    """Capa que borra el texto original: (RGBA, (x, y) absolutos)."""
    base = reconstruir(est) if modo == "natural" else parche_suave(est)
    return Image.fromarray(base, "RGBA"), est["origen"]


def escribir(grupo, trad, fuente, arr, modo="natural", respaldo=None):
    """Capa con la traducción de un grupo de líneas: (RGBA, (x, y) absolutos)."""
    ruta, inst, tam = fuente
    est = grupo[0]["est"]
    cajas = [geometria(p["est"]) for p in grupo]
    x0 = min(c[0][0] for c in cajas)
    y0 = min(c[0][1] for c in cajas)
    x1 = max(c[0][2] for c in cajas)
    filas = [f for c in cajas for f in c[1]]
    alto_fila = float(np.median([b - a for a, b in filas]))
    paso = ((filas[-1][0] - filas[0][0]) / (len(filas) - 1)) if len(filas) > 1 else None
    centrado = est["alineacion"] == "centro"
    espacio = medir_espacio(arr, grupo[-1]["est"])
    if est["fondo"] == "textura" and centrado:
        # subtítulos o títulos sobre imagen: pueden crecer un poco y bajar a una 2ª línea
        espacio = (int((x1 - x0) * 0.15), int((x1 - x0) * 0.15), int(alto_fila * 1.3))
    f, lineas, compresion, ancho_usado = maquetar(trad, ruta, inst, tam, x1 - x0, len(filas), espacio,
                                                  centrado, alto_fila)
    if est["pixel"]:
        f = cargar(ruta, max(6, round(f.size)), inst)
    asc, desc = f.getmetrics()
    alto_linea = paso or (asc + desc) * 1.05
    ancho_txt = max(f.getlength(l) for l in lineas) * compresion
    margen = 10 + est["grosor"] * 2 + (max(abs(v) for v in est["sombra"][0]) if est["sombra"] else 0)
    cx = (x0 + x1) / 2
    izq_abs = (cx - ancho_txt / 2) if centrado else x0
    _, top_ref, _, bot_ref = f.getbbox("H")
    n_o, n_n = len(filas), len(lineas)
    if any(p.get("jp") for p in grupo):
        # los kanji llenan todo el alto: centrar la banda de mayúsculas en el centro del original
        centro = (filas[0][0] + filas[-1][1]) / 2
        y_ini = centro - (n_n - 1) * alto_linea / 2 - (top_ref + bot_ref) / 2
    else:
        y_ini = y0 - top_ref
        if centrado and n_n != n_o:
            sube = (n_n - n_o) * alto_linea
            abajo_pantalla = y0 > arr.shape[0] * 0.5
            y_ini -= sube if (sube > 0 and abajo_pantalla and est["fondo"] == "textura") else sube / 2
    ox, oy = int(izq_abs - margen), int(y_ini - margen)
    lienzo = Image.new("RGBA", (int(ancho_txt + 2 * margen),
                                int((n_n - 1) * alto_linea + asc + desc + 2 * margen + 4)), (0, 0, 0, 0))
    d = ImageDraw.Draw(lienzo)
    if est["pixel"]:
        d.fontmode = "1"
    kw = {}
    if est["contorno"]:
        kw = {"stroke_width": est["grosor"], "stroke_fill": est["contorno"]}
    elif modo == "suave":       # sobre el vidrio: un borde fino que contraste con la letra
        claro = np.mean(est["relleno"]) > 128
        kw = {"stroke_width": max(1, int(f.size / 18)), "stroke_fill": (0, 0, 0) if claro else (255, 255, 255)}
    y = y_ini - oy
    for linea in lineas:
        w = f.getlength(linea) * compresion
        x = (cx - w / 2 if centrado else x0) - ox
        if compresion >= 0.999:
            capa, dd, xx = lienzo, d, x
        else:                                         # dibujar ancha y estrechar después
            capa = Image.new("RGBA", (int(f.getlength(linea)) + 2 * margen, lienzo.height), (0, 0, 0, 0))
            dd, xx = ImageDraw.Draw(capa), margen
        if est["pixel"]:
            dd.fontmode = "1"
        if est["sombra"]:
            (sx, sy), col = est["sombra"]
            _texto_con_respaldo(dd, (xx + sx, y + sy), linea, f, ruta, respaldo, fill=col, **kw)
        _texto_con_respaldo(dd, (xx, y), linea, f, ruta, respaldo, fill=est["relleno"], **kw)
        if capa is not lienzo:
            bb = capa.getbbox()
            if bb:
                trozo = capa.crop(bb)
                nuevo = trozo.resize((max(1, int(trozo.width * compresion)), trozo.height), Image.LANCZOS)
                lienzo.alpha_composite(nuevo, (max(0, int(x)), bb[1]))
        y += alto_linea
    return lienzo, (ox, oy)


def vidrio(arr, caja, claro=True, alto=20):
    """Vidrio esmerilado sobre una región (absoluta) de la captura: (RGBA, (x, y))."""
    H, W = arr.shape[:2]
    pad = max(6, int(alto * 0.5))
    x0, y0, x1, y1 = [int(v) for v in caja]
    rx0, ry0 = max(0, x0 - 2 * pad), max(0, y0 - 2 * pad)
    rx1, ry1 = min(W, x1 + 2 * pad), min(H, y1 + 2 * pad)
    if rx1 - rx0 < 4 or ry1 - ry0 < 4:
        return None, (0, 0)
    region = Image.fromarray(arr[ry0:ry1, rx0:rx1])
    borroso = np.asarray(region.filter(ImageFilter.GaussianBlur(max(4, alto * 0.7))), np.float32)
    borroso = borroso * 0.62 if claro else borroso * 0.7 + 255 * 0.3
    a = Image.new("L", region.size, 0)
    ImageDraw.Draw(a).rounded_rectangle((x0 - rx0 - pad, y0 - ry0 - pad * 0.6, x1 - rx0 + pad, y1 - ry0 + pad * 0.6),
                                        radius=pad * 1.2, fill=255)
    a = a.filter(ImageFilter.GaussianBlur(pad * 0.5))
    return Image.fromarray(np.dstack([borroso.clip(0, 255).astype(np.uint8), np.asarray(a)]), "RGBA"), (rx0, ry0)


def _pegar(lienzo, im, x, y):
    """alpha_composite que admite posiciones negativas (recorta lo que sobra)."""
    sx, sy = max(0, -x), max(0, -y)
    if sx >= im.width or sy >= im.height:
        return
    lienzo.alpha_composite(im, (max(0, x), max(0, y)), (sx, sy))


def componer_piezas(arr, piezas, fuente_de, respaldo=None):
    """Una imagen RGBA por cuadro de texto (borrado o vidrio + traducción).
    piezas: [{'est', 'trad', 'modo', 'jp'?, 'id'?}]. Devuelve [{'ids', 'img', 'x', 'y'}]
    en coordenadas de arr."""
    H, W = arr.shape[:2]
    res = []
    for g in agrupar(piezas):
        trad = " ".join(p["trad"] for p in g)
        m = g[0].get("modo", "natural")
        try:
            texto, (tx, ty) = escribir(g, trad, fuente_de(g[0]), arr, m, respaldo)
        except Exception:
            continue
        capas = []
        if m == "natural":
            for p in g:
                b, (bx, by) = borrado(p["est"], "natural")
                capas.append((b, int(bx), int(by)))
        else:
            cajas = [geometria(p["est"])[0] for p in g]
            bb = texto.getbbox()
            if bb:
                cajas.append((tx + bb[0], ty + bb[1], tx + bb[2], ty + bb[3]))
            caja = (min(c[0] for c in cajas), min(c[1] for c in cajas),
                    max(c[2] for c in cajas), max(c[3] for c in cajas))
            v, (vx, vy) = vidrio(arr, caja, np.mean(g[0]["est"]["relleno"]) > 128, _alto_fila(g[0]["est"]))
            if v is not None:
                capas.append((v, int(vx), int(vy)))
        capas.append((texto, int(tx), int(ty)))
        x0 = max(0, min(c[1] for c in capas))
        y0 = max(0, min(c[2] for c in capas))
        x1 = min(W, max(c[1] + c[0].width for c in capas))
        y1 = min(H, max(c[2] + c[0].height for c in capas))
        if x1 - x0 < 2 or y1 - y0 < 2:
            continue
        lienzo = Image.new("RGBA", (x1 - x0, y1 - y0), (0, 0, 0, 0))
        for im, x, y in capas:
            _pegar(lienzo, im, x - x0, y - y0)
        res.append({"ids": tuple(p.get("id", i) for i, p in enumerate(g)), "img": lienzo, "x": x0, "y": y0})
    return res


def componer(arr, piezas, fuente_de, modo="natural", respaldo=None):
    """Todo en una sola capa del tamaño de arr (vista previa y pruebas)."""
    H, W = arr.shape[:2]
    capa = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    for p in piezas:
        p.setdefault("modo", modo)
    for r in componer_piezas(arr, piezas, fuente_de, respaldo):
        _pegar(capa, r["img"], r["x"], r["y"])
    return capa


def _texto_con_respaldo(d, xy, texto, f, ruta, respaldo, **kw):
    """Escribe con la fuente elegida; las letras que esa fuente no tiene (á, ñ, ¡, ¿… en fuentes
    de juegos hechos solo en inglés) salen con la de respaldo, sobre la misma línea base."""
    cob = cobertura(ruta) if (ruta and respaldo is not None) else None
    if not cob or all(c.isspace() or ord(c) in cob for c in texto):
        d.text(xy, texto, font=f, **kw)
        return
    tramos = []
    for c in texto:
        propia = c.isspace() or ord(c) in cob
        if tramos and tramos[-1][0] == propia:
            tramos[-1][1] += c
        else:
            tramos.append([propia, c])
    otra = respaldo.font_variant(size=max(1, int(round(f.size))))
    x, y = xy
    base = y + f.getmetrics()[0]
    for propia, t in tramos:
        fuente = f if propia else otra
        d.text((x, base), t, font=fuente, anchor="ls", **kw)
        x += fuente.getlength(t)
