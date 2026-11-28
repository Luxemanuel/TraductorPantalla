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
    """(antes, ahora, caja, etiqueta, detalle, texto_antes, texto_ahora): un solo fotograma."""
    antes, cuadros, caja, etq, det, t1, t2 = secuencia(semilla, 1)
    return antes, cuadros[0], caja, etq, det, t1, t2


def secuencia(semilla, k=5):
    """Como caso(), pero con k fotogramas seguidos (cada 0,1 s) después del momento del cambio:
    el fondo animado se mueve, el ruido y las partículas cambian en cada uno, el brillo late, la
    flecha y el cursor parpadean (cada 0,5 s). Un cambio de texto se mantiene en todos."""
    import math
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
        molestia = r.choice(["nada", "nada", "ruido", "fondo"])
    var0, var1 = r.randrange(100), r.randrange(100)
    fase = r.random()

    def pintar(t, var, mol, n):
        rr = random.Random(semilla * 31 + n)
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
            fx = 1.0 + 0.14 * math.sin(n * 1.3 + fase * 6.3)
            c = tuple(max(0, min(255, int(v * fx))) for v in col)
        if t:
            if contorno:
                d.text((x, y), t, font=f, fill=c, stroke_width=2, stroke_fill=(0, 0, 0))
            else:
                d.text((x, y), t, font=f, fill=c)
        fin = x + f.getlength(texto)
        visible = ((n * 0.1 + fase) % 1.0) < 0.5 or n == 0
        if mol == "flecha" and visible:
            d.polygon([(fin + 10, y + px * 0.5), (fin + 10 + px * 0.6, y + px * 0.5), (fin + 10 + px * 0.3, y + px)], fill=(240, 240, 255))
        if mol == "flecha_dentro" and visible:
            d.polygon([(fin - px * 0.6, y + px * 0.9), (fin, y + px * 0.9), (fin - px * 0.3, y + px * 1.3)], fill=(240, 240, 255))
        if mol == "cursor" and visible:
            d.rectangle((fin + 3, y, fin + 5, y + px), fill=c)
        if mol == "particula":
            for _ in range(3):
                px_ = rr.randrange(x, int(fin)); py_ = rr.randrange(y, y + px)
                d.ellipse((px_ - 3, py_ - 3, px_ + 3, py_ + 3), fill=(255, 255, 200))
        im = im.convert("RGB")
        if mol == "ruido":
            a = np.asarray(im).astype(np.int16) + np.random.default_rng(var * 7 + n).integers(-6, 7, (H, W, 3))
            im = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))
        return im

    antes = pintar(texto, var0, "nada", -1)
    cuadros = [pintar(texto2, (var1 + 2 * n) if fondo == "animado" else var0, molestia, n) for n in range(k)]
    # la caja como la daría el lector: ajustada a la tinta, con algún píxel de margen
    bb = ImageDraw.Draw(Image.new("L", (W, H))).textbbox((x, y), texto, font=f)
    j = lambda: r.randint(-2, 2)
    caja = (bb[0] + j(), bb[1] + j(), bb[2] + j(), bb[3] + j())
    return antes, cuadros, caja, etiqueta, detalle, texto, texto2


def mediana(cuadros):
    """El fotograma 'mediano' (píxel a píxel): lo pasajero (partículas, ruido, parpadeos cortos)
    desaparece y lo que se queda (el texto) permanece."""
    return Image.fromarray(np.median(np.stack([np.asarray(c) for c in cuadros]), axis=0).astype(np.uint8))


def medir(motor, n=300, inicio=50000, ejemplos=12):
    """La cadena completa de una línea fija, como en vivo (envivo.SesionEnVivo._dudar), con el
    lector real: 1 s de fotogramas tras el cambio. Devuelve (informe, desfases, parpadeos).
    Desfase = cambió el texto y la traducción vieja se quedaría.
    Parpadeo = mismo texto y la traducción se quitaría."""
    import collections
    import time
    import comprobar
    import envivo
    K, G = envivo.SIGUE_IGUAL, envivo.SE_FUE
    tot, desf, parp, como_c = collections.Counter(), collections.Counter(), collections.Counter(), collections.Counter()
    malos, t_lect, n_lect = [], 0.0, 0
    for i in range(inicio, inicio + n):
        a, cs, caja, etq, det, t1, t2 = secuencia(i, 10)
        h = envivo.huella(a, caja)
        if h is None or h.sum() < envivo.MIN_TRAZOS:
            continue
        tot[etq] += 1
        alto = caja[3] - caja[1]
        pa = envivo.paso_alto(a, caja)
        cv = envivo.caja_lectura(caja, a)
        leido_ref = None
        p0 = envivo.paso_alto(cs[0], caja)
        dif, en = envivo.comparar_trazos(pa, p0, alto)
        primero = envivo.rasgos_imagen(pa, p0, alto)
        final, como = None, ""
        if dif < K:
            final, como = "mantener", "igual"
        elif en < G:
            final, como = "quitar", "se fue"
        for j in range(1, 10):
            if final:
                break
            dj, ej = envivo.comparar_trazos(pa, envivo.paso_alto(cs[j], caja), alto)
            if dj < K:
                final, como = "mantener", "vuelve"; break
            if ej < G:
                final, como = "quitar", "se fue"; break
            if j < 2:
                continue
            med = envivo.mediana(cs[max(0, j - 4):j + 1])
            pm = envivo.paso_alto(med, caja)
            dm, em = envivo.comparar_trazos(pa, pm, alto)
            if dm < K:
                final, como = "mantener", "mediana"; break
            if em < G:
                final, como = "quitar", "se fue"; break
            if j in (3, 9):
                t0 = time.perf_counter()
                pista = None if t1.isascii() else "ja"
                leer = lambda im: " ".join(p["t"] for p in motor.leer_parrafos(im.crop(cv), "auto", True, pista=pista))
                if leido_ref is None:
                    leido_ref = leer(a)
                ahora_txt = leer(med)
                t_lect += time.perf_counter() - t0
                n_lect += 1
                modelo = comprobar.MODELOS.get("final" if j == 9 else "temprano")
                img_ahora = envivo.rasgos_imagen(pa, pm, alto)
                if comprobar.misma_lectura(leido_ref, ahora_txt) and not envivo.otro_texto_claro(ahora_txt, leido_ref):
                    prob = 0.0
                elif j != 9 and not envivo._palabras(ahora_txt) and img_ahora["en"] >= 0.6:
                    prob = 0.5
                elif modelo:
                    prob = comprobar.predecir(modelo, comprobar.rasgos(leido_ref, ahora_txt, primero, img_ahora, not t1.isascii()))
                else:
                    prob = 1.0 if (envivo.otro_texto_estricto(ahora_txt, leido_ref, img_ahora["en"])
                                   or envivo.otro_texto(ahora_txt, leido_ref)) else 0.0
                como = f"leyendo «{leido_ref[:28]}» → «{ahora_txt[:28]}» p={prob:.2f}"
                if prob >= comprobar.SEGURO_CAMBIO or (j == 9 and prob >= 0.5):
                    final = "quitar"
                elif prob <= comprobar.SEGURO_IGUAL or j == 9:
                    final = "mantener"
        final = final or "quitar"
        como_c[(etq, como.split(" ")[0])] += 1
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
             f"lecturas {n_lect} ({1000 * t_lect / max(1, n_lect):.0f} ms) · modelo {'sí' if comprobar.MODELOS else 'no'}")
    return "\n".join([texto] + malos[:ejemplos]), nd / max(1, tot["cambio"]), np_ / max(1, tot["igual"])


def trozos(p0, p1, alto):
    """Diferencia de cada trozo del ancho de una letra (las mismas cuentas que
    envivo.comparar_trazos): (difs, posiciones relativas 0..1)."""
    if p0 is None or p1 is None or p0.shape != p1.shape:
        return np.array([2.0]), np.array([0.5])
    a2, b2, d2 = (p0 * p0).sum(axis=(0, 2)), (p1 * p1).sum(axis=(0, 2)), ((p0 - p1) ** 2).sum(axis=(0, 2))
    n = p0.shape[0] * p0.shape[2]
    W = len(a2)
    w = max(5, int(alto * 0.45))
    if W <= w:
        return np.array([float(d2.sum()) / max(1e-3, float(a2.sum() + b2.sum()))]), np.array([0.5])
    ca, cb, cd = (np.concatenate(([0.0], np.cumsum(v))) for v in (a2, b2, d2))
    pos = np.array(list(range(0, W - w, max(2, w // 2))) + [W - w])
    sa, sb, sd = ca[pos + w] - ca[pos], cb[pos + w] - cb[pos], cd[pos + w] - cd[pos]
    ref = max(float(a2.sum()) / (n * W), float(b2.sum()) / (n * W), 1e-3)
    t = np.maximum(sa, sb) / (n * w) >= 0.12 * ref
    if not t.any():
        return np.array([0.0]), np.array([0.5])
    return sd[t] / (sa[t] + sb[t]), (pos[t] + w / 2) / W


def rasgos_imagen(pa, im, caja, prefijo):
    """Rasgos de imagen de 'im' frente a la referencia (paso alto 'pa') en la caja."""
    import envivo
    alto = caja[3] - caja[1]
    pb = envivo.paso_alto(im, caja)
    dif, en = envivo.comparar_trazos(pa, pb, alto)
    ds, ps = trozos(pa, pb, alto)
    k = envivo.SIGUE_IGUAL
    return {prefijo + "dif": dif, prefijo + "en": en, prefijo + "n": float((ds >= k).sum()),
            prefijo + "frac": float((ds >= k).mean()), prefijo + "pos": float(ps[int(np.argmax(ds))]),
            prefijo + "d2": float(np.sort(ds)[-2]) if len(ds) > 1 else 0.0}


def recolectar(motor, n=2500, inicio=100000, ruta="banco_datos.jsonl", k=10):
    """Datos para ajustar la comprobación, como funciona en vivo: 10 fotogramas (1 s) tras el
    cambio. Si en ese segundo vuelve a verse igual que antes (o lo hace la mediana de los últimos
    fotogramas), se resuelve sin leer. Si no, lo que lee el lector real (recorte amplio) en la
    imagen de antes, en la mediana de los 3 primeros fotogramas y en la de los 10."""
    import json
    import time
    import envivo
    K, G = envivo.SIGUE_IGUAL, envivo.SE_FUE
    t0 = time.time()
    salida = open(ruta, "w", encoding="utf-8")
    for i in range(inicio, inicio + n):
        try:
            a, cs, caja, etq, det, t1, t2 = secuencia(i, k)
            h = envivo.huella(a, caja)
            if h is None or h.sum() < envivo.MIN_TRAZOS:
                continue
            alto = caja[3] - caja[1]
            pa = envivo.paso_alto(a, caja)
            d = dict(i=i, e=etq, d=det, t1=t1, t2=t2, alto=alto, cjk=float(not t1.isascii()))
            d.update(rasgos_imagen(pa, cs[0], caja, "f"))
            resuelto = None
            if d["fdif"] < K:
                resuelto = "igual"
            elif d["fen"] < G:
                resuelto = "se fue"
            else:
                for j in range(1, k):
                    dj, ej = envivo.comparar_trazos(pa, envivo.paso_alto(cs[j], caja), alto)
                    if dj < K:
                        resuelto = "vuelve"; break
                    if ej < G:
                        resuelto = "se fue"; break
                    if j >= 2:
                        dm, em = envivo.comparar_trazos(pa, envivo.paso_alto(mediana(cs[max(0, j - 4):j + 1]), caja), alto)
                        if dm < K:
                            resuelto = "mediana"; break
            d["resuelto"] = resuelto
            if resuelto is None:
                m3, m10 = mediana(cs[:3]), mediana(cs[-5:])
                d.update(rasgos_imagen(pa, m3, caja, "m"))
                d.update(rasgos_imagen(pa, m10, caja, "u"))
                pista = None if t1.isascii() else "ja"
                cv = caja_lectura(caja, a)
                leer = lambda im: " ".join(p["t"] for p in motor.leer_parrafos(im.crop(cv), "auto", True, pista=pista))
                d["ra"], d["rm"], d["ru"] = leer(a), leer(m3), leer(m10)
            salida.write("BANCO_JSON " + json.dumps(d, ensure_ascii=True) + "\n")
        except Exception as e:
            salida.write("BANCO_JSON " + json.dumps({"i": i, "error": repr(e)}) + "\n")
    salida.close()
    return time.time() - t0


def caja_lectura(caja, img):
    """El recorte para leer una línea al comprobarla: con margen (el lector acierta más)."""
    alto = caja[3] - caja[1]
    m = max(12, int(1.2 * alto))
    return (max(0, caja[0] - m), max(0, caja[1] - m), min(img.width, caja[2] + m), min(img.height, caja[3] + m))
