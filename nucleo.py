# -*- coding: utf-8 -*-
"""
Núcleo del Traductor de Pantalla: configuración, glosario, OCR + traducción,
dibujo de la superposición y lectura en voz alta.  (Sin interfaz gráfica.)
"""
import copy
import functools
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time

from PIL import Image, ImageDraw, ImageFont

if getattr(sys, "frozen", False):          # versión .exe: los datos van junto al .exe
    BASE = os.path.dirname(sys.executable)
else:
    BASE = os.path.dirname(os.path.abspath(__file__))

# Si junto al programa hay una carpeta "modelos" (versión .exe), usar esos modelos:
# así funciona sin internet desde el primer momento.
RUTA_MODELOS = os.path.join(BASE, "modelos")
if os.path.isdir(RUTA_MODELOS):
    os.environ["ARGOS_PACKAGES_DIR"] = os.path.join(RUTA_MODELOS, "argos")
    os.environ["EASYOCR_MODULE_PATH"] = os.path.join(RUTA_MODELOS, "easyocr")
RUTA_CONFIG = os.path.join(BASE, "config.json")
RUTA_GLOSARIO = os.path.join(BASE, "glosario.json")
RUTA_REGISTRO = os.path.join(BASE, "registro.txt")
RUTA_HISTORIAL = os.path.join(BASE, "historial.json")

log = logging.getLogger("traductor")


def iniciar_registro():
    """Guarda los errores en registro.txt (útil porque en Windows no hay consola)."""
    try:
        if os.path.exists(RUTA_REGISTRO) and os.path.getsize(RUTA_REGISTRO) > 1_000_000:
            os.remove(RUTA_REGISTRO)
    except OSError:
        pass
    logging.basicConfig(filename=RUTA_REGISTRO, level=logging.INFO, encoding="utf-8",
                        format="%(asctime)s %(levelname)s %(message)s")
    # Con pythonw no existen stdout/stderr; algunas librerías fallan si no los hay.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(RUTA_REGISTRO, "a", encoding="utf-8")


# ============================================================================
# CONFIGURACIÓN
# ============================================================================
ESQUEMAS = {
    # clave: (nombre visible, color de fondo, color de texto)
    "blanco_negro": ("Blanco sobre negro", (0, 0, 0), (255, 255, 255)),
    "amarillo_negro": ("Amarillo sobre negro (alto contraste)", (0, 0, 0), (255, 225, 0)),
    "negro_blanco": ("Negro sobre blanco", (255, 255, 255), (0, 0, 0)),
    "blanco_azul": ("Blanco sobre azul oscuro", (12, 28, 72), (255, 255, 255)),
    "verde_negro": ("Verde claro sobre negro", (0, 0, 0), (130, 255, 150)),
    "vino_hueso": ("Hueso sobre rojo vino", (74, 8, 22), (245, 238, 220)),
}

# nombre visible: (archivos normales, archivos negrita, familia para Tk)
FUENTES = {
    "Arial": (["arial.ttf", "Arial.ttf"], ["arialbd.ttf", "Arial Bold.ttf"], "Arial"),
    "Verdana (muy legible)": (["verdana.ttf", "Verdana.ttf"], ["verdanab.ttf", "Verdana Bold.ttf"], "Verdana"),
    "Segoe UI": (["segoeui.ttf"], ["segoeuib.ttf"], "Segoe UI"),
    "Tahoma": (["tahoma.ttf", "Tahoma.ttf"], ["tahomabd.ttf", "Tahoma Bold.ttf"], "Tahoma"),
    "Comic Sans (fácil de leer)": (["comic.ttf", "Comic Sans MS.ttf"], ["comicbd.ttf", "Comic Sans MS Bold.ttf"], "Comic Sans MS"),
    "OpenDyslexic (si la tienes instalada)": (["OpenDyslexic-Regular.otf", "OpenDyslexic3-Regular.ttf"],
                                              ["OpenDyslexic-Bold.otf", "OpenDyslexic3-Bold.ttf"], "OpenDyslexic"),
}
_RESPALDO_NORMAL = ["arial.ttf", "Arial.ttf", "segoeui.ttf", "Helvetica.ttc",
                    "DejaVuSans.ttf", "LiberationSans-Regular.ttf"]
_RESPALDO_NEGRITA = ["arialbd.ttf", "Arial Bold.ttf", "segoeuib.ttf",
                     "DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf"]

TAM_INTERFAZ = {"Normal": 10, "Grande": 13, "Muy grande": 16}

TECLAS_ATAJO = [chr(c) for c in range(ord("A"), ord("Z") + 1)] + \
               [str(n) for n in range(10)] + [f"F{n}" for n in range(1, 13)]

ACCIONES = {
    # clave: (nombre visible, atajo por defecto)
    "pantalla": ("Traducir pantalla completa", {"ctrl": True, "alt": True, "shift": False, "tecla": "T"}),
    "zona": ("Traducir una zona", {"ctrl": True, "alt": True, "shift": False, "tecla": "Z"}),
    "repetir": ("Repetir la última zona", {"ctrl": True, "alt": True, "shift": False, "tecla": "R"}),
    "portapapeles": ("Traducir lo copiado", {"ctrl": True, "alt": True, "shift": False, "tecla": "V"}),
    "detener": ("Detener / cerrar todo", {"ctrl": True, "alt": True, "shift": False, "tecla": "X"}),
}

DEFAULTS = {
    "idioma_origen": "auto",          # auto | en | ja
    "modo_vista": "superpuesto",      # superpuesto | panel
    "omitir_espanol": True,
    "siempre_visible": True,
    "cerrar_auto_seg": 0,             # 0 = no cerrar solo
    "mostrar_original_panel": True,
    "esquema": "blanco_negro",
    "opacidad": 90,                   # %
    "fuente": "Arial",
    "negrita": False,
    "escala_texto": 100,              # %
    "letra_minima": 12,               # px
    "tam_interfaz": "Normal",
    "tam_panel": 14,
    "atajos_activos": True,
    "atajos": {k: v[1] for k, v in ACCIONES.items()},
    "voz_auto": False,
    "voz_velocidad": 0,               # -5 … 5
    "guardar_historial": True,        # conservar el historial al cerrar
    "historial_max": 300,             # entradas como máximo
}


def cargar_config():
    cfg = copy.deepcopy(DEFAULTS)
    try:
        with open(RUTA_CONFIG, encoding="utf-8") as f:
            guardada = json.load(f)
        for k, v in guardada.items():
            if k == "atajos" and isinstance(v, dict):
                for a, at in v.items():
                    if a in cfg["atajos"] and isinstance(at, dict):
                        cfg["atajos"][a].update(at)
            elif k in cfg:
                cfg[k] = v
    except FileNotFoundError:
        pass
    except Exception as e:
        log.warning("config.json ilegible, uso valores por defecto: %s", e)
    return cfg


def guardar_config(cfg):
    tmp = RUTA_CONFIG + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, RUTA_CONFIG)


def atajo_legible(at):
    partes = [n for n, k in (("Ctrl", "ctrl"), ("Alt", "alt"), ("Mayús", "shift")) if at.get(k)]
    return "+".join(partes + [at["tecla"]])


def atajo_pynput(at):
    partes = [f"<{k}>" for k in ("ctrl", "alt", "shift") if at.get(k)]
    t = at["tecla"]
    partes.append(f"<{t.lower()}>" if t.startswith("F") and len(t) > 1 else t.lower())
    return "+".join(partes)


# ============================================================================
# GLOSARIO
# ============================================================================
def leer_glosario():
    datos = {"en": {}, "ja": {}}
    try:
        with open(RUTA_GLOSARIO, encoding="utf-8") as f:
            crudo = json.load(f)
        for idioma in ("en", "ja"):
            datos[idioma] = dict(crudo.get(idioma, {}))
        if "_instrucciones" in crudo:
            datos["_instrucciones"] = crudo["_instrucciones"]
    except FileNotFoundError:
        pass
    return datos


def guardar_glosario(datos):
    tmp = RUTA_GLOSARIO + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(datos, f, ensure_ascii=False, indent=2)
    os.replace(tmp, RUTA_GLOSARIO)


# ============================================================================
# HISTORIAL DE TRADUCCIONES
# ============================================================================
class Historial:
    """Lista de lo traducido (lo más reciente primero). Si una frase se repite
    (por ejemplo al repetir una zona con subtítulos) sube arriba en vez de duplicarse."""
    FUENTES = {"pantalla": "Pantalla", "zona": "Zona", "repetir": "Zona", "portapapeles": "Copiado"}

    def __init__(self, cfg):
        self.cfg = cfg
        self.entradas = []
        self.al_cambiar = None
        if cfg.get("guardar_historial", True):
            try:
                with open(RUTA_HISTORIAL, encoding="utf-8") as f:
                    self.entradas = [e for e in json.load(f) if isinstance(e, dict) and "traduccion" in e]
            except FileNotFoundError:
                pass
            except Exception:
                log.exception("historial.json ilegible")

    def agregar(self, bloques, fuente):
        if not bloques:
            return
        fecha = time.strftime("%Y-%m-%d %H:%M:%S")
        claves = {(b["idioma"], b["original"].strip()) for b in bloques}
        self.entradas = [e for e in self.entradas if (e["idioma"], e["original"].strip()) not in claves]
        nuevas = [{"fecha": fecha, "idioma": b["idioma"], "original": b["original"],
                   "traduccion": b["traduccion"], "fuente": fuente} for b in bloques]
        self.entradas = (nuevas + self.entradas)[: max(10, int(self.cfg.get("historial_max", 300)))]
        self._cambio()

    def borrar(self, indices):
        quitar = set(indices)
        self.entradas = [e for i, e in enumerate(self.entradas) if i not in quitar]
        self._cambio()

    def vaciar(self):
        self.entradas = []
        self._cambio()

    def _cambio(self):
        self.guardar()
        if self.al_cambiar:
            try:
                self.al_cambiar()
            except Exception:
                log.exception("Actualizando historial")

    def guardar(self):
        try:
            if not self.cfg.get("guardar_historial", True):
                if os.path.exists(RUTA_HISTORIAL):
                    os.remove(RUTA_HISTORIAL)
                return
            tmp = RUTA_HISTORIAL + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.entradas, f, ensure_ascii=False, indent=1)
            os.replace(tmp, RUTA_HISTORIAL)
        except Exception:
            log.exception("No se pudo guardar el historial")


# ============================================================================
# DETECCIÓN DE IDIOMA
# ============================================================================
JP_RE = re.compile(r"[぀-ヿ㐀-䶿一-鿿ｦ-ﾟ]")
LETRA_RE = re.compile(r"[A-Za-z぀-ヿ㐀-䶿一-鿿ｦ-ﾟ]")
ESP_ENTRE_JP = re.compile(
    r"(?<=[　-ヿ㐀-䶿一-鿿＀-￯])\s+"
    r"(?=[　-ヿ㐀-䶿一-鿿＀-￯])")
_PAL_ES = set("el la los las de del que y en un una por para con es son se no al lo su sus "
              "como más pero este esta está están ya muy también hay fue ser han archivo "
              "inicio buscar abrir guardar cerrar ver ayuda nuevo nueva configuración".split())
_PAL_EN = set("the of and to in is it you that for on with are this be at by not or from "
              "your have an was can will all do my we they".split())


def detectar_idioma(texto):
    return "ja" if JP_RE.search(texto) else "en"


def parece_espanol(texto):
    """Evita 'traducir' lo que ya está en español (menús de Windows, etc.)."""
    if JP_RE.search(texto):
        return False
    if re.search(r"[ñÑ¿¡áéíóúÁÉÍÓÚ]", texto):
        return True
    palabras = re.findall(r"[a-záéíóúñü]+", texto.lower())
    es = sum(p in _PAL_ES for p in palabras)
    en = sum(p in _PAL_EN for p in palabras)
    return es > 0 and es > en


# ============================================================================
# MOTOR: OCR + GLOSARIO + TRADUCCIÓN
# ============================================================================
class Cancelado(Exception):
    pass


class SeparadorSimple:
    """Separa frases sin modelos ni internet.

    Argos Translate usa por defecto separadores (stanza/minisbd) que intentan
    conectarse a internet al arrancar; para textos de pantalla (frases cortas)
    basta con cortar en los signos de final de frase."""
    _RE = re.compile(r"(?<=[.!?。！？])\s*")

    def split_sentences(self, texto):
        partes = [p for p in self._RE.split(texto) if p.strip()]
        return partes or [texto]


def usar_separador_simple(traduccion):
    if hasattr(traduccion, "sentencizer"):
        traduccion.sentencizer = SeparadorSimple()
    for sub in ("t1", "t2"):            # traducciones encadenadas (ja -> en -> es)
        if hasattr(traduccion, sub):
            usar_separador_simple(getattr(traduccion, sub))


class Motor:
    def __init__(self, avisar=print):
        self.avisar = avisar
        self.lector = None
        self.lector_en = None
        self.traductores = {}
        self.glosario = {}
        self.cache = {}
        self._lock = threading.Lock()

    def cargar(self):
        self.cargar_glosario()
        self.avisar("Cargando diccionarios de traducción…")
        self._cargar_traductores()
        self.avisar("Cargando lector de texto (OCR)… puede tardar un poco")
        import easyocr
        self.lector = easyocr.Reader(["ja", "en"], gpu=False, verbose=False)
        self.lector_en = easyocr.Reader(["en"], gpu=False, verbose=False)
        self.avisar("Calentando motores…")
        for idioma, muestra in (("en", "Hello"), ("ja", "こんにちは")):
            if idioma in self.traductores:
                try:
                    self.traductores[idioma].translate(muestra)
                except Exception:
                    log.exception("Fallo al calentar %s", idioma)

    def cargar_glosario(self):
        nuevo = {}
        try:
            datos = leer_glosario()
            for idioma in ("en", "ja"):
                for k, v in datos.get(idioma, {}).items():
                    nuevo[(idioma, self._norm(k, idioma))] = v
        except Exception as e:
            self.avisar(f"Aviso: el glosario tiene un error ({e})")
            log.exception("Glosario")
        self.glosario = nuevo
        self.cache.clear()

    @staticmethod
    def _norm(t, idioma):
        t = t.strip()
        if idioma == "en":
            return re.sub(r"\s+", " ", t).lower().strip(" .,:;!?\"'")
        return re.sub(r"\s+", "", t).strip("。、！？!?「」")

    def _cargar_traductores(self):
        from argostranslate import package, translate
        instalados = {(p.from_code, p.to_code) for p in package.get_installed_packages()}
        if ("en", "es") not in instalados or not ({("ja", "en"), ("ja", "es")} & instalados):
            self.avisar("Descargando idiomas por primera vez (necesita internet)…")
            import descargar_idiomas
            descargar_idiomas.descargar_traductores(self.avisar)
        langs = {l.code: l for l in translate.get_installed_languages()}
        es = langs.get("es")
        for origen in ("en", "ja"):
            if origen in langs and es is not None:
                tr = langs[origen].get_translation(es)   # ja->es pasa por inglés
                if tr is not None:
                    usar_separador_simple(tr)
                    self.traductores[origen] = tr
        if not self.traductores:
            raise RuntimeError("No hay idiomas instalados. Ejecuta descargar_idiomas.py")

    def traducir(self, texto, idioma):
        clave = (idioma, self._norm(texto, idioma))
        if clave in self.glosario:                 # 1) tu glosario personal
            return self.glosario[clave]
        if clave in self.cache:
            return self.cache[clave]
        tr = self.traductores.get(idioma)          # 2) diccionario/modelo offline
        if tr is None:
            return texto
        try:
            res = tr.translate(texto)
        except Exception:
            log.exception("Error traduciendo %r", texto)
            res = texto
        self.cache[clave] = res
        return res

    def procesar(self, imagen, modo="auto", omitir_espanol=True, seguir=lambda: True):
        """Devuelve [{'caja':(x0,y0,x1,y1), 'original':str, 'traduccion':str, 'idioma':str}]."""
        import numpy as np
        with self._lock:
            if not seguir():
                raise Cancelado()
            arr = np.array(imagen.convert("RGB"))
            filas = self._leer_filas(arr, modo, seguir)
            bloques = []
            for par in agrupar_parrafos(filas):
                if not seguir():
                    raise Cancelado()
                texto, idioma = par["t"], ("ja" if par["jp"] else "en")
                if len(texto) < 2 or not LETRA_RE.search(texto):
                    continue
                if (modo == "en" and idioma == "ja") or (modo == "ja" and idioma == "en"):
                    continue
                if idioma == "en" and omitir_espanol and parece_espanol(texto):
                    continue
                trad = self.traducir(texto, idioma).strip()
                if not trad or trad.lower() == texto.lower():
                    continue        # nada que traducir (números, nombres propios…)
                bloques.append({"caja": (par["x0"], par["y0"], par["x1"], par["y1"]),
                                "original": texto, "traduccion": trad, "idioma": idioma})
            return bloques

    MAX_COPIADO = 6000   # caracteres: evita congelar el equipo con textos enormes

    def traducir_texto(self, texto, modo="auto", omitir_espanol=True, seguir=lambda: True):
        """Traduce texto copiado (sin OCR). Cada línea/párrafo detecta su idioma por separado."""
        texto = texto.replace("\r\n", "\n").replace("\r", "\n")[: self.MAX_COPIADO]
        bloques = []
        with self._lock:
            for linea in texto.split("\n"):
                if not seguir():
                    raise Cancelado()
                linea = linea.strip()
                if len(linea) < 2 or not LETRA_RE.search(linea):
                    continue
                es_jp = bool(JP_RE.search(linea))
                if (modo == "en" and es_jp) or (modo == "ja" and not es_jp):
                    continue
                if not es_jp and omitir_espanol and parece_espanol(linea):
                    continue
                idioma = "ja" if es_jp else "en"
                trad = self.traducir(linea, idioma).strip()
                if trad and trad.lower() != linea.lower():
                    bloques.append({"caja": None, "original": linea, "traduccion": trad,
                                    "idioma": idioma})
        return bloques

    def _leer_filas(self, arr, modo, seguir):
        """OCR por líneas + unión de trozos de la misma línea + relectura del inglés."""
        trozos = []
        # Con el idioma fijado en inglés basta un solo lector (más rápido)
        lector = self.lector_en if modo == "en" else self.lector
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
            if modo == "en":                 # ya lo leyó el lector de inglés
                f["t"] = corregir_ingles(f["t"])
                continue
            if modo == "ja":                 # en modo japonés el inglés se descarta
                continue
            # Releer con el modelo de inglés (más preciso). Se deja margen para no perder
            # la puntuación final, sin invadir los renglones vecinos.
            h = f["y1"] - f["y0"]
            arriba, abajo = 0.3 * h, 0.3 * h
            for o in filas:
                if o is f or min(o["x1"], f["x1"]) <= max(o["x0"], f["x0"]):
                    continue
                if o["y1"] <= f["y0"]:
                    arriba = min(arriba, (f["y0"] - o["y1"]) / 2)
                elif o["y0"] >= f["y1"]:
                    abajo = min(abajo, (o["y0"] - f["y1"]) / 2)
            derecha = 1.0 * h
            for o in filas:
                if o is not f and o["x0"] >= f["x1"] and min(o["y1"], f["y1"]) > max(o["y0"], f["y0"]):
                    derecha = min(derecha, (o["x0"] - f["x1"]) * 0.8)
            caja = [int(max(0, f["x0"] - 0.15 * h)), int(min(w_img, f["x1"] + derecha)),
                    int(max(0, f["y0"] - arriba)), int(min(h_img, f["y1"] + abajo))]
            try:
                r = self.lector_en.recognize(arr, horizontal_list=[caja], free_list=[], detail=0)
                texto = " ".join(r).strip()
                if texto:
                    f["t"], f["jp"] = corregir_ingles(texto), False
            except Exception:
                log.exception("Relectura en inglés")
        return filas


def corregir_ingles(texto):
    """Errores típicos del OCR: el punto final se lee como ':' en frases largas."""
    if texto[-1:] in (":", ";") and len(texto.split()) >= 4:
        texto = texto[:-1] + "."
    texto = re.sub(r"\s+([,.!?;:])", r"\1", texto)
    # "Was", "The"… en mitad de una frase suelen ser errores de mayúscula del OCR
    palabras = texto.split(" ")
    for i in range(1, len(palabras)):
        p = palabras[i]
        if (p[:1].isupper() and p.lower() in _PAL_EN | {"was", "were", "a", "once", "has", "had"}
                and not palabras[i - 1].endswith((".", "!", "?", ":", '"'))):
            palabras[i] = p.lower()
    return " ".join(palabras)


def unir_misma_linea(trozos):
    """Une los pedazos que el detector separó dentro de una misma línea."""
    filas = []
    for t in sorted(trozos, key=lambda t: t["x0"]):
        h = t["y1"] - t["y0"]
        for f in filas:
            hf = f["y1"] - f["y0"]
            solape = min(f["y1"], t["y1"]) - max(f["y0"], t["y0"])
            hueco = t["x0"] - f["x1"]
            if (f["jp"] == t["jp"] and solape > 0.5 * min(h, hf)
                    and -0.5 * max(h, hf) < hueco < 1.0 * max(h, hf)):
                f["t"] += ("" if t["jp"] else " ") + t["t"]
                f["x1"] = max(f["x1"], t["x1"])
                f["y0"], f["y1"] = min(f["y0"], t["y0"]), max(f["y1"], t["y1"])
                break
        else:
            filas.append(dict(t))
    return filas


_FIN_FRASE = tuple(".!?:;。！？」』…)")


def agrupar_parrafos(filas):
    """Junta renglones de un mismo párrafo (texto que continúa), sin juntar menús."""
    parrafos = []
    for f in sorted(filas, key=lambda f: (f["y0"], f["x0"])):
        h = f["y1"] - f["y0"]
        for p in parrafos:
            u = p["ultima"]
            hu = u["y1"] - u["y0"]
            larga = (len(u["t"]) >= 10) if u["jp"] else (len(u["t"].split()) >= 4)
            if (p["jp"] == f["jp"] and larga and not u["t"].rstrip().endswith(_FIN_FRASE)
                    and abs(f["x0"] - u["x0"]) < 1.0 * max(h, hu)
                    and -0.2 * h <= f["y0"] - u["y1"] < 0.6 * max(h, hu)
                    and abs(h - hu) < 0.35 * max(h, hu)):
                if f["jp"]:
                    p["t"] += f["t"]
                elif p["t"].endswith("-"):
                    p["t"] = p["t"][:-1] + f["t"]
                else:
                    p["t"] += " " + f["t"]
                p["x0"], p["x1"] = min(p["x0"], f["x0"]), max(p["x1"], f["x1"])
                p["y1"] = max(p["y1"], f["y1"])
                p["ultima"] = f
                break
        else:
            parrafos.append({"x0": f["x0"], "y0": f["y0"], "x1": f["x1"], "y1": f["y1"],
                             "t": f["t"], "jp": f["jp"], "ultima": f})
    return parrafos


# ============================================================================
# DIBUJO DE LA SUPERPOSICIÓN
# ============================================================================
@functools.lru_cache(maxsize=256)
def cargar_fuente(nombre, tam, negrita=False):
    normales, negritas, _ = FUENTES.get(nombre, FUENTES["Arial"])
    lista = (negritas + _RESPALDO_NEGRITA if negrita else []) + normales + _RESPALDO_NORMAL
    for archivo in lista:
        try:
            return ImageFont.truetype(archivo, tam)
        except Exception:
            continue
    try:
        return ImageFont.load_default(size=tam)
    except TypeError:
        return ImageFont.load_default()


_CJK = ["YuGothM.ttc", "meiryo.ttc", "msgothic.ttc", "Hiragino Sans GB.ttc",
        "ヒラギノ角ゴシック W3.ttc", "NotoSansCJK-Regular.ttc", "NotoSansJP-Regular.otf"]


@functools.lru_cache(maxsize=64)
def cargar_fuente_cjk(tam):
    for archivo in _CJK:
        try:
            return ImageFont.truetype(archivo, tam)
        except Exception:
            continue
    return None


def _envolver(texto, fuente, ancho, draw):
    palabras, lineas, actual = texto.split(), [], ""
    for p in palabras:
        prueba = (actual + " " + p).strip()
        if draw.textlength(prueba, font=fuente) <= ancho or not actual:
            actual = prueba
        else:
            lineas.append(actual)
            actual = p
    if actual:
        lineas.append(actual)
    return lineas or [""]


def componer(imagen, bloques, cfg):
    """Dibuja los recuadros con la traducción encima de la captura."""
    _, fondo, color_txt = ESQUEMAS.get(cfg["esquema"], ESQUEMAS["blanco_negro"])
    alfa = int(255 * max(30, min(100, cfg["opacidad"])) / 100)
    escala = max(50, cfg["escala_texto"]) / 100
    minimo = max(8, int(cfg["letra_minima"]))
    base = imagen.convert("RGBA")
    capa = Image.new("RGBA", base.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(capa)
    W, H = base.size
    colocados = []
    for b in sorted(bloques, key=lambda b: (b["caja"][1], b["caja"][0])):
        x0, y0, x1, y1 = b["caja"]
        texto = b["traduccion"]
        ancho = max(40, x1 - x0) * max(1.0, escala)
        alto = max(12, y1 - y0)
        tam = max(minimo, int(min(alto * 0.8, 48) * escala))
        while True:
            fuente = cargar_fuente(cfg["fuente"], tam, cfg["negrita"])
            if JP_RE.search(texto):   # si quedó algo en japonés, usar una fuente que lo tenga
                fuente = cargar_fuente_cjk(tam) or fuente
            lineas = _envolver(texto, fuente, ancho, d)
            alto_linea = fuente.getbbox("ÁgjÑ")[3] + 2
            total = alto_linea * len(lineas)
            if total <= alto * max(1.0, escala) or tam <= minimo:
                break
            tam -= 1
        alto_final = max(alto, total)
        ancho_final = max(x1 - x0, max(d.textlength(ln, font=fuente) for ln in lineas))
        if x0 + ancho_final > W - 4:
            x0 = max(4, W - ancho_final - 8)
        rect = [x0 - 5, y0 - 3, x0 + ancho_final + 5, y0 + alto_final + 3]
        # si choca con un recuadro ya dibujado, bajarlo
        for _ in range(30):
            choque = next((c for c in colocados if rect[0] < c[2] and rect[2] > c[0]
                           and rect[1] < c[3] and rect[3] > c[1]), None)
            if not choque:
                break
            dy = choque[3] + 2 - rect[1]
            rect[1] += dy
            rect[3] += dy
        colocados.append(rect)
        d.rounded_rectangle(rect, radius=5, fill=fondo + (alfa,))
        y = rect[1] + 3 + (alto_final - total) / 2
        for ln in lineas:
            d.text((rect[0] + 5, y), ln, font=fuente, fill=color_txt + (255,))
            y += alto_linea
    return Image.alpha_composite(base, capa)


# ============================================================================
# VOZ (lectura en voz alta, sin internet, sin librerías extra)
# ============================================================================
class Voz:
    def __init__(self):
        self.proc = None
        self._tmp = None
        self._voz_mac = None

    def disponible(self):
        if sys.platform.startswith("win"):
            return shutil.which("powershell") is not None
        if sys.platform == "darwin":
            return shutil.which("say") is not None
        return any(shutil.which(p) for p in ("espeak-ng", "espeak", "spd-say"))

    def hablando(self):
        return self.proc is not None and self.proc.poll() is None

    def decir(self, texto, velocidad=0):
        self.detener()
        texto = (texto or "").strip()
        if not texto or not self.disponible():
            return False
        fd, ruta = tempfile.mkstemp(suffix=".txt")
        with os.fdopen(fd, "w", encoding="utf-8-sig") as f:
            f.write(texto)
        self._tmp = ruta
        kw = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "stdin": subprocess.DEVNULL}
        try:
            if sys.platform.startswith("win"):
                r = ruta.replace("'", "''")
                script = (
                    "Add-Type -AssemblyName System.Speech;"
                    "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
                    "$v=$s.GetInstalledVoices()|Where-Object{$_.VoiceInfo.Culture.TwoLetterISOLanguageName -eq 'es'}|Select-Object -First 1;"
                    "if($v){$s.SelectVoice($v.VoiceInfo.Name)};"
                    f"$s.Rate={int(velocidad) * 2};"
                    f"$s.Speak((Get-Content -Raw -Encoding UTF8 '{r}'))")
                kw["creationflags"] = 0x08000000  # sin ventana de consola
                cmd = ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                       "-Command", script]
            elif sys.platform == "darwin":
                cmd = ["say", "-r", str(175 + 25 * int(velocidad)), "-f", ruta]
                voz = self._buscar_voz_mac()
                if voz:
                    cmd[1:1] = ["-v", voz]
            else:
                prog = next(p for p in ("espeak-ng", "espeak", "spd-say") if shutil.which(p))
                if prog == "spd-say":
                    cmd = ["spd-say", "-w", "-l", "es", "-r", str(int(velocidad) * 15), texto]
                else:
                    cmd = [prog, "-v", "es", "-s", str(170 + 20 * int(velocidad)), "-f", ruta]
            self.proc = subprocess.Popen(cmd, **kw)
            return True
        except Exception:
            log.exception("No se pudo leer en voz alta")
            return False

    def _buscar_voz_mac(self):
        if self._voz_mac is None:
            self._voz_mac = ""
            try:
                salida = subprocess.run(["say", "-v", "?"], capture_output=True, text=True).stdout
                for linea in salida.splitlines():
                    if re.search(r"\bes[_-](MX|ES|US|CO|419)", linea):
                        self._voz_mac = re.split(r"\s{2,}", linea.strip())[0]
                        break
            except Exception:
                pass
        return self._voz_mac

    def detener(self):
        if self.hablando():
            try:
                self.proc.terminate()
            except Exception:
                pass
        self.proc = None
        if self._tmp:
            try:
                os.remove(self._tmp)
            except OSError:
                pass
            self._tmp = None
