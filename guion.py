# -*- coding: utf-8 -*-
"""
Texto exacto de los juegos Ren'Py.

Las novelas visuales hechas con Ren'Py traen su guion dentro de la carpeta "game": archivos
.rpy (texto), .rpyc (compilados) o metidos en archive.rpa. Aquí se sacan sus frases (diálogos y
opciones de menú) y, mientras se juega, cada frase leída de la pantalla se cambia por la frase
exacta del guion que más se le parece. Así un error de lectura (una "l" por una "I", un kanji
confundido) no llega a la traducción.

Seguridad: los .rpyc son "pickles" de Python. Se leen con un lector que NUNCA ejecuta nada del
juego: cualquier clase o función que pidan se sustituye por un objeto vacío que solo guarda datos.
"""
import difflib
import io
import os
import pickle
import re
import struct
import threading
import time
import zlib

from nucleo import log

LIMITE_BYTES = 300 << 20           # no leer más de 300 MB de guiones
LIMITE_FRASES = 400_000


# ============================================================================
# Lectura segura de .rpyc
# ============================================================================
class _Nodo:
    """Sustituto de cualquier clase del juego: acepta cualquier argumento y guarda su estado."""

    def __new__(cls, *a, **k):
        return object.__new__(cls)

    def __init__(self, *a, **k):
        pass

    def __setstate__(self, estado):
        extra = None
        if isinstance(estado, tuple) and len(estado) == 2:
            estado, extra = estado
        for d in (estado, extra):
            if isinstance(d, dict):
                for k, v in d.items():
                    if isinstance(k, bytes):
                        k = k.decode("utf-8", "replace")
                    if isinstance(k, str):
                        self.__dict__[k] = v

    # listas y diccionarios del juego (RevertableList, RevertableDict…)
    def append(self, v):
        self.__dict__.setdefault("_elementos", []).append(v)

    def extend(self, vs):
        self.__dict__.setdefault("_elementos", []).extend(vs)

    def __setitem__(self, k, v):
        self.__dict__.setdefault("_elementos", []).append(v)

    def add(self, v):
        self.append(v)

    def update(self, *a, **k):
        pass


_CLASES = {}
_PERMITIDAS = {"set": set, "frozenset": frozenset, "list": list, "dict": dict, "tuple": tuple,
               "str": str, "unicode": str, "bytes": bytes, "bytearray": bytearray, "int": int,
               "long": int, "float": float, "complex": complex, "bool": bool, "object": object}


def _reconstruir(cls, base=None, estado=None):
    if isinstance(cls, type) and issubclass(cls, _Nodo):
        return cls.__new__(cls)
    return _clase("copyreg", "_reconstructor")()


def _clase(modulo, nombre):
    clave = (modulo, nombre)
    if clave not in _CLASES:
        _CLASES[clave] = type(nombre, (_Nodo,), {"_modulo": modulo})
    return _CLASES[clave]


class _LectorSeguro(pickle.Unpickler):
    def find_class(self, modulo, nombre):
        if modulo in ("builtins", "__builtin__") and nombre in _PERMITIDAS:
            return _PERMITIDAS[nombre]
        if modulo in ("copy_reg", "copyreg") and nombre == "_reconstructor":
            return _reconstruir
        return _clase(modulo, nombre)            # todo lo demás: un objeto vacío, nunca código


def _blob_rpyc(datos):
    """El pickle comprimido de un .rpyc (formato 1 o 2)."""
    if datos.startswith(b"RENPY RPC2"):
        pos, trozos = 10, {}
        while pos + 12 <= len(datos):
            ranura, ini, largo = struct.unpack("III", datos[pos:pos + 12])
            if ranura == 0:
                break
            trozos[ranura] = datos[ini:ini + largo]
            pos += 12
        datos = trozos.get(1, b"")
    return zlib.decompress(datos)


def frases_de_rpyc(datos):
    """Diálogos y opciones de menú de un .rpyc (bytes)."""
    arbol = _LectorSeguro(io.BytesIO(_blob_rpyc(datos)), encoding="bytes").load()
    salida, pila, vistos = [], [arbol], set()
    while pila and len(vistos) < 3_000_000:
        o = pila.pop()
        if id(o) in vistos:
            continue
        vistos.add(id(o))
        if isinstance(o, _Nodo):
            d = o.__dict__
            nombre = type(o).__name__
            if nombre in ("Say", "TranslateSay") and isinstance(d.get("what"), (str, bytes)):
                salida.append(d["what"])
            elif nombre == "Menu" and isinstance(d.get("items"), (list, tuple)):
                for it in d["items"]:
                    if isinstance(it, (list, tuple)) and it and isinstance(it[0], (str, bytes)):
                        salida.append(it[0])
            pila.extend(d.values())
        elif isinstance(o, (list, tuple, set, frozenset)):
            pila.extend(o)
        elif isinstance(o, dict):
            pila.extend(o.values())
    return [s.decode("utf-8", "replace") if isinstance(s, bytes) else s for s in salida]


# ============================================================================
# .rpy (texto)
# ============================================================================
_NO_DIALOGO = set("""define default image scene show hide play queue stop jump call label screen style
transform init python with pause voice return window nvl camera layeredimage text add key use
action hover idle font size color xpos ypos xalign yalign at as behind onlayer zorder""".split())
_CADENA = r'"((?:[^"\\]|\\.)*)"'
_LINEA_DIALOGO = re.compile(r'^\s*(?:([A-Za-z_][\w.]*)\s+)?(?:[\w ]*?\s)?' + _CADENA + r'\s*(?::|$|with\b|\(|nointeract)')
_TRADUCIBLE = re.compile(r'_\(\s*' + _CADENA + r'\s*\)')


def frases_de_rpy(texto):
    salida = []
    for linea in texto.splitlines():
        s = linea.strip()
        if not s or s.startswith(("#", "$")):
            continue
        primera = s.split(None, 1)[0]
        if primera in _NO_DIALOGO and not _TRADUCIBLE.search(s):
            continue
        m = _LINEA_DIALOGO.match(linea)
        if m and (m.group(1) is None or m.group(1) not in _NO_DIALOGO):
            salida.append(m.group(2).replace('\\"', '"').replace("\\n", "\n"))
        salida += [t.replace('\\"', '"') for t in _TRADUCIBLE.findall(s)]
    return salida


# ============================================================================
# archive.rpa
# ============================================================================
def archivos_de_rpa(ruta, extensiones, tam_max=60 << 20):
    """[(nombre, bytes)] de los archivos con esas extensiones dentro de un .rpa."""
    from estilo import _SoloDatos
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
            if not isinstance(nombre, str) or not nombre.lower().endswith(extensiones):
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
                salida.append((nombre, prefijo + f.read(largo - len(prefijo))))
            except Exception:
                continue
    return salida


# ============================================================================
# El guion y la búsqueda de la frase exacta
# ============================================================================
_ETIQUETA = re.compile(r"\{[^{}]*\}")
_CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿]")


def limpiar(frase):
    """Texto tal como se ve en pantalla: sin etiquetas {i}, {w}, {color=…} de Ren'Py."""
    f = frase.replace("{{", "\x00").replace("\n", " ").replace("\\n", " ")
    f = _ETIQUETA.sub("", f).replace("\x00", "{")
    return re.sub(r"\s+", " ", f).strip()


def _norm(t):
    if _CJK.search(t):
        return re.sub(r"[\s\W_]+", "", t)
    return re.sub(r"[^a-z0-9]+", " ", t.lower()).strip()


def carpeta_juego(ruta_exe):
    """La carpeta 'game' de un juego Ren'Py (o None si no lo es)."""
    if not ruta_exe:
        return None
    base = os.path.dirname(ruta_exe)
    for c in (os.path.join(base, "game"), os.path.join(os.path.dirname(base), "game")):
        if os.path.isdir(c) and (os.path.isdir(os.path.join(os.path.dirname(c), "renpy"))
                                 or any(n.endswith((".rpa", ".rpyc", ".rpy")) for n in os.listdir(c))):
            return c
    return None


class Guion:
    def __init__(self, frases):
        vistas, self.frases, self.normas = set(), [], []
        for f in frases:
            limpia = limpiar(f)
            n = _norm(limpia)
            if len(n) < 4 or "[" in limpia or n in vistas:     # [variables]: no se pueden reconstruir
                continue
            vistas.add(n)
            self.frases.append(limpia)
            self.normas.append(n)
            if len(self.frases) >= LIMITE_FRASES:
                break
        self.indice = {}
        for i, n in enumerate(self.normas):
            for g in self._gramas(n):
                self.indice.setdefault(g, []).append(i)
        self._cache = {}

    def __len__(self):
        return len(self.frases)

    @staticmethod
    def _gramas(n):
        return {n[i:i + 3] for i in range(max(1, len(n) - 2))}

    def exacta(self, texto):
        """La frase del guion que corresponde a lo leído, o None si no hay una clara."""
        if texto in self._cache:
            return self._cache[texto]
        n = _norm(texto)
        mejor = None
        largo_min = 6 if _CJK.search(n) else 15        # lo corto (nombres, botones) se lee bien solo
        if len(n) >= largo_min:
            votos = {}
            for g in self._gramas(n):
                lista = self.indice.get(g)
                if lista and len(lista) <= 3000:            # los trozos muy comunes no ayudan
                    for i in lista:
                        votos[i] = votos.get(i, 0) + 1
            candidatas = sorted(votos, key=votos.get, reverse=True)[:25]
            puntos = 0.0
            for i in candidatas:
                c = self.normas[i]
                if not 0.75 <= len(c) / len(n) <= 1.33:
                    continue
                r = difflib.SequenceMatcher(None, n, c, autojunk=False).ratio()
                if r > puntos:
                    puntos, mejor = r, i
            mejor = self.frases[mejor] if mejor is not None and puntos >= 0.82 else None
        if len(self._cache) > 5000:
            self._cache.clear()
        self._cache[texto] = mejor
        return mejor

    def corregir(self, parrafos):
        """Cambia el texto leído de cada párrafo por la frase exacta del guion (si la hay)."""
        for p in parrafos:
            e = self.exacta(p["t"])
            if e and e != p["t"]:
                p["t"] = e
                p["exacta"] = True
        return parrafos


def cargar_guion(carpeta):
    """Lee todas las frases del juego (puede tardar unos segundos en juegos grandes)."""
    t0 = time.time()
    frases, leidos = [], 0
    for raiz, dirs, archivos in os.walk(carpeta):
        dirs[:] = [d for d in dirs if d not in ("saves", "cache", "tl")]
        for a in archivos:
            ruta = os.path.join(raiz, a)
            al = a.lower()
            try:
                if al.endswith(".rpa"):
                    for nombre, datos in archivos_de_rpa(ruta, (".rpyc", ".rpy")):
                        if "/tl/" in "/" + nombre.replace("\\", "/"):
                            continue
                        leidos += len(datos)
                        frases += frases_de_rpyc(datos) if nombre.endswith(".rpyc") else \
                            frases_de_rpy(datos.decode("utf-8", "replace"))
                elif al.endswith(".rpyc"):
                    with open(ruta, "rb") as f:
                        datos = f.read()
                    leidos += len(datos)
                    frases += frases_de_rpyc(datos)
                elif al.endswith(".rpy"):
                    with open(ruta, encoding="utf-8", errors="replace") as f:
                        frases += frases_de_rpy(f.read())
            except Exception:
                log.exception("Leyendo el guion de %s", ruta)
            if leidos > LIMITE_BYTES:
                break
    g = Guion(frases)
    log.info("Guion de Ren'Py: %d frases de %s (%.1f s)", len(g), carpeta, time.time() - t0)
    return g


def cargar_en_segundo_plano(ruta_exe, listo):
    """Si el programa es un juego Ren'Py, carga su guion en otro hilo y llama a listo(guion)."""
    carpeta = carpeta_juego(ruta_exe)
    if not carpeta:
        return False

    def tarea():
        try:
            g = cargar_guion(carpeta)
            if len(g):
                listo(g)
        except Exception:
            log.exception("Guion de Ren'Py")
    threading.Thread(target=tarea, daemon=True, name="guion").start()
    return True
