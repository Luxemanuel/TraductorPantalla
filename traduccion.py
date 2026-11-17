# -*- coding: utf-8 -*-
"""
Motores de traducción:
  · Google (en línea, sin clave): rápido, buena calidad, cualquier idioma, japonés→destino directo.
  · Argos (sin internet): modelos CTranslate2 + SentencePiece usados directamente (sin PyTorch).
    Inglés→~50 idiomas y japonés→inglés; para japonés→otro idioma pasa por el inglés.
  · Traductor: usa Google y, si falla o no hay internet, cambia solo a Argos.
"""
import json
import os
import re
import shutil
import tempfile
import threading
import time
import zipfile

from nucleo import log

# código: nombre visible
IDIOMAS_DESTINO = {
    "es": "Español", "en": "Inglés", "pt": "Portugués", "fr": "Francés", "de": "Alemán",
    "it": "Italiano", "ca": "Catalán", "ru": "Ruso", "uk": "Ucraniano", "pl": "Polaco",
    "nl": "Neerlandés", "tr": "Turco", "ar": "Árabe", "hi": "Hindi", "ko": "Coreano",
    "zh": "Chino (simplificado)", "id": "Indonesio", "vi": "Vietnamita", "th": "Tailandés",
}
_CODIGO_GOOGLE = {"zh": "zh-CN"}
INDICE_ARGOS = "https://raw.githubusercontent.com/argosopentech/argospm-index/main/index.json"
_FIN_FRASE = re.compile(r"(?<=[.!?。！？])\s*")


def _frases(texto):
    partes = [p for p in _FIN_FRASE.split(texto) if p.strip()]
    return partes or [texto]


# ============================================================================
# Google (en línea)
# ============================================================================
class Google:
    nombre = "Google (en línea)"
    URL = "https://translate.googleapis.com/translate_a/single"

    def __init__(self):
        import requests
        self.s = requests.Session()
        self.s.headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"

    def _pedir(self, texto, origen, destino, espera):
        params = {"client": "gtx", "sl": origen, "tl": _CODIGO_GOOGLE.get(destino, destino),
                  "dt": "t", "ie": "UTF-8", "oe": "UTF-8"}
        if len(texto) < 1500:
            r = self.s.get(self.URL, params={**params, "q": texto}, timeout=espera)
        else:
            r = self.s.post(self.URL, params=params, data={"q": texto}, timeout=espera)
        r.raise_for_status()
        datos = r.json()
        return "".join(seg[0] for seg in (datos[0] or []) if seg and seg[0])

    def lote(self, textos, origen, destino, espera=4):
        if not textos:
            return []
        limpio = [t.replace("\n", " ").strip() for t in textos]
        salida = self._pedir("\n".join(limpio), origen, destino, espera).split("\n")
        if len(salida) == len(limpio):
            return [s.strip() for s in salida]
        # Si Google juntó o partió líneas, se piden una por una (en paralelo)
        res = [None] * len(limpio)

        def una(i):
            try:
                res[i] = self._pedir(limpio[i], origen, destino, espera).strip()
            except Exception:
                res[i] = None
        hilos = [threading.Thread(target=una, args=(i,)) for i in range(len(limpio))]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join(espera + 1)
        if any(r is None for r in res):
            raise RuntimeError("Google no respondió")
        return res


# ============================================================================
# Argos sin internet (sin la librería argostranslate ni PyTorch)
# ============================================================================
class ArgosLite:
    nombre = "Argos (sin internet)"

    def __init__(self, carpeta):
        self.carpeta = carpeta
        os.makedirs(carpeta, exist_ok=True)
        self._cargados = {}
        self._lock = threading.Lock()
        self.escanear()

    def escanear(self):
        self.paquetes = {}
        for nombre in os.listdir(self.carpeta):
            ruta = os.path.join(self.carpeta, nombre)
            meta = os.path.join(ruta, "metadata.json")
            if os.path.isfile(meta) and os.path.isfile(os.path.join(ruta, "sentencepiece.model")):
                try:
                    with open(meta, encoding="utf-8") as f:
                        m = json.load(f)
                    self.paquetes[(m["from_code"], m["to_code"])] = (ruta, m)
                except Exception:
                    log.exception("Paquete Argos ilegible: %s", ruta)

    def ruta(self, origen, destino):
        """Pares necesarios para traducir origen->destino (directo o pasando por inglés)."""
        if origen == destino:
            return []
        if (origen, destino) in self.paquetes:
            return [(origen, destino)]
        pasos = []
        if origen != "en":
            pasos.append((origen, "en"))
        if destino != "en":
            pasos.append(("en", destino))
        return pasos

    def disponible(self, origen, destino):
        return all(p in self.paquetes for p in self.ruta(origen, destino))

    def _modelo(self, par):
        if par not in self._cargados:
            import ctranslate2
            import sentencepiece as spm
            ruta, meta = self.paquetes[par]
            hilos = max(1, (os.cpu_count() or 2) // 2)
            traductor = ctranslate2.Translator(os.path.join(ruta, "model"), device="cpu",
                                               compute_type="int8", inter_threads=1, intra_threads=hilos)
            sp = spm.SentencePieceProcessor(model_file=os.path.join(ruta, "sentencepiece.model"))
            self._cargados[par] = (traductor, sp, meta.get("target_prefix", ""))
        return self._cargados[par]

    def _par(self, textos, par):
        traductor, sp, prefijo = self._modelo(par)
        frases, idx = [], []
        for i, t in enumerate(textos):
            for f in _frases(t):
                frases.append(sp.encode(f, out_type=str))
                idx.append(i)
        if not frases:
            return list(textos)
        res = traductor.translate_batch(frases, beam_size=2, max_decoding_length=256,
                                        replace_unknowns=True,
                                        target_prefix=[[prefijo]] * len(frases) if prefijo else None)
        trozos = [[] for _ in textos]
        for i, r in zip(idx, res):
            v = sp.decode_pieces(r.hypotheses[0]).replace("▁", " ").replace("_", " ")
            if prefijo and v.startswith(prefijo):
                v = v[len(prefijo):]
            trozos[i].append(v.strip())
        return [" ".join(t) for t in trozos]

    def lote(self, textos, origen, destino, espera=None):
        with self._lock:
            salida = list(textos)
            for par in self.ruta(origen, destino):
                if par not in self.paquetes:
                    raise RuntimeError(f"Falta el diccionario sin internet {par[0]}→{par[1]}")
                salida = self._par(salida, par)
            return salida

    # ---------- descargas ----------
    def descargar(self, origen, destino, avisar=print):
        """Descarga los paquetes que falten para origen->destino. Devuelve True si quedó listo."""
        import requests
        faltan = [p for p in self.ruta(origen, destino) if p not in self.paquetes]
        if not faltan:
            return True
        indice = requests.get(INDICE_ARGOS, timeout=20).json()
        for o, d in faltan:
            paq = next((p for p in indice if p["from_code"] == o and p["to_code"] == d), None)
            if not paq:
                avisar(f"No existe diccionario sin internet {o}→{d}")
                return False
            avisar(f"Descargando diccionario sin internet {o}→{d}…")
            url = next(l for l in paq["links"] if l.startswith("http"))
            tmp = tempfile.mkdtemp()
            try:
                archivo = os.path.join(tmp, "paquete.zip")
                with requests.get(url, stream=True, timeout=60) as r:
                    r.raise_for_status()
                    with open(archivo, "wb") as f:
                        for bloque in r.iter_content(1 << 20):
                            f.write(bloque)
                with zipfile.ZipFile(archivo) as z:
                    z.extractall(tmp)
                carpeta = next(os.path.join(tmp, n) for n in os.listdir(tmp)
                               if os.path.isfile(os.path.join(tmp, n, "metadata.json")))
                destino_dir = os.path.join(self.carpeta, os.path.basename(carpeta))
                if os.path.exists(destino_dir):
                    shutil.rmtree(destino_dir)
                shutil.move(carpeta, destino_dir)
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
        self.escanear()
        return self.disponible(origen, destino)


# ============================================================================
# Traductor combinado
# ============================================================================
class Traductor:
    def __init__(self, cfg, carpeta_argos):
        self.cfg = cfg if callable(cfg) else (lambda c=cfg: c)   # función que da la config actual
        self.google = None
        try:
            self.google = Google()
        except Exception:
            log.exception("Traducción en línea no disponible")
        self.argos = ArgosLite(carpeta_argos)
        self.sin_red_hasta = 0
        self.ultimo_motor = ""

    def lote(self, textos, origen, destino):
        modo = self.cfg().get("motor_traduccion", "auto")
        if self.google and modo != "sin_internet" and (modo == "en_linea" or time.time() >= self.sin_red_hasta):
            try:
                res = self.google.lote(textos, origen, destino)
                self.ultimo_motor = "en línea"
                return res
            except Exception as e:
                log.warning("Traducción en línea falló: %s", e)
                if modo == "en_linea":
                    raise
                self.sin_red_hasta = time.time() + 30     # reintentar en línea en 30 s
        res = self.argos.lote(textos, origen, destino)
        self.ultimo_motor = "sin internet"
        return res
