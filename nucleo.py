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
RUTA_CONFIG = os.path.join(BASE, "config.json")
RUTA_GLOSARIO = os.path.join(BASE, "glosario.json")
RUTA_REGISTRO = os.path.join(BASE, "registro.txt")
RUTA_HISTORIAL = os.path.join(BASE, "historial.json")
RUTA_MEMORIA = os.path.join(BASE, "memoria.sqlite")
RUTA_PERFILES = os.path.join(BASE, "perfiles.json")

log = logging.getLogger("traductor")


SIGLA_RE = re.compile(r"^(?=.*[A-Z])[A-Z0-9+★☆]{1,5}$")


class _RegistroLimitado(logging.FileHandler):
    """registro.txt que nunca crece sin límite: al pasar de 2 MB pasa a registro_anterior.txt."""
    LIMITE = 2_000_000

    def emit(self, record):
        try:
            if self.stream is not None and self.stream.tell() > self.LIMITE:
                self.close()
                anterior = os.path.join(os.path.dirname(self.baseFilename), "registro_anterior.txt")
                try:
                    os.replace(self.baseFilename, anterior)
                except OSError:
                    open(self.baseFilename, "w").close()
        except Exception:
            pass
        super().emit(record)


class _ConsolaAlRegistro:
    """stderr de la versión .exe (que no tiene consola): cada línea va al registro."""

    def __init__(self):
        self._buf = ""

    def write(self, texto):
        self._buf += texto
        while "\n" in self._buf:
            linea, self._buf = self._buf.split("\n", 1)
            if linea.strip():
                log.warning("[consola] %s", linea)
        return len(texto)

    def flush(self):
        pass

    def isatty(self):
        return False


_repetidos = {}


def log_limitado(clave, mensaje):
    """Para errores dentro de bucles (cada 60-150 ms): el primero con todos los detalles y
    luego, como mucho, un aviso por minuto con cuántas veces se repitió."""
    ahora = time.time()
    n, desde = _repetidos.get(clave, (0, 0.0))
    if n == 0 or ahora - desde > 60:
        log.exception(mensaje if n <= 1 else f"{mensaje} (se repitió {n} veces en el último minuto)")
        _repetidos[clave] = (1, ahora)
    else:
        _repetidos[clave] = (n + 1, desde)


def iniciar_registro():
    """Guarda los errores en registro.txt (útil porque en Windows no hay consola)."""
    try:
        if os.path.exists(RUTA_REGISTRO) and os.path.getsize(RUTA_REGISTRO) > 1_000_000:
            os.replace(RUTA_REGISTRO, os.path.join(BASE, "registro_anterior.txt"))
    except OSError:
        pass
    manejador = _RegistroLimitado(RUTA_REGISTRO, encoding="utf-8", delay=True)
    manejador.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    raiz = logging.getLogger()
    raiz.addHandler(manejador)
    raiz.setLevel(logging.INFO)
    logging.raiseExceptions = False
    # Con pythonw no existen stdout/stderr; algunas librerías fallan si no los hay.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = _ConsolaAlRegistro()

    def hilo_fallido(args):
        if args.exc_type is not SystemExit:
            log.error("Error en el hilo %s", getattr(args.thread, "name", "?"),
                      exc_info=(args.exc_type, args.exc_value, args.exc_traceback))
    threading.excepthook = hilo_fallido


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
               [str(n) for n in range(10)] + [f"F{n}" for n in range(1, 13)] + ["Espacio"]

ACCIONES = {
    # clave: (nombre visible, atajo por defecto)
    "pantalla": ("Traducir pantalla completa", {"ctrl": True, "alt": True, "shift": False, "tecla": "T"}),
    "zona": ("Traducir una zona", {"ctrl": True, "alt": True, "shift": False, "tecla": "Z"}),
    "anclar": ("Traducir un juego o programa", {"ctrl": True, "alt": True, "shift": False, "tecla": "W"}),
    "subtitulos": ("Subtítulos de pantalla (zona)", {"ctrl": True, "alt": True, "shift": False, "tecla": "S"}),
    "voz": ("Subtítulos de voz (audio)", {"ctrl": True, "alt": True, "shift": False, "tecla": "A"}),
    "repetir": ("Repetir la última zona", {"ctrl": True, "alt": True, "shift": False, "tecla": "R"}),
    "portapapeles": ("Traducir lo copiado", {"ctrl": True, "alt": True, "shift": False, "tecla": "V"}),
    "detener": ("Detener / cerrar todo", {"ctrl": True, "alt": True, "shift": False, "tecla": "X"}),
    "menu": ("Menú rápido junto al cursor", {"ctrl": True, "alt": True, "shift": False, "tecla": "Espacio"}),
    "original": ("Ver el original (mantener pulsada)", {"ctrl": False, "alt": False, "shift": False, "tecla": "F8"}),
}
ACCIONES_MANTENER = {"original"}      # se vigilan mientras están pulsadas, no al pulsarlas
# Acciones que se ofrecen: en Windows el camino es anclar el traductor al juego (el perfil
# recuerda la zona); en otros sistemas no hay modo en vivo y se traduce una zona o la pantalla.
ACCIONES_VIVO = ["anclar", "subtitulos", "voz", "portapapeles", "detener", "menu", "original"]
ACCIONES_FIJO = ["zona", "pantalla", "subtitulos", "voz", "portapapeles", "detener"]

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
    "idioma_destino": "es",           # a qué idioma se traduce
    "motor_traduccion": "auto",       # auto | en_linea | sin_internet
    "modelo_voz": "base",             # base (rápido) | small (más preciso)
    "tam_subtitulo": 30,              # px
    "subtitulo_original": True,       # mostrar también el texto original pequeño
    "guardar_historial": True,        # conservar el historial al cerrar
    "historial_max": 300,             # entradas como máximo
    "memoria": True,                  # recordar traducciones entre sesiones
    "perfiles_auto": True,            # traducir solo al abrir un juego con perfil
    "bandeja": True,                  # icono junto al reloj
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
    if t == "Espacio":
        partes.append("<space>")
    else:
        partes.append(f"<{t.lower()}>" if t.startswith("F") and len(t) > 1 else t.lower())
    return "+".join(partes)


# ============================================================================
# PERFILES POR JUEGO (zona, modo y fuente de cada programa)
# ============================================================================
def clave_perfil(exe):
    return os.path.normcase(os.path.abspath(exe)) if exe else None


def cargar_perfiles():
    try:
        with open(RUTA_PERFILES, encoding="utf-8") as f:
            datos = json.load(f)
        return datos if isinstance(datos, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as e:
        log.warning("perfiles.json ilegible: %s", e)
        return {}


def guardar_perfiles(perfiles):
    try:
        tmp = RUTA_PERFILES + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(perfiles, f, ensure_ascii=False, indent=2)
        os.replace(tmp, RUTA_PERFILES)
    except Exception:
        log.exception("No se pudo guardar perfiles.json")


# ============================================================================
# MEMORIA DE TRADUCCIONES (entre sesiones) Y CORRECCIONES DEL USUARIO
# ============================================================================
class Memoria:
    """Lo traducido se guarda en memoria.sqlite: lo repetido sale al instante (incluso sin
    internet) y lo que corrijas a mano manda siempre sobre la traducción automática."""
    MAXIMO = 60000

    def __init__(self, ruta=None):
        import sqlite3
        self._lock = threading.Lock()
        self.db = None
        try:
            self.db = sqlite3.connect(ruta or RUTA_MEMORIA, check_same_thread=False,
                                      isolation_level=None, timeout=3)
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=NORMAL")
            self.db.execute("CREATE TABLE IF NOT EXISTS memoria (origen TEXT, destino TEXT, clave TEXT, "
                            "texto TEXT, traduccion TEXT, manual INTEGER DEFAULT 0, usado REAL, "
                            "PRIMARY KEY (origen, destino, clave))")
            self._recortar()
        except Exception:
            log.exception("Memoria de traducciones no disponible")
            self.db = None

    def buscar(self, origen, destino, claves):
        """→ {clave: (traducción, es_corrección)}"""
        if self.db is None or not claves:
            return {}
        res = {}
        with self._lock:
            try:
                for i in range(0, len(claves), 400):
                    trozo = list(claves[i:i + 400])
                    marcas = ",".join("?" * len(trozo))
                    for c, t, m in self.db.execute(
                            f"SELECT clave, traduccion, manual FROM memoria WHERE origen=? AND destino=? "
                            f"AND clave IN ({marcas})", [origen, destino] + trozo):
                        res[c] = (t, bool(m))
                if res:
                    ahora = time.time()
                    self.db.executemany("UPDATE memoria SET usado=? WHERE origen=? AND destino=? AND clave=?",
                                        [(ahora, origen, destino, c) for c in res])
            except Exception:
                log.exception("Leyendo la memoria")
        return res

    def guardar(self, origen, destino, pares, manual=False):
        """pares: [(clave, texto original, traducción)]"""
        if self.db is None or not pares:
            return
        ahora = time.time()
        with self._lock:
            try:
                self.db.executemany(
                    "INSERT INTO memoria (origen, destino, clave, texto, traduccion, manual, usado) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(origen, destino, clave) DO UPDATE SET "
                    "traduccion=excluded.traduccion, texto=excluded.texto, manual=excluded.manual, "
                    "usado=excluded.usado WHERE memoria.manual=0 OR excluded.manual=1",
                    [(origen, destino, c, t, tr, 1 if manual else 0, ahora) for c, t, tr in pares if tr])
            except Exception:
                log.exception("Guardando en la memoria")

    def cuantas(self):
        if self.db is None:
            return (0, 0)
        with self._lock:
            try:
                total, manuales = self.db.execute(
                    "SELECT COUNT(*), COALESCE(SUM(manual), 0) FROM memoria").fetchone()
                return int(total), int(manuales)
            except Exception:
                return (0, 0)

    def vaciar(self, tambien_correcciones=False):
        if self.db is None:
            return
        with self._lock:
            self.db.execute("DELETE FROM memoria" + ("" if tambien_correcciones else " WHERE manual=0"))
            self.db.execute("VACUUM")

    def _recortar(self):
        total = self.db.execute("SELECT COUNT(*) FROM memoria").fetchone()[0]
        if total > self.MAXIMO:
            sobra = total - int(self.MAXIMO * 0.9)
            self.db.execute("DELETE FROM memoria WHERE rowid IN (SELECT rowid FROM memoria WHERE manual=0 "
                            "ORDER BY usado LIMIT ?)", (sobra,))

    def cerrar(self):
        if self.db is not None:
            with self._lock:
                self.db.close()
            self.db = None


# ============================================================================
# GLOSARIO
# ============================================================================
def leer_glosario():
    datos = {"en": {}, "ja": {}, "zh": {}}
    try:
        with open(RUTA_GLOSARIO, encoding="utf-8") as f:
            crudo = json.load(f)
        for idioma in ("en", "ja", "zh"):
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

    def quitar_original(self, original):
        """Quita una frase (p. ej. un subtítulo que luego se completó)."""
        antes = len(self.entradas)
        self.entradas = [e for e in self.entradas if e["original"] != original]
        if len(self.entradas) != antes:
            self._cambio()

    def borrar(self, indices):
        quitar = set(indices)
        self.entradas = [e for i, e in enumerate(self.entradas) if i not in quitar]
        self._cambio()

    def vaciar(self):
        self.entradas = []
        self._cambio()

    def _cambio(self):
        self._programar_guardado()
        if self.al_cambiar:
            try:
                self.al_cambiar()
            except Exception:
                log.exception("Actualizando historial")

    def _programar_guardado(self):
        """Guarda como mucho una vez cada 2 s (en vivo llegan frases muy seguido)."""
        if getattr(self, "_temporizador", None) is None:
            self._temporizador = threading.Timer(2.0, self._guardar_diferido)
            self._temporizador.daemon = True
            self._temporizador.start()

    def _guardar_diferido(self):
        self._temporizador = None
        self.guardar()

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


_KANA_RE = re.compile(r"[\u3040-\u30ff\uff66-\uff9f]")


def detectar_idioma(texto, japones_cerca=False):
    """en / ja / zh. Ideogramas sin kana = chino, salvo que haya japonés alrededor."""
    if not JP_RE.search(texto):
        return "en"
    if _KANA_RE.search(texto) or japones_cerca:
        return "ja"
    return "zh"


def idioma_de(p):
    """Idioma de un renglón o párrafo leído (los lectores antiguos solo marcan 'jp')."""
    return p.get("idi") or ("ja" if p.get("jp") else "en")


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


class Motor:
    """Lee el texto (OCR) y lo traduce por lotes.

    cfg: función que devuelve la configuración actual (o un diccionario)."""
    MAX_COPIADO = 6000   # caracteres: evita congelar el equipo con textos enormes

    def __init__(self, avisar=print, cfg=None):
        self.avisar = avisar
        if cfg is None:
            cfg = cargar_config()
        self.cfg = cfg if callable(cfg) else (lambda c=cfg: c)
        self.lector = None
        self.traductor = None
        self.glosario = {}
        self.cache = {}
        self.tiempos = (0.0, 0.0)          # (lectura, traducción) de la última vez
        self._lock = threading.Lock()
        self.memoria = Memoria()

    def cargar(self):
        import lector as modulo_lector
        import traduccion
        self.cargar_glosario()
        self.avisar("Preparando el lector de texto…")
        self.lector = modulo_lector.crear_lector(self.avisar)
        self.avisar("Preparando la traducción…")
        carpeta = os.environ.get("ARGOS_PACKAGES_DIR") or os.path.join(RUTA_MODELOS, "argos")
        self.traductor = traduccion.Traductor(self.cfg, carpeta)
        threading.Thread(target=self._calentar, daemon=True).start()

    def _calentar(self):
        """Deja listo el diccionario sin internet para que el respaldo responda rápido."""
        destino = self.cfg().get("idioma_destino", "es")
        try:
            if self.traductor.argos.disponible("en", destino):
                self.traductor.argos.lote(["Hello"], "en", destino)
        except Exception:
            log.exception("Calentando el diccionario sin internet")

    def cargar_glosario(self):
        nuevo = {}
        try:
            datos = leer_glosario()
            for idioma in ("en", "ja", "zh"):
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

    # ---------- traducción ----------
    def traducir_lote(self, items, destino=None, contexto=None):
        """items: [(texto, idioma_origen)] -> traducciones en el mismo orden (None si falló).
        contexto: {idioma: frase anterior del diálogo}; las frases nuevas de ese idioma se
        traducen junto con ella (en línea), lo demás como siempre."""
        cfg = self.cfg()
        destino = destino or cfg.get("idioma_destino", "es")
        usar_memoria = cfg.get("memoria", True) and self.memoria is not None
        res = [None] * len(items)
        pendientes = {}
        guardado = {}
        if self.memoria is not None:
            por_idioma = {}
            for texto, idioma in items:
                if idioma != destino:
                    por_idioma.setdefault(idioma, set()).add(self._norm(texto, idioma))
            for idioma, claves in por_idioma.items():
                for c, v in self.memoria.buscar(idioma, destino, sorted(claves)).items():
                    guardado[(idioma, c)] = v
        for i, (texto, idioma) in enumerate(items):
            norm = self._norm(texto, idioma)
            mem = guardado.get((idioma, norm))
            if mem and mem[1]:                                          # 1) tu corrección
                res[i] = mem[0]
            elif destino == "es" and (idioma, norm) in self.glosario:   # 2) tu glosario
                res[i] = self.glosario[(idioma, norm)]
            elif (idioma, destino, norm) in self.cache:                 # 3) ya traducido hoy
                res[i] = self.cache[(idioma, destino, norm)]
            elif mem and usar_memoria:                                  # 4) traducido otro día
                res[i] = self.cache[(idioma, destino, norm)] = mem[0]
            elif idioma == destino:
                res[i] = texto
            else:
                pendientes.setdefault(idioma, []).append((i, texto, (idioma, destino, norm)))
        if len(self.cache) > 5000:
            self.cache.clear()
        errores = []

        def hacer(idioma, lista):
            try:
                previo = (contexto or {}).get(idioma)
                trads = [None] * len(lista)
                if previo and hasattr(self.traductor, "con_contexto"):
                    for k, (_, t, _) in enumerate(lista):
                        if t.strip() != previo.strip():
                            trads[k] = self.traductor.con_contexto(t, previo, idioma, destino)
                faltan = [k for k, tr in enumerate(trads) if not tr]
                if faltan:
                    for k, tr in zip(faltan, self.traductor.lote([lista[k][1] for k in faltan], idioma, destino)):
                        trads[k] = tr
                for (i, _, clave), tr in zip(lista, trads):
                    res[i] = tr
                    self.cache[clave] = tr
                if usar_memoria:
                    self.memoria.guardar(idioma, destino, [(c[2], t, tr) for (_, t, c), tr in zip(lista, trads)
                                                           if tr and tr.strip() and tr.strip() != t.strip()])
            except Exception as e:
                log.exception("Traduciendo %s→%s", idioma, destino)
                errores.append(e)
        hilos = [threading.Thread(target=hacer, args=(k, v)) for k, v in pendientes.items()]
        for h in hilos[1:]:
            h.start()
        if hilos:
            hilos[0].run()            # el primer idioma en este mismo hilo, el resto en paralelo
        for h in hilos[1:]:
            h.join()
        if errores and all(r is None for r in res):
            raise errores[0]
        return res

    def traducir(self, texto, idioma):
        return self.traducir_lote([(texto, idioma)])[0] or texto

    def corregir(self, texto, idioma, traduccion, destino=None):
        """Tu traducción para esta frase: se usará siempre (también en otras sesiones)."""
        destino = destino or self.cfg().get("idioma_destino", "es")
        norm = self._norm(texto, idioma)
        self.cache[(idioma, destino, norm)] = traduccion
        if self.memoria is not None:
            self.memoria.guardar(idioma, destino, [(norm, texto, traduccion)], manual=True)

    def _vale(self, texto, idioma, modo, omitir):
        if len(texto) < 2 or not LETRA_RE.search(texto):
            return False
        if SIGLA_RE.match(texto.strip()):          # SSR, HP, ATK, LV…: se dejan como están
            return False
        if modo in ("en", "ja", "zh") and idioma != modo:
            return False
        destino = self.cfg().get("idioma_destino", "es")
        if idioma == destino:
            return False
        if idioma == "en" and omitir and destino == "es" and parece_espanol(texto):
            return False
        return True

    # ---------- pantalla ----------
    def procesar(self, imagen, modo="auto", omitir_espanol=True, seguir=lambda: True):
        """Devuelve [{'caja':(x0,y0,x1,y1), 'original', 'traduccion', 'idioma'}]."""
        with self._lock:
            if not seguir():
                raise Cancelado()
            t0 = time.perf_counter()
            filas = quitar_furigana(self.lector.leer(imagen, modo, seguir))
            t1 = time.perf_counter()
            pars = [p for p in quitar_nombres(agrupar_parrafos(filas))
                    if self._vale(p["t"], idioma_de(p), modo, omitir_espanol)]
            if not seguir():
                raise Cancelado()
            trads = self.traducir_lote([(p["t"], idioma_de(p)) for p in pars]) if pars else []
            self.tiempos = (t1 - t0, time.perf_counter() - t1)
            bloques = []
            for p, tr in zip(pars, trads):
                tr = (tr or "").strip()
                if not tr or tr.lower() == p["t"].lower():
                    continue        # nada que traducir (números, nombres propios…)
                bloques.append({"caja": (p["x0"], p["y0"], p["x1"], p["y1"]),
                                "original": p["t"], "traduccion": tr,
                                "idioma": idioma_de(p)})
            return bloques

    def leer_parrafos(self, imagen, modo="auto", omitir_espanol=True, seguir=lambda: True):
        """Solo lectura (sin traducir): [{'caja':(x0,y0,x1,y1), 't', 'idioma'}]. Muy rápido."""
        with self._lock:
            if not seguir():
                raise Cancelado()
            t0 = time.perf_counter()
            filas = quitar_furigana(self.lector.leer(imagen, modo, seguir))
            self.tiempos = (time.perf_counter() - t0, self.tiempos[1])
            return [{"caja": (p["x0"], p["y0"], p["x1"], p["y1"]), "t": p["t"],
                     "idioma": idioma_de(p), "filas": p["filas"]}
                    for p in quitar_nombres(agrupar_parrafos(filas))
                    if self._vale(p["t"], idioma_de(p), modo, omitir_espanol)]

    # ---------- texto copiado ----------
    def traducir_texto(self, texto, modo="auto", omitir_espanol=True, seguir=lambda: True):
        """Traduce texto copiado (sin OCR). Cada línea detecta su idioma por separado."""
        texto = texto.replace("\r\n", "\n").replace("\r", "\n")[: self.MAX_COPIADO]
        lineas = []
        japones = bool(_KANA_RE.search(texto))       # con kana en el texto, los ideogramas son japonés
        for linea in texto.split("\n"):
            linea = linea.strip()
            idioma = detectar_idioma(linea, japones)
            if self._vale(linea, idioma, modo, omitir_espanol):
                lineas.append((linea, idioma))
        if not seguir():
            raise Cancelado()
        trads = self.traducir_lote(lineas) if lineas else []
        return [{"caja": None, "original": l, "traduccion": t.strip(), "idioma": i}
                for (l, i), t in zip(lineas, trads)
                if t and t.strip() and t.strip().lower() != l.lower()]


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


_KANA_SOLO = re.compile(r"^[\u3040-\u30ff・ー]+$")
_KANJI = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf々〆]")


def quitar_furigana(filas):
    """La furigana (kana pequeña encima de los kanji) no es una frase aparte: se quita del texto
    y su sitio se suma al renglón de abajo, para que también se borre al escribir la traducción."""
    principales = [f for f in filas if f["jp"] and _KANJI.search(f["t"])]
    if not principales:
        return filas
    fuera = set()
    for i, r in enumerate(filas):
        if not r["jp"] or len(r["t"]) > 12 or not _KANA_SOLO.match(r["t"]):
            continue
        h = r["y1"] - r["y0"]
        for f in principales:
            if f is r:
                continue
            hf = f["y1"] - f["y0"]
            hueco = f["y0"] - r["y1"]
            if (h <= 0.62 * hf and -0.35 * h <= hueco <= 0.45 * hf
                    and r["x0"] >= f["x0"] - 0.5 * hf and r["x1"] <= f["x1"] + 0.5 * hf):
                f.setdefault("rubi", []).append((r["x0"], r["y0"], r["x1"], r["y1"]))
                fuera.add(i)
                break
    return [r for i, r in enumerate(filas) if i not in fuera]


def _caja_con_rubi(f):
    x0, y0, x1, y1 = f["x0"], f["y0"], f["x1"], f["y1"]
    for a, b, c, d in f.get("rubi", ()):
        x0, y0, x1, y1 = min(x0, a), min(y0, b), max(x1, c), max(y1, d)
    return x0, y0, x1, y1


def agrupar_parrafos(filas):
    """Junta renglones de un mismo párrafo (texto que continúa), sin juntar menús.
    La caja del párrafo incluye la furigana de sus renglones; 'filas' lleva solo los renglones."""
    parrafos = []
    for f in sorted(filas, key=lambda f: (f["y0"], f["x0"])):
        h = f["y1"] - f["y0"]
        cx0, cy0, cx1, cy1 = _caja_con_rubi(f)
        for p in parrafos:
            u = p["ultima"]
            hu = u["y1"] - u["y0"]
            larga = (len(u["t"]) >= 10) if u["jp"] else (len(u["t"].split()) >= 4)
            if (p["jp"] == f["jp"] and p["idi"] == idioma_de(f) and larga
                    and not u["t"].rstrip().endswith(_FIN_FRASE)
                    and abs(f["x0"] - u["x0"]) < 1.0 * max(h, hu)
                    # chino/japonés: la caja es el carácter entero, así que el hueco entre renglones
                    # se ve mayor que en el alfabeto latino
                    and -0.2 * h <= f["y0"] - u["y1"] < (0.95 if f["jp"] else 0.6) * max(h, hu)
                    and abs(h - hu) < 0.35 * max(h, hu)):
                if f["jp"]:
                    p["t"] += f["t"]
                elif p["t"].endswith("-"):
                    p["t"] = p["t"][:-1] + f["t"]
                else:
                    p["t"] += " " + f["t"]
                p["x0"], p["x1"] = min(p["x0"], cx0), max(p["x1"], cx1)
                p["y0"], p["y1"] = min(p["y0"], cy0), max(p["y1"], cy1)
                p["ultima"] = f
                p["filas"].append((f["t"], (f["x0"], f["y0"], f["x1"], f["y1"])))
                break
        else:
            parrafos.append({"x0": cx0, "y0": cy0, "x1": cx1, "y1": cy1,
                             "t": f["t"], "jp": f["jp"], "idi": idioma_de(f), "ultima": f,
                             "filas": [(f["t"], (f["x0"], f["y0"], f["x1"], f["y1"]))]})
    return parrafos


_PALABRAS_MENU = set("""start load save options option settings setting continue quit exit back yes no
menu items item skills skill status equip config auto skip log history title return new game play close
next prev previous ok cancel help extras gallery music credits about quick system main inventory map
party magic attack defend run fight end chapter prologue epilogue part act day episode route""".split())
_NOMBRE_RE = re.compile(r"^[A-Z][A-Za-z'’\-]{0,15}(?: [A-Z][A-Za-z'’\-]{0,15})?$")


def es_nombre_personaje(p, otros):
    """¿Es el cuadro con el nombre de quien habla? Una o dos palabras con mayúscula, justo encima
    de un diálogo. Los nombres no se traducen ("Rose" no es "Rosa", "Hope" no es "Esperanza")."""
    if idioma_de(p) != "en":
        return False
    t = p["t"].strip().strip("[]【】「」:：").strip()
    if not _NOMBRE_RE.match(t) or any(w.lower() in _PALABRAS_MENU for w in t.split()):
        return False
    h = max(1.0, p["y1"] - p["y0"])
    for q in otros:
        if q is p:
            continue
        largo = len(q["t"]) / 3 if q["jp"] else len(q["t"].split())
        if (largo >= 4 and -0.3 * h <= q["y0"] - p["y1"] <= 3.5 * h
                and q["x0"] - 4 * h <= p["x0"] <= q["x1"] and p["x1"] <= q["x1"] + 4 * h):
            return True
    return False


def quitar_nombres(parrafos):
    """Los nombres de personaje (en alfabeto latino) se dejan como están en el juego."""
    return [p for p in parrafos if not es_nombre_personaje(p, parrafos)]


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


_CJK = ["YuGothM.ttc", "meiryo.ttc", "msgothic.ttc", "msyh.ttc", "simsun.ttc", "Hiragino Sans GB.ttc",
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
