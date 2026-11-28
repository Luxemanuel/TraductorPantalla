# -*- coding: utf-8 -*-
"""
Lectores de texto (OCR).
  · LectorWindows: el lector integrado de Windows 10/11 (Windows.Media.Ocr). Muy rápido.
    Usa a la vez el lector de inglés y el de japonés y se queda con lo mejor de cada línea.
  · LectorEasyOCR: respaldo para desarrollo en otros sistemas (no se incluye en el .exe).
Ambos devuelven "trozos": {'x0','y0','x1','y1','t','jp'} en píxeles de la imagen recibida.
"""
import asyncio
import re
import sys
import threading
import time

from PIL import Image

from nucleo import JP_RE, ESP_ENTRE_JP, corregir_ingles, log

ES_WINDOWS = sys.platform.startswith("win")

# Comando para instalar los lectores (necesita permisos de administrador)
CAPACIDADES = {"en": "Language.OCR~~~en-US~0.0.1.0", "ja": "Language.OCR~~~ja-JP~0.0.1.0",
               "zh": "Language.OCR~~~zh-CN~0.0.1.0"}
COMANDO_INSTALAR = "; ".join(f'Add-WindowsCapability -Online -Name "{c}"' for c in CAPACIDADES.values())

KANA_RE = re.compile(r"[\u3040-\u30ff\uff66-\uff9f]")
CJK_RE = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uff66-\uff9f]")


def proporcion_kana(textos):
    """Qué parte de los caracteres CJK son kana: el japonés los usa en casi toda frase; el chino nunca."""
    cjk = sum(len(CJK_RE.findall(t)) for t in textos)
    return sum(len(KANA_RE.findall(t)) for t in textos) / cjk if cjk else 0.0


def crear_lector(avisar=print):
    if ES_WINDOWS:
        try:
            return LectorWindows(avisar)
        except Exception:
            log.exception("Lector de Windows no disponible")
    return LectorEasyOCR(avisar)


def instalar_lectores_windows(idiomas=None):
    """Abre una ventana de PowerShell como administrador que instala los lectores."""
    import ctypes
    comando = COMANDO_INSTALAR if not idiomas else "; ".join(
        f'Add-WindowsCapability -Online -Name "{CAPACIDADES[i]}"' for i in idiomas)
    args = ('-NoProfile -ExecutionPolicy Bypass -Command "' + comando.replace('"', '\\"') +
            '; Write-Host; Write-Host Listo. Reinicia el Traductor de Pantalla.; pause"')
    return ctypes.windll.shell32.ShellExecuteW(None, "runas", "powershell.exe", args, None, 1) > 32


_LATINA = re.compile(r"[A-Za-z]")
_CJK = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uff66-\uff9f]")


def parece_ingles(linea_ja, lineas_en):
    """¿Esta línea que el lector japonés cree japonesa es en realidad texto latino mal leído?
    (el lector japonés «inventa» algún kanji o ー en texto inglés). Ajustado con el banco de
    pruebas (1552 casos leídos por Windows, con inglés, japonés y chino): es CJK solo si casi
    todas sus letras son CJK (≥95 %) y hay al menos 0,65 caracteres CJK por cada letra que leyó
    el lector inglés en ese sitio. Errores: 36 (la regla anterior, 50; la primera, 534)."""
    en = [l for l in lineas_en if _solapa(l, linea_ja)]
    if not en:
        return False
    tj = re.sub(r"\s", "", linea_ja["t"])
    nc, nl = len(_CJK.findall(tj)), len(_LATINA.findall(tj))
    letras_en = len(_LATINA.findall(" ".join(l["t"] for l in en)))
    return not (nc / max(1, nc + nl) >= 0.95 and nc / max(1, letras_en) > 0.65)


def _solapa(a, b):
    """True si dos cajas comparten buena parte de su área."""
    ix = min(a["x1"], b["x1"]) - max(a["x0"], b["x0"])
    iy = min(a["y1"], b["y1"]) - max(a["y0"], b["y0"])
    if ix <= 0 or iy <= 0:
        return False
    area = min((a["x1"] - a["x0"]) * (a["y1"] - a["y0"]), (b["x1"] - b["x0"]) * (b["y1"] - b["y0"]))
    return ix * iy > 0.3 * max(1, area)


# ============================================================================
# Lector de Windows
# ============================================================================
class _HiloWinRT:
    """Un hilo propio (apartamento MTA) donde viven los lectores de Windows."""

    def __init__(self):
        listo = threading.Event()

        def correr():
            try:
                import ctypes
                ctypes.windll.ole32.CoInitializeEx(None, 0)     # COINIT_MULTITHREADED
            except Exception:
                pass
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            listo.set()
            self.loop.run_forever()
        threading.Thread(target=correr, daemon=True, name="lector-windows").start()
        listo.wait(10)

    def ejecutar(self, corrutina, espera=15):
        return asyncio.run_coroutine_threadsafe(corrutina, self.loop).result(espera)


class LectorWindows:
    nombre = "Windows"

    def __init__(self, avisar=print):
        self.chino_visto = 0.0          # última vez que se vio texto chino
        self.falta_chino = False        # se vio algo que parece chino y no hay lector chino
        self.hilo = _HiloWinRT()
        self.motores = self.hilo.ejecutar(self._crear())
        if not self.motores:
            raise RuntimeError("Windows no tiene lectores de texto instalados")
        self.faltan = [n for c, n in (("en", "inglés"), ("ja", "japonés")) if c not in self.motores]
        disponibles = ", ".join(sorted(self.motores))
        log.info("Lectores de Windows: %s (etiquetas: %s)", disponibles, self.etiquetas)

    async def _crear(self):
        from winrt.windows.media.ocr import OcrEngine
        from winrt.windows.globalization import Language
        etiquetas = [l.language_tag for l in OcrEngine.available_recognizer_languages]
        self.etiquetas = etiquetas
        self.max_dim = OcrEngine.max_image_dimension
        motores = {}
        for codigo in ("en", "ja"):
            tag = next((t for t in etiquetas if t.lower().split("-")[0] == codigo), None)
            if tag:
                motores[codigo] = OcrEngine.try_create_from_language(Language(tag))
        # chino: el simplificado si está; si no, el tradicional
        for pref in ("zh-hans", "zh-cn", "zh-sg", "zh-hant", "zh-tw", "zh-hk", "zh"):
            tag = next((t for t in etiquetas if t.lower().startswith(pref)), None)
            if tag:
                motores["zh"] = OcrEngine.try_create_from_language(Language(tag))
                break
        if "en" not in motores:
            # Sin lector de inglés: uno de alfabeto latino (p. ej. el español de Windows) lee el inglés
            latino = next((t for t in etiquetas if t.lower().split("-")[0] in
                           ("es", "fr", "de", "it", "pt", "nl", "en")), None)
            if latino:
                motores["en"] = OcrEngine.try_create_from_language(Language(latino))
                motores["_en_prestado"] = True
        return {k: v for k, v in motores.items() if v is not None}

    @property
    def tiene_japones(self):
        return "ja" in self.motores

    @property
    def tiene_chino(self):
        return "zh" in self.motores

    async def _reconocer(self, img, codigos):
        from winrt.windows.graphics.imaging import SoftwareBitmap, BitmapPixelFormat
        sb = SoftwareBitmap.create_copy_from_buffer(img.tobytes(), BitmapPixelFormat.RGBA8,
                                                    img.width, img.height)
        resultados = await asyncio.gather(*(self.motores[c].recognize_async(sb) for c in codigos))
        return dict(zip(codigos, resultados))

    def leer(self, imagen, modo="auto", seguir=lambda: True, pista=None):
        """pista: idioma CJK que ya se sabe que usa el juego ("ja" o "zh"). Sirve cuando se lee
        solo un trozo de la pantalla: un renglón de puros kanji no se confunde con chino."""
        img = imagen.convert("RGBA")
        # El lector de Windows acierta más con letras de unos 20-40 px: ampliar zonas pequeñas
        # Ampliar antes de leer: con la letra pequeña de los juegos el lector la detecta mucho mejor
        # (banco de pruebas: ×2 frente a tamaño real, inglés ≤13 px 0,56 → 0,64; chino 0,37 → 0,46).
        # Pantallas de hasta 1400 px se leen ×2 y hasta 2000 px ×1,5.
        lado = max(img.size)
        escala = 2 if lado <= 1400 else (1.5 if lado <= 2000 else 1)
        if escala != 1:
            img = img.resize((int(img.width * escala), int(img.height * escala)), Image.LANCZOS)
        lim = getattr(self, "max_dim", 10000) or 10000
        if max(img.size) > lim:
            f = lim / max(img.size)
            img = img.resize((int(img.width * f), int(img.height * f)))
            escala *= f
        if modo in ("en", "ja", "zh"):
            codigos = [modo]
        else:
            # el lector chino solo cuando hace falta (si hace poco se vio chino, desde el principio)
            codigos = ["en", "ja"] + (["zh"] if (time.time() - self.chino_visto < 30 or pista == "zh")
                                      and pista != "ja" else [])
        codigos = [c for c in codigos if c in self.motores]
        if not codigos:
            return []
        res = self.hilo.ejecutar(self._reconocer(img, codigos))
        lineas = {c: self._lineas(r, escala, c) for c, r in res.items()}
        lineas = self._releer_letra_pequena(imagen, lineas, escala, lim)
        self.ultimo_crudo = lineas            # lo que dio cada lector antes de combinar (banco.py)
        if pista == "ja" and modo == "auto":
            return self._combinar(lineas, "auto_ja")
        if modo == "auto" and "zh" in self.motores and "zh" not in lineas:
            cjk = [l["t"] for l in lineas.get("ja", []) if l["jp"] and not parece_ingles(l, lineas.get("en", []))]
            if cjk and proporcion_kana(cjk) < 0.08:          # ideogramas sin kana: ¿chino?
                lineas["zh"] = self._lineas(self.hilo.ejecutar(self._reconocer(img, ["zh"]))["zh"], escala, "zh")
        return self._combinar(lineas, modo)

    def _releer_letra_pequena(self, imagen, lineas, escala, lim):
        """La letra pequeña se lee mal a tamaño normal. Medido con el banco de pruebas y el lector
        de Windows (2900 líneas): con renglones de hasta ~13 px, leer ampliado ×3 sube el acierto
        de 0,56 a 0,66 en inglés y de 0,37 a 0,51 en chino; con 14-18 px, ×2 (0,65 → 0,69, y
        respeta más los espacios); con letra mayor no mejora. Segunda pasada solo en las zonas
        con letra pequeña (aunque en la pantalla haya también letra grande)."""
        todas = [l for ls in lineas.values() for l in ls]
        if not todas or not getattr(self, "segunda_pasada", True):
            return lineas
        pequenas = [l for l in todas if 5 <= l["y1"] - l["y0"] <= 18]
        if not pequenas:
            return lineas
        m = 14
        zonas = []
        for l in sorted(pequenas, key=lambda l: (l["y0"], l["x0"])):
            r = [l["x0"] - m, l["y0"] - m, l["x1"] + m, l["y1"] + m, [l["y1"] - l["y0"]]]
            for z in zonas:
                if r[0] < z[2] and z[0] < r[2] and r[1] < z[3] and z[1] < r[3]:
                    z[0], z[1], z[2], z[3] = min(z[0], r[0]), min(z[1], r[1]), max(z[2], r[2]), max(z[3], r[3])
                    z[4] += r[4]
                    break
            else:
                zonas.append(r)
        zonas = sorted(zonas, key=lambda z: -(z[2] - z[0]) * (z[3] - z[1]))[:6]
        salida = {c: list(ls) for c, ls in lineas.items()}
        for x0, y0, x1, y1, altos in zonas:
            x0, y0 = max(0, int(x0)), max(0, int(y0))
            x1, y1 = min(imagen.width, int(x1)), min(imagen.height, int(y1))
            alto = sorted(altos)[len(altos) // 2]
            f = min(3.0 if alto <= 13 else 2.0, lim / max(1, x1 - x0, y1 - y0))   # escala total
            if f < 1.4 * escala or x1 - x0 < 8 or y1 - y0 < 8:
                continue
            zona = imagen.convert("RGBA").crop((x0, y0, x1, y1))
            zona = zona.resize((int(zona.width * f), int(zona.height * f)), Image.LANCZOS)
            res = self.hilo.ejecutar(self._reconocer(zona, list(lineas)))
            dentro = lambda l: x0 <= (l["x0"] + l["x1"]) / 2 <= x1 and y0 <= (l["y0"] + l["y1"]) / 2 <= y1
            for c, r in res.items():
                nuevas = [dict(l, x0=l["x0"] + x0, x1=l["x1"] + x0, y0=l["y0"] + y0, y1=l["y1"] + y0)
                          for l in self._lineas(r, f, c)]
                viejas = [l for l in salida.get(c, []) if dentro(l)]
                if sum(len(l["t"]) for l in nuevas) >= 0.6 * sum(len(l["t"]) for l in viejas):
                    salida[c] = [l for l in salida.get(c, []) if not dentro(l)] + nuevas
        return salida

    def _combinar(self, lineas, modo):
        ja = [l for l in lineas.get("ja", []) if l["jp"]]
        if modo != "ja":
            ja = [l for l in ja if not parece_ingles(l, lineas.get("en", []))]
        zh = [l for l in lineas.get("zh", []) if l["jp"]]
        if modo == "ja":
            return [dict(l, idi="ja") for l in ja]
        if modo == "zh":
            return [dict(l, idi="zh") for l in zh]
        kana = proporcion_kana([l["t"] for l in ja])
        if modo == "auto_ja":
            cjk = [dict(l, idi="ja") for l in ja]
        elif zh and kana < 0.08:
            # pantalla en chino: las líneas del lector chino (y las japonesas con kana que no choquen)
            self.chino_visto = time.time()
            cjk = [dict(l, idi="zh") for l in zh]
            cjk += [dict(j, idi="ja") for j in ja if proporcion_kana([j["t"]]) >= 0.15
                    and not any(_solapa(j, z) for z in zh)]
        else:
            # línea a línea: con kana es japonés; frases largas sin kana son chino (hay juegos que
            # mezclan las dos cosas). Sin lector chino, la lectura japonesa de los hanzi sirve.
            cjk = []
            for l in ja:
                largo = len(re.sub(r"\s", "", l["t"]))
                if proporcion_kana([l["t"]]) < 0.02 and largo >= 8 and modo == "auto":
                    if "zh" not in self.motores:
                        self.falta_chino = True
                    otra = [z for z in zh if _solapa(l, z)]
                    cjk += [dict(z, idi="zh") for z in otra] if otra else [dict(l, idi="zh")]
                else:
                    cjk.append(dict(l, idi="ja"))
        if modo == "en":
            cjk = []
        latinas = [dict(l, idi="en") for l in lineas.get("en", []) if not any(_solapa(l, j) for j in cjk)]
        if "en" not in lineas:   # solo hay lector japonés: usar también sus líneas latinas
            latinas = [dict(l, idi="en") for l in lineas.get("ja", []) if not l["jp"]]
        return cjk + latinas

    @staticmethod
    def _lineas(resultado, escala, codigo):
        salida = []
        for linea in resultado.lines:
            rects = [w.bounding_rect for w in linea.words]
            if not rects:
                continue
            texto = linea.text.strip()
            es_jp = bool(JP_RE.search(texto))
            if es_jp:
                texto = ESP_ENTRE_JP.sub("", texto)
            else:
                texto = corregir_ingles(re.sub(r"\s+", " ", texto))
            if not texto:
                continue
            salida.append({"x0": min(r.x for r in rects) / escala, "y0": min(r.y for r in rects) / escala,
                           "x1": max(r.x + r.width for r in rects) / escala,
                           "y1": max(r.y + r.height for r in rects) / escala,
                           "t": texto, "jp": es_jp})
        return salida


# ============================================================================
# Respaldo para desarrollo (Linux/Mac): EasyOCR
# ============================================================================
class LectorEasyOCR:
    nombre = "EasyOCR"
    faltan = []
    tiene_japones = True

    def __init__(self, avisar=print):
        import easyocr
        avisar("Cargando lector de texto (EasyOCR)…")
        self.lector = easyocr.Reader(["ja", "en"], gpu=False, verbose=False)
        self.lector_en = easyocr.Reader(["en"], gpu=False, verbose=False)

    def leer(self, imagen, modo="auto", seguir=lambda: True, pista=None):
        import numpy as np
        from nucleo import unir_misma_linea, Cancelado
        arr = np.array(imagen.convert("RGB"))
        lector = self.lector_en if modo == "en" else self.lector
        trozos = []
        for caja, texto, conf in lector.readtext(arr, paragraph=False):
            texto = texto.strip()
            if conf < 0.2 or not texto:
                continue
            xs = [p[0] for p in caja]
            ys = [p[1] for p in caja]
            trozos.append({"x0": min(xs), "y0": min(ys), "x1": max(xs), "y1": max(ys),
                           "t": texto, "jp": bool(JP_RE.search(texto))})
        filas = unir_misma_linea(trozos)
        h_img, w_img = arr.shape[:2]
        for f in filas:
            if not seguir():
                raise Cancelado()
            if f["jp"] and modo != "en":
                f["t"] = ESP_ENTRE_JP.sub("", f["t"])
                continue
            if modo == "en":
                f["t"] = corregir_ingles(f["t"])
                continue
            if modo == "ja":
                continue
            h = f["y1"] - f["y0"]
            caja = [int(max(0, f["x0"] - 0.15 * h)), int(min(w_img, f["x1"] + 1.0 * h)),
                    int(max(0, f["y0"] - 0.3 * h)), int(min(h_img, f["y1"] + 0.3 * h))]
            try:
                r = self.lector_en.recognize(arr, horizontal_list=[caja], free_list=[], detail=0)
                texto = " ".join(r).strip()
                if texto:
                    f["t"], f["jp"] = corregir_ingles(texto), False
            except Exception:
                log.exception("Relectura en inglés")
        return filas
