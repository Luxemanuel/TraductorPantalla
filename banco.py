"""Banco de pruebas de las decisiones en vivo (¿sigue igual el texto de una línea ya traducida?).

Genera pares (antes, ahora) de una línea de texto con muchas letras, tamaños, colores y fondos
(lisos, degradados, animados detrás de cuadros semitransparentes), y la verdad de cada par:
'igual' (mismo texto, aunque haya una molestia: flecha que parpadea, brillo, otro color, cursor,
partículas, ruido) o 'cambio' (otra frase, otra palabra, otro número, otra letra, o se fue).
Con él se ajustaron envivo.SIGUE_IGUAL y envivo.SE_FUE, y la autoprueba mide en Windows, con el
lector de verdad, cuántas veces se equivoca la cadena completa (detector + comprobación leyendo).
"""
import glob
import os
import random

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

FRASES = ["Welcome to the village, traveler.", "Do you want to save your progress?", "The gate is closed at night.",
          "I never thought I'd see this place again.", "HP 120/150", "Gold: 3,450", "Quest: Find the lost sword.",
          "Press any key to continue", "Level 12 reached!", "Thank you, come again!", "Deadline: 2026-12-31 12:00",
          "You obtained a Silver Key.", "Are you sure? Yes / No", "Chapter 3 - The Forest", "Attack Defense Magic Items",
          "She looked at me and smiled.", "The lanterns still glow the same way.", "Time left 04:59"]
FRASES_JA = ["魔法の剣を手に入れた！", "とても有名です。", "締切：2026-12-31 12:00", "「森の外」特別募集協定",
             "ここはどこですか？", "セーブしますか？", "レベルが上がった！", "残り時間 04:59"]


def _sirve(ruta, muestra):
    """La letra dibuja de verdad esos caracteres (no cuadros vacíos ni símbolos)."""
    try:
        f = ImageFont.truetype(ruta, 24)
        def masc(c):
            im = Image.new("L", (40, 40)); ImageDraw.Draw(im).text((4, 4), c, font=f, fill=255)
            return im.tobytes()
        nada = masc("\uffff")
        vistos = set()
        for c in muestra:
            m = masc(c)
            if m == nada or not any(m):
                return False
            vistos.add(m)
        return len(vistos) == len(set(muestra))
    except Exception:
        return False


_FUENTES = None


def fuentes():
    """(latinas, japonesas) que dibujan de verdad el texto de prueba."""
    global _FUENTES
    if _FUENTES is None:
        import marca
        rutas = []
        for d in (os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"), "/usr/share/fonts",
                  marca.CARPETA_FUENTES):
            for ext in ("ttf", "otf", "ttc"):
                rutas += glob.glob(os.path.join(d, "**", "*." + ext), recursive=True)
        rutas = sorted(set(rutas))
        random.Random(7).shuffle(rutas)
        lat = [f for f in rutas[:400] if _sirve(f, _LAT_MUESTRA)][:120]
        cjk = [f for f in rutas if any(k in os.path.basename(f).lower() for k in
                                       ("gothic", "mincho", "meiryo", "yugoth", "yumin", "msyh", "simsun",
                                        "cjk", "ipag", "jp", "dotgothic", "zenmaru", "unifont"))
               and _sirve(f, _JA_MUESTRA)]
        _FUENTES = (lat, cjk)
    return _FUENTES


_LAT_MUESTRA = "AaBbeRgk12!?"
_JA_MUESTRA = "魔法剣入有名締切森外特別募集協定こはどセブレベル残時間！？"


def _fuente(r, cjk, px):
    lat, cjk_l = fuentes()
    lista = (cjk_l or lat) if cjk else lat
    for _ in range(10):
        try:
            return ImageFont.truetype(r.choice(lista), px)
        except Exception:
            continue
    return ImageFont.truetype(lista[0], px)


def _cambiar(r, t):
    """Otro texto parecido: una palabra, un número o una letra distinta, o una frase entera."""
    tipo = r.choice(["frase", "palabra", "numero", "letra", "numero", "palabra"])
    if tipo == "numero" and any(c.isdigit() for c in t):
        i = r.choice([k for k, c in enumerate(t) if c.isdigit()])
        return t[:i] + r.choice([d for d in "0123456789" if d != t[i]]) + t[i + 1:], "numero"
    if tipo == "palabra" and " " in t:
        ps = t.split(" "); i = r.randrange(len(ps))
        nueva = r.choice(["house", "night", "magic", "door", "river", "stone", "light", "sword", "this", "that"])
        if nueva != ps[i]:
            ps[i] = nueva
            return " ".join(ps), "palabra"
    if tipo == "letra":
        idx = [k for k, c in enumerate(t) if c.isalpha()]
        if idx:
            i = r.choice(idx); c = t[i]
            if c.isascii():
                otra = r.choice([x for x in "aeiourstnl" if x != c.lower()])
            else:
                otra = r.choice([x for x in "あいうえおかきくけこ" if x != c])
            return t[:i] + otra + t[i + 1:], "letra"
    pool = FRASES_JA if not t.isascii() else FRASES
    return r.choice([f for f in pool if f != t]), "frase"


def _fondo(r, W, H, tipo, var):
    if tipo == "liso":
        return Image.new("RGB", (W, H), tuple(r.randrange(0, 256) for _ in range(3)))
    if tipo == "degradado":
        a = np.array([r.randrange(256) for _ in range(3)], float); b = np.array([r.randrange(256) for _ in range(3)], float)
        x = np.linspace(0, 1, W)[None, :, None]
        return Image.fromarray(np.broadcast_to(a * (1 - x) + b * x, (H, W, 3)).astype(np.uint8))
    # manchas que se mueven (var = fotograma)
    rr = random.Random(r.random())
    im = Image.new("RGB", (W, H), tuple(rr.randrange(0, 80) for _ in range(3)))
    d = ImageDraw.Draw(im)
    for k in range(6):
        cx = (rr.randrange(W) + var * rr.randrange(5, 25)) % (W + 80) - 40
        cy = rr.randrange(H); rad = rr.randrange(20, 70)
        d.ellipse((cx - rad, cy - rad, cx + rad, cy + rad), fill=tuple(rr.randrange(60, 256) for _ in range(3)))
    return im.filter(ImageFilter.GaussianBlur(12))


def caso(semilla):
    """Devuelve (img_antes, img_ahora, caja, etiqueta, detalle)."""
    r = random.Random(semilla)
    cjk = r.random() < 0.3 and bool(fuentes()[1])
    texto = r.choice(FRASES_JA if cjk else FRASES)
    px = r.choice([12, 14, 16, 18, 20, 24, 28, 34, 40])
    f = _fuente(r, cjk, px)
    W = int(f.getlength(texto)) + 120; H = px * 3 + 40
    fondo = r.choice(["liso", "liso", "degradado", "animado"])
    caja_cuadro = r.random() < 0.6
    opac = r.choice([90, 150, 200, 255])
    col_fondo_caja = tuple(r.randrange(0, 70) for _ in range(3))
    col = tuple(r.randrange(150, 256) for _ in range(3)) if r.random() < 0.8 else tuple(r.randrange(0, 90) for _ in range(3))
    if caja_cuadro is False and fondo == "liso":
        pass
    contorno = r.random() < 0.25
    x, y = 50, H // 2 - px // 2 - 4
    # qué pasa entre 'antes' y 'ahora'
    etiqueta = "cambio" if r.random() < 0.45 else "igual"
    molestia = r.choice(["nada", "ruido", "fondo", "flecha", "flecha_dentro", "brillo", "cursor", "particula", "color"])
    texto2, detalle = (texto, molestia)
    if etiqueta == "cambio":
        texto2, detalle = _cambiar(r, texto)
        if r.random() < 0.15:
            texto2, detalle = "", "se va"
    var0, var1 = r.randrange(100), r.randrange(100)

    def pintar(t, var, mol):
        im = _fondo(random.Random(semilla), W, H, fondo, var if fondo == "animado" else 0).convert("RGBA")
        if caja_cuadro:
            capa = Image.new("RGBA", im.size, (0, 0, 0, 0))
            ImageDraw.Draw(capa).rounded_rectangle((20, 10, W - 20, H - 10), radius=8, fill=col_fondo_caja + (opac,))
            im.alpha_composite(capa)
        d = ImageDraw.Draw(im)
        c = col
        if mol == "color":
            c = tuple(min(255, int(v * 0.8 + 60)) for v in col[::-1])
        if mol == "brillo":
            c = tuple(min(255, int(v * 1.12)) for v in col)
        if t:
            if contorno:
                d.text((x, y), t, font=f, fill=c, stroke_width=2, stroke_fill=(0, 0, 0))
            else:
                d.text((x, y), t, font=f, fill=c)
        fin = x + f.getlength(texto)
        if mol == "flecha":
            d.polygon([(fin + 10, y + px * 0.5), (fin + 10 + px * 0.6, y + px * 0.5), (fin + 10 + px * 0.3, y + px)], fill=(240, 240, 255))
        if mol == "flecha_dentro":
            d.polygon([(fin - px * 0.6, y + px * 0.9), (fin, y + px * 0.9), (fin - px * 0.3, y + px * 1.3)], fill=(240, 240, 255))
        if mol == "cursor":
            d.rectangle((fin + 3, y, fin + 5, y + px), fill=c)
        if mol == "particula":
            for _ in range(3):
                px_ = r.randrange(x, int(fin)); py_ = r.randrange(y, y + px)
                d.ellipse((px_ - 3, py_ - 3, px_ + 3, py_ + 3), fill=(255, 255, 200))
        im = im.convert("RGB")
        if mol == "ruido":
            a = np.asarray(im).astype(np.int16) + np.random.default_rng(var).integers(-6, 7, (H, W, 3))
            im = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))
        return im

    antes = pintar(texto, var0, "nada")
    ahora = pintar(texto2, var1 if fondo == "animado" else var0,
                   molestia if etiqueta == "igual" else r.choice(["nada", "nada", "ruido", "fondo"]))
    # la caja como la daría el lector: ajustada a la tinta, con algún píxel de margen
    bb = ImageDraw.Draw(Image.new("L", (W, H))).textbbox((x, y), texto, font=f)
    j = lambda: r.randint(-2, 2)
    caja = (bb[0] + j(), bb[1] + j(), bb[2] + j(), bb[3] + j())
    return antes, ahora, caja, etiqueta, detalle, texto, texto2


def medir(motor, n=300, inicio=50000, ejemplos=12):
    """La cadena completa de una línea fija, como en vivo: detector de trazos y, si duda,
    comprobación leyendo (ahora y como era) con el lector real. Devuelve (texto del informe,
    desfases, parpadeos). Desfase = cambió el texto y la traducción vieja se quedaría.
    Parpadeo = mismo texto y la traducción se quitaría."""
    import collections
    import time
    import envivo
    tot, desf, parp = collections.Counter(), collections.Counter(), collections.Counter()
    decis = collections.Counter()
    malos, t_lect, n_lect = [], 0.0, 0
    for i in range(inicio, inicio + n):
        a, b, caja, etq, det, t1, t2 = caso(i)
        h = envivo.huella(a, caja)
        if h is None or h.sum() < envivo.MIN_TRAZOS:
            continue
        tot[etq] += 1
        dif, en = envivo.comparar_trazos(envivo.paso_alto(a, caja), envivo.paso_alto(b, caja), caja[3] - caja[1])
        if dif < envivo.SIGUE_IGUAL:
            final, como = "mantener", "trazos"
        elif en < envivo.SE_FUE:
            final, como = "quitar", "trazos"
        else:
            cv = envivo._caja_verif(caja, a)
            pista = None if t1.isascii() else "ja"
            t0 = time.perf_counter()
            leer = lambda im: " ".join(p["t"] for p in motor.leer_parrafos(im, "auto", True, pista=pista))
            ahora, antes = leer(b.crop(cv)), leer(a.crop(cv))
            t_lect += time.perf_counter() - t0
            n_lect += 1
            final = "quitar" if envivo.otro_texto_estricto(ahora, antes, en) else "mantener"
            como = f"leyendo «{antes[:30]}» → «{ahora[:30]}»"
        decis[(etq, final)] += 1
        if etq == "cambio" and final == "mantener":
            desf[det] += 1
            malos.append(f"    desfase #{i} {det}: {t1!r} → {t2!r} · {como}")
        if etq == "igual" and final == "quitar":
            parp[det] += 1
            malos.append(f"    parpadeo #{i} {det}: {t1!r} · {como}")
    nd, np_ = sum(desf.values()), sum(parp.values())
    texto = (f"Banco de decisiones en vivo: {tot['cambio']} cambios, {tot['igual']} sin cambio · "
             f"desfases {nd} ({100 * nd / max(1, tot['cambio']):.2f}%) {dict(desf)} · "
             f"parpadeos {np_} ({100 * np_ / max(1, tot['igual']):.2f}%) {dict(parp)} · "
             f"comprobadas leyendo {n_lect} ({1000 * t_lect / max(1, n_lect):.0f} ms cada una)")
    return "\n".join([texto] + malos[:ejemplos]), nd / max(1, tot["cambio"]), np_ / max(1, tot["igual"])


def recolectar(motor, n=1500, inicio=60000, ruta="banco_datos.jsonl"):
    """Datos para ajustar la comprobación leyendo: por cada caso, los rasgos de imagen y lo que
    lee el lector real en dos recortes (justo y con más margen), antes y ahora. Una línea JSON
    por caso en 'ruta'."""
    import json
    import time
    import envivo
    t0 = time.time()
    salida = open(ruta, "w", encoding="utf-8")
    print = lambda texto, flush=True: salida.write(texto + "\n")
    for i in range(inicio, inicio + n):
        try:
            a, b, caja, etq, det, t1, t2 = caso(i)
            h = envivo.huella(a, caja)
            if h is None or h.sum() < envivo.MIN_TRAZOS:
                continue
            alto = caja[3] - caja[1]
            dif, en = envivo.comparar_trazos(envivo.paso_alto(a, caja), envivo.paso_alto(b, caja), alto)
            pista = None if t1.isascii() else "ja"
            leer = lambda im: " ".join(p["t"] for p in motor.leer_parrafos(im, "auto", True, pista=pista))
            m2 = max(12, int(1.2 * alto))
            c2 = (max(0, caja[0] - m2), max(0, caja[1] - m2), min(a.width, caja[2] + m2), min(a.height, caja[3] + m2))
            c1 = envivo._caja_verif(caja, a)
            d = dict(i=i, e=etq, d=det, t1=t1, t2=t2, dif=round(dif, 4), en=round(en, 4), alto=alto,
                     a1=leer(a.crop(c1)), b1=leer(b.crop(c1)), a2=leer(a.crop(c2)), b2=leer(b.crop(c2)))
            print("BANCO_JSON " + json.dumps(d, ensure_ascii=True), flush=True)
        except Exception as e:
            print("BANCO_JSON " + json.dumps({"i": i, "error": str(e)}), flush=True)
    print(f"BANCO_JSON_FIN {time.time() - t0:.0f} s", flush=True)
    salida.close()
    return time.time() - t0
