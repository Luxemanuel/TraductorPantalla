# -*- coding: utf-8 -*-
"""
Imagen del Traductor de Pantalla: emblema, logotipo e iconos de la interfaz.

El emblema es un arco gótico en forma de globo de diálogo (rojo vino con aro de hueso),
con una 𝔄 gótica y una medalla de obsidiana con «あ»: el texto del juego que pasa a tu idioma.

Las imágenes del emblema van ya dibujadas dentro de marca_datos.py (no dependen de las letras
instaladas). Para volver a dibujarlas:   python marca.py --generar   (necesita fuentes/NotoSerifJP.ttf).
"""
import base64
import io
import math
import os
import struct
import sys
from functools import lru_cache

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

# colores de la marca (los mismos del tema)
OBSIDIANA = (14, 12, 16)
VINO = (122, 21, 48)
HUESO = (237, 228, 211)
PLATA = (200, 202, 210)

TAMANOS_ICO = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)
_SIN_MEDALLA = 24          # hasta este tamaño el emblema va sin la medalla (se leería como un borrón)


def _base():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    aqui = os.path.dirname(os.path.abspath(__file__))
    if os.path.basename(aqui) == "app" and os.path.isdir(os.path.join(os.path.dirname(aqui), "fuentes")):
        return os.path.dirname(aqui)
    return aqui


CARPETA_FUENTES = os.path.join(_base(), "fuentes")


def _fuente(nombre, tam, variacion=None):
    ruta = os.path.join(CARPETA_FUENTES, nombre)
    f = ImageFont.truetype(ruta, max(4, int(round(tam))))
    if variacion:
        try:
            f.set_variation_by_name(variacion)
        except Exception:
            pass
    return f


# ============================================================================
# Emblema (ya dibujado)
# ============================================================================
@lru_cache(maxsize=None)
def _png(clave):
    import marca_datos
    return Image.open(io.BytesIO(base64.b64decode(getattr(marca_datos, clave)))).convert("RGBA")


def emblema(tam):
    """El emblema a 'tam' píxeles (RGBA). Los tamaños pequeños usan su versión simplificada."""
    tam = int(round(tam))
    if tam <= _SIN_MEDALLA:
        for t in (16, 20, 24):
            if tam <= t:
                im = _png(f"PEQ_{t}")
                return im if im.width == tam else im.resize((tam, tam), Image.LANCZOS)
    grande = _png("EMBLEMA_512")
    return grande.resize((tam, tam), Image.LANCZOS)


def escribir_ico(ruta):
    """Icono .ico con cada tamaño dibujado aparte (nítido en la barra de título y en el explorador)."""
    datos = []
    for t in TAMANOS_ICO:
        b = io.BytesIO()
        emblema(t).save(b, "PNG", optimize=True)
        datos.append((t, b.getvalue()))
    cab = struct.pack("<HHH", 0, 1, len(datos))
    pos = 6 + 16 * len(datos)
    entradas, cuerpo = b"", b""
    for t, png in datos:
        lado = 0 if t >= 256 else t
        entradas += struct.pack("<BBBBHHII", lado, lado, 0, 0, 1, 32, len(png), pos)
        cuerpo += png
        pos += len(png)
    with open(ruta, "wb") as f:
        f.write(cab + entradas + cuerpo)


# ============================================================================
# Logotipo (se dibuja con la letra Cinzel incluida en la carpeta "fuentes")
# ============================================================================
@lru_cache(maxsize=8)
def logotipo(alto, texto="TRADUCTOR DE PANTALLA", color=HUESO):
    """Imagen RGBA del nombre del programa, de 'alto' píxeles de mayúscula. None si falta la letra."""
    try:
        f = _fuente("Cinzel.ttf", alto * 1.42, "Bold")
    except Exception:
        return None
    k = 3                                          # dibujar grande y reducir: bordes suaves
    fk = _fuente("Cinzel.ttf", alto * 1.42 * k, "Bold")
    espacio = 0.11 * alto * k
    ancho = sum(fk.getlength(c) for c in texto) + espacio * (len(texto) - 1)
    asc, desc = fk.getmetrics()
    im = Image.new("RGBA", (int(ancho + 8 * k), int(asc + desc + 6 * k)), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    x = 4 * k
    for c in texto:
        d.text((x, 2 * k), c, font=fk, fill=color + (255,))
        x += fk.getlength(c) + espacio
    caja = im.getbbox()
    if caja:
        im = im.crop((max(0, caja[0] - 2 * k), max(0, caja[1] - 2 * k), caja[2] + 2 * k, caja[3] + 2 * k))
    del f
    return im.resize((max(1, round(im.width / k)), max(1, round(im.height / k))), Image.LANCZOS)


# ============================================================================
# Iconos de la interfaz (trazos simples, dibujados al tamaño exacto)
# ============================================================================
@lru_cache(maxsize=64)
def icono_ui(nombre, tam, color=HUESO):
    """Iconos de línea: 'mando', 'subtitulos', 'voz', 'copiado', 'detener', 'flecha'."""
    k = 4
    n = tam * k
    im = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    c = tuple(color) + (255,)
    g = max(2, round(n * 0.085))                    # grosor del trazo
    u = n / 24.0                                     # cuadrícula de 24

    def r(x0, y0, x1, y1):
        return (x0 * u, y0 * u, x1 * u, y1 * u)

    if nombre == "mando":
        d.rounded_rectangle(r(2.5, 7, 21.5, 17.5), radius=5.2 * u, outline=c, width=g)
        d.line([(6.4 * u, 12.2 * u), (10.2 * u, 12.2 * u)], fill=c, width=g)
        d.line([(8.3 * u, 10.3 * u), (8.3 * u, 14.1 * u)], fill=c, width=g)
        for cx, cy in ((15.6, 11.0), (18.0, 13.4)):
            rr = 1.25 * u
            d.ellipse((cx * u - rr, cy * u - rr, cx * u + rr, cy * u + rr), fill=c)
    elif nombre == "subtitulos":
        d.rounded_rectangle(r(2.5, 4.5, 21.5, 19.5), radius=2.6 * u, outline=c, width=g)
        d.line([(6.5 * u, 13.2 * u), (17.5 * u, 13.2 * u)], fill=c, width=g)
        d.line([(8.5 * u, 16.2 * u), (15.5 * u, 16.2 * u)], fill=c, width=g)
    elif nombre == "voz":
        d.rounded_rectangle(r(9, 2.5, 15, 14), radius=3 * u, outline=c, width=g)
        d.arc(r(5.2, 6.5, 18.8, 17.5), start=20, end=160, fill=c, width=g)
        d.line([(12 * u, 17.6 * u), (12 * u, 21 * u)], fill=c, width=g)
        d.line([(8.6 * u, 21 * u), (15.4 * u, 21 * u)], fill=c, width=g)
    elif nombre == "copiado":
        d.rounded_rectangle(r(4.5, 4.5, 19.5, 21.5), radius=2.4 * u, outline=c, width=g)
        d.rounded_rectangle(r(8.5, 2.5, 15.5, 6.5), radius=1.4 * u, fill=c)
        d.line([(8 * u, 11.5 * u), (16 * u, 11.5 * u)], fill=c, width=g)
        d.line([(8 * u, 15.5 * u), (13.5 * u, 15.5 * u)], fill=c, width=g)
    elif nombre == "detener":
        d.rounded_rectangle(r(6, 6, 18, 18), radius=2.2 * u, fill=c)
    elif nombre == "flecha":
        d.line([(4 * u, 12 * u), (19 * u, 12 * u)], fill=c, width=g)
        d.line([(13.5 * u, 6.5 * u), (19.2 * u, 12 * u), (13.5 * u, 17.5 * u)], fill=c, width=g,
               joint="curve")
    return im.resize((tam, tam), Image.LANCZOS)


# ============================================================================
# Fondos redondeados (botones y etiquetas de atajo)
# ============================================================================
def rectangulo(ancho, alto, radio, relleno, borde=None, grosor=1, degradado=None):
    """Rectángulo redondeado suave (RGBA). degradado = color de abajo (el de arriba es 'relleno')."""
    k = 3
    W, H = max(1, int(ancho)) * k, max(1, int(alto)) * k
    forma = Image.new("L", (W, H), 0)
    ImageDraw.Draw(forma).rounded_rectangle((0, 0, W - 1, H - 1), radius=radio * k, fill=255)
    if degradado:
        a = Image.new("RGB", (1, 2))
        a.putpixel((0, 0), tuple(relleno[:3]))
        a.putpixel((0, 1), tuple(degradado[:3]))
        color = a.resize((W, H), Image.BILINEAR)
    else:
        color = Image.new("RGB", (W, H), tuple(relleno[:3]))
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    im.paste(color, (0, 0), forma)
    if len(relleno) == 4:
        im.putalpha(ImageChops.multiply(forma, Image.new("L", (W, H), relleno[3])))
    if borde:
        g = max(1, int(round(grosor * k)))
        aro = Image.new("L", (W, H), 0)
        ImageDraw.Draw(aro).rounded_rectangle((0, 0, W - 1, H - 1), radius=radio * k, outline=255, width=g)
        if len(borde) == 4:
            aro = ImageChops.multiply(aro, Image.new("L", (W, H), borde[3]))
        im.alpha_composite(Image.merge("RGBA", [Image.new("L", (W, H), v) for v in borde[:3]] + [aro]))
    return im.resize((int(ancho), int(alto)), Image.LANCZOS)


# ============================================================================
# Generador del emblema (solo para quien desarrolla: escribe marca_datos.py)
# ============================================================================
def _arco(x0, y0, x1, y1, radio=1.15, n=90):
    """Contorno de un arco apuntado (lanceta): lados rectos y punta arriba."""
    w = x1 - x0
    R = w * radio
    xm = (x0 + x1) / 2
    alto_arco = math.sqrt(R * R - (xm - (x0 + R)) ** 2)
    ys = y0 + alto_arco
    pts = [(x0, y1), (x0, ys)]
    cx_i, cx_d = x0 + R, x1 - R
    a_fin = math.atan2(-alto_arco, xm - cx_i) + 2 * math.pi
    for i in range(1, n + 1):
        a = math.pi + (a_fin - math.pi) * i / n
        pts.append((cx_i + R * math.cos(a), ys + R * math.sin(a)))
    b_ini = math.atan2(-alto_arco, xm - cx_d)
    for i in range(1, n + 1):
        a = b_ini + (0 - b_ini) * i / n
        pts.append((cx_d + R * math.cos(a), ys + R * math.sin(a)))
    pts.append((x1, y1))
    return pts


def _degradado(S, arriba, abajo):
    a = Image.new("RGB", (1, 2))
    a.putpixel((0, 0), arriba)
    a.putpixel((0, 1), abajo)
    return a.resize((S, S), Image.BILINEAR)


def _dilatar(m, r):
    return m.filter(ImageFilter.GaussianBlur(r / 2.2)).point(lambda v: 255 if v > 10 else 0)


def _erosionar(m, r):
    return ImageChops.invert(_dilatar(ImageChops.invert(m), r))


def _glifo(S, texto, fuente, centro, color):
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    bb = d.textbbox((0, 0), texto, font=fuente)
    d.text((centro[0] - (bb[0] + bb[2]) / 2, centro[1] - (bb[1] + bb[3]) / 2), texto, font=fuente, fill=color)
    return im


def _dibujar(S, peq=False):
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    forma = Image.new("L", (S, S), 0)
    d = ImageDraw.Draw(forma)
    if peq:
        d.polygon(_arco(0.10 * S, 0.02 * S, 0.90 * S, 0.84 * S), fill=255)
        d.polygon([(0.22 * S, 0.76 * S), (0.10 * S, 0.99 * S), (0.50 * S, 0.83 * S)], fill=255)
        contorno, aro = 0.035 * S, 0.085 * S
    else:
        d.polygon(_arco(0.13 * S, 0.03 * S, 0.87 * S, 0.84 * S), fill=255)
        d.polygon([(0.25 * S, 0.78 * S), (0.15 * S, 0.975 * S), (0.47 * S, 0.835 * S)], fill=255)
        contorno, aro = 0.012 * S, 0.038 * S
    cuerpo = _erosionar(forma, aro)
    im.paste(Image.new("RGB", (S, S), OBSIDIANA), (0, 0), _dilatar(forma, contorno))
    im.paste(_degradado(S, (247, 239, 226), (198, 188, 174)), (0, 0), forma)
    im.paste(_degradado(S, (180, 42, 74), (58, 6, 22)), (0, 0), cuerpo)
    if not peq:                                         # reflejo de vidrio
        r = Image.new("L", (S, S), 0)
        ImageDraw.Draw(r).ellipse((-0.35 * S, -0.55 * S, 0.95 * S, 0.42 * S), fill=34)
        r = ImageChops.multiply(r, cuerpo).filter(ImageFilter.GaussianBlur(0.01 * S))
        im.alpha_composite(Image.merge("RGBA", [Image.new("L", (S, S), 255)] * 3 + [r]))
    tam_a = (0.66 if peq else 0.52) * S
    centro_a = (0.505 * S, (0.43 if peq else 0.47) * S)
    letra = _glifo(S, "A", _fuente("UnifrakturMaguntia-Book.ttf", tam_a), centro_a, HUESO + (255,))
    if not peq:                                         # sombra suave bajo la letra
        sombra = letra.getchannel("A").filter(ImageFilter.GaussianBlur(0.012 * S)).point(lambda v: int(v * 0.7))
        desplazada = Image.new("L", (S, S), 0)
        desplazada.paste(sombra, (int(0.008 * S), int(0.014 * S)))
        im.alpha_composite(Image.merge("RGBA", [Image.new("L", (S, S), 20)] * 3
                                       + [ImageChops.multiply(desplazada, cuerpo)]))
    im.alpha_composite(letra)
    if not peq:                                         # medalla de obsidiana con «あ»
        cx, cy, rr = 0.775 * S, 0.775 * S, 0.13 * S
        dd = ImageDraw.Draw(im)
        e = 0.014 * S
        dd.ellipse((cx - rr - e, cy - rr - e, cx + rr + e, cy + rr + e), fill=OBSIDIANA + (255,))
        dd.ellipse((cx - rr, cy - rr, cx + rr, cy + rr), fill=PLATA + (255,))
        ri = rr * 0.84
        m = Image.new("L", (S, S), 0)
        ImageDraw.Draw(m).ellipse((cx - ri, cy - ri, cx + ri, cy + ri), fill=255)
        im.paste(_degradado(S, (44, 38, 50), (10, 8, 12)), (0, 0), m)
        kana = _fuente("NotoSerifJP.ttf", 1.12 * ri, "SemiBold")
        im.alpha_composite(_glifo(S, "あ", kana, (cx, cy + 0.03 * ri), (226, 228, 234, 255)))
    return im


def generar(destino=None):
    """Dibuja el emblema y escribe marca_datos.py con las imágenes en base64."""
    destino = destino or os.path.join(os.path.dirname(os.path.abspath(__file__)), "marca_datos.py")
    S = 2048
    grande = _dibujar(S).resize((512, 512), Image.LANCZOS)
    pequeno = _dibujar(S, peq=True)
    imgs = {"EMBLEMA_512": grande}
    for t in (16, 20, 24):
        imgs[f"PEQ_{t}"] = pequeno.resize((t, t), Image.LANCZOS)
    lineas = ['# -*- coding: utf-8 -*-', '"""Imágenes del emblema (PNG en base64). Generado por: python marca.py --generar"""']
    for clave, im in imgs.items():
        b = io.BytesIO()
        im.save(b, "PNG", optimize=True)
        txt = base64.b64encode(b.getvalue()).decode("ascii")
        trozos = [txt[i:i + 100] for i in range(0, len(txt), 100)]
        lineas.append(f"{clave} = (")
        lineas += [f'    "{t}"' for t in trozos]
        lineas.append(")")
    with open(destino, "w", encoding="utf-8") as f:
        f.write("\n".join(lineas) + "\n")
    return destino


if __name__ == "__main__":
    if "--generar" in sys.argv:
        print("Escrito:", generar())
    if "--ico" in sys.argv:
        escribir_ico(sys.argv[sys.argv.index("--ico") + 1])
        print("Icono escrito")
