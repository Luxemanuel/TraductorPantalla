# -*- coding: utf-8 -*-
"""
Subtítulos de VOZ: escucha lo que suena en tus altavoces (no el micrófono), detecta
cuándo alguien habla, lo transcribe con Whisper (sin internet) y lo traduce.

  audio del sistema ──► Segmentador (detector de voz Silero) ──► Whisper ──► traducción ──► barra
"""
import os
import queue
import threading
import time
from collections import deque

import numpy as np

import nucleo
from nucleo import log

SR = 16000
PASO = 0.25                       # segundos de audio por lectura
ALUCINACIONES = {                 # frases que Whisper "inventa" con música o silencio
    "thank you.", "thanks for watching!", "thank you for watching.", "thank you so much for watching.",
    "please subscribe.", "you", "bye.", "ご視聴ありがとうございました", "ご視聴ありがとうございました。",
    "おやすみなさい。", "(音楽)", "[音楽]", "[music]", "(music)", "♪",
}


def carpeta_modelos_voz():
    ruta = os.path.join(nucleo.RUTA_MODELOS, "voz")
    os.makedirs(ruta, exist_ok=True)
    return ruta


# ============================================================================
# Segmentador: corta el audio en frases usando el detector de voz Silero
# ============================================================================
class Segmentador:
    def __init__(self, silencio_fin=0.55, max_frase=7.0, ventana=4.0):
        from faster_whisper.vad import VadOptions
        self.opciones = VadOptions(threshold=0.5, min_silence_duration_ms=200, speech_pad_ms=120,
                                   min_speech_duration_ms=200)
        self.silencio_fin = silencio_fin
        self.max_frase = max_frase
        self.ventana = int(ventana * SR)
        self.audio = np.zeros(0, dtype=np.float32)   # audio reciente
        self.inicio_buf = 0                           # posición absoluta de audio[0]
        self.inicio_voz = None                        # posición absoluta donde empezó a hablar
        self.ultimo_fin = 0                           # fin de la última frase entregada

    def agregar(self, trozo):
        """Recibe audio mono 16 kHz. Devuelve [(frase, segundos de silencio antes de ella)]."""
        from faster_whisper.vad import get_speech_timestamps
        self.audio = np.concatenate([self.audio, trozo.astype(np.float32)])
        fin_abs = self.inicio_buf + len(self.audio)
        ventana = self.audio[-self.ventana:]
        base = fin_abs - len(ventana)
        voces = get_speech_timestamps(ventana, self.opciones) if np.abs(ventana).max() > 0.003 else []
        frases = []
        hablando_ahora = bool(voces) and (len(ventana) - voces[-1]["end"]) < self.silencio_fin * SR
        if self.inicio_voz is None:
            if hablando_ahora:
                self.inicio_voz = base + voces[-1]["start"]
                for v in voces:               # si la frase empezó antes en la ventana
                    if base + v["end"] > self.inicio_voz - 0.4 * SR:
                        self.inicio_voz = min(self.inicio_voz, base + v["start"])
                # nunca volver a incluir audio de una frase ya entregada
                self.inicio_voz = max(self.inicio_voz, self.ultimo_fin)
        else:
            largo = fin_abs - self.inicio_voz
            if not hablando_ahora or largo > self.max_frase * SR:
                fin_voz = fin_abs if hablando_ahora else min(fin_abs, base + (voces[-1]["end"] if voces else 0) + int(0.15 * SR))
                fin_voz = max(fin_voz, self.inicio_voz)
                frase = self._recortar(self.inicio_voz, fin_voz)
                if len(frase) > 0.3 * SR:
                    hueco = (self.inicio_voz - self.ultimo_fin) / SR if self.ultimo_fin else 99.0
                    frases.append((frase, hueco))
                self.ultimo_fin = fin_voz
                self.inicio_voz = fin_abs if hablando_ahora else None
        # no guardar más audio del necesario
        guardar_desde = (self.inicio_voz if self.inicio_voz is not None else fin_abs) - self.ventana
        if guardar_desde > self.inicio_buf:
            corte = guardar_desde - self.inicio_buf
            self.audio = self.audio[corte:]
            self.inicio_buf += corte
        return frases

    def terminar(self):
        """Entrega lo que quede (al apagar o al final de un archivo)."""
        if self.inicio_voz is None:
            return []
        frase = self._recortar(self.inicio_voz, self.inicio_buf + len(self.audio))
        hueco = (self.inicio_voz - self.ultimo_fin) / SR if self.ultimo_fin else 99.0
        self.inicio_voz = None
        return [(frase, hueco)] if len(frase) > 0.3 * SR else []

    def _recortar(self, a, b):
        return self.audio[max(0, a - self.inicio_buf): max(0, b - self.inicio_buf)].copy()


# ============================================================================
# Transcriptor (Whisper)
# ============================================================================
class Transcriptor:
    def __init__(self, nombre="base", avisar=print):
        from faster_whisper import WhisperModel
        avisar(f"Cargando reconocimiento de voz ({nombre})… la primera vez se descarga "
               f"({'~150' if nombre == 'base' else '~480'} MB)")
        self.modelo = WhisperModel(nombre, device="cpu", compute_type="int8",
                                   cpu_threads=max(2, (os.cpu_count() or 4) - 1),
                                   download_root=carpeta_modelos_voz())

    def transcribir(self, audio, idioma=None):
        """Devuelve (texto, idioma) o (None, None) si no había voz clara."""
        segs, info = self.modelo.transcribe(
            audio, language=idioma, beam_size=1, best_of=1, temperature=0.0,
            condition_on_previous_text=False, without_timestamps=True, vad_filter=False,
            no_speech_threshold=0.6)
        partes = [s.text.strip() for s in segs
                  if not (s.no_speech_prob > 0.6 and s.avg_logprob < -0.8) and s.avg_logprob > -1.2]
        texto = " ".join(p for p in partes if p).strip()
        if not texto or texto.lower() in ALUCINACIONES:
            return None, None
        return texto, (idioma or info.language)


# ============================================================================
# Servicio completo (lo usa la ventana principal)
# ============================================================================
class SubtitulosDeVoz:
    def __init__(self, app):
        self.app = app
        self.activo = False
        self.frases = queue.Queue()
        self._transcriptor = None
        self._previo = None          # (texto, idioma, momento) de la última frase mostrada

    def iniciar(self):
        self.activo = True
        threading.Thread(target=self._transcribir_bucle, daemon=True, name="voz-texto").start()

    def detener(self):
        self.activo = False

    def _avisar(self, texto):
        self.app.cola.put(("voz_estado", (self, texto)))

    # ---------- captura del audio del sistema ----------
    def _capturar_bucle(self):
        try:
            import ctypes
            ctypes.windll.ole32.CoInitializeEx(None, 0)
        except Exception:
            pass
        try:
            import soundcard as sc
            altavoz = sc.default_speaker()
            mic = sc.get_microphone(id=str(altavoz.name), include_loopback=True)
        except Exception as e:
            log.exception("Captura de audio")
            self._avisar(f"No pude escuchar el audio del sistema: {e}")
            self.activo = False
            return
        seg = Segmentador()
        n = int(PASO * SR)
        try:
            with mic.recorder(samplerate=SR, channels=1, blocksize=1024) as rec:
                self._avisar("● Subtítulos de voz: escuchando…")
                while self.activo:
                    datos = rec.record(numframes=n)
                    mono = datos[:, 0] if datos.ndim == 2 else datos
                    for frase, hueco in seg.agregar(mono):
                        if self.frases.qsize() > 2:      # ir siempre al día: descartar lo atrasado
                            try:
                                self.frases.get_nowait()
                            except queue.Empty:
                                pass
                        self.frases.put((frase, time.time(), hueco))
        except Exception as e:
            log.exception("Grabando el audio del sistema")
            self._avisar(f"Se cortó la captura de audio: {e}")
            self.activo = False

    # ---------- transcripción + traducción ----------
    def _transcribir_bucle(self):
        try:
            self._transcriptor = Transcriptor(self.app.cfg.get("modelo_voz", "base"), self._avisar)
        except Exception as e:
            log.exception("Cargando Whisper")
            self._avisar(f"No se pudo cargar el reconocimiento de voz: {e}")
            self.activo = False
            return
        threading.Thread(target=self._capturar_bucle, daemon=True, name="voz-audio").start()
        while self.activo:
            try:
                frase, t_fin, hueco = self.frases.get(timeout=0.3)
            except queue.Empty:
                continue
            self.procesar_frase(frase, t_fin, hueco)

    def procesar_frase(self, frase, t_fin=None, hueco=99.0):
        idioma_cfg = self.app.cfg.get("idioma_origen", "auto")
        try:
            texto, idioma = self._transcriptor.transcribir(frase, None if idioma_cfg == "auto" else idioma_cfg)
        except Exception:
            log.exception("Transcribiendo")
            return None
        if not texto or idioma not in ("en", "ja"):
            return None
        # Si esta frase empezó casi pegada a la anterior (pausa corta, p. ej. una coma),
        # es la misma oración: se muestran juntas para que la traducción tenga sentido.
        ahora = time.time()
        p = self._previo
        reemplaza = None
        if p and p[1] == idioma and hueco < 0.8 and len(p[0].split()) < 30:
            reemplaza = p[0]
            texto = p[0].rstrip(".。") + ("" if idioma == "ja" else " ") + texto
        self._previo = (texto, idioma, ahora)
        destino = self.app.cfg.get("idioma_destino", "es")
        try:
            trad = texto if idioma == destino else self.app.motor.traducir_lote([(texto, idioma)])[0]
        except Exception:
            log.exception("Traduciendo voz")
            trad = None
        demora = time.time() - t_fin if t_fin else 0.0
        resultado = {"original": texto, "traduccion": trad or texto, "idioma": idioma, "demora": demora,
                     "reemplaza": reemplaza}
        self.app.cola.put(("voz", (self, resultado)))
        return resultado


def leer_wav(ruta):
    """WAV -> audio mono 16 kHz float32 (para pruebas)."""
    import wave
    with wave.open(ruta, "rb") as w:
        sr, canales, ancho = w.getframerate(), w.getnchannels(), w.getsampwidth()
        datos = w.readframes(w.getnframes())
    tipo = {1: np.int8, 2: np.int16, 4: np.int32}[ancho]
    a = np.frombuffer(datos, dtype=tipo).astype(np.float32) / float(np.iinfo(tipo).max)
    if canales > 1:
        a = a.reshape(-1, canales).mean(axis=1)
    if sr != SR:
        x = np.linspace(0, len(a), int(len(a) * SR / sr), endpoint=False)
        a = np.interp(x, np.arange(len(a)), a).astype(np.float32)
    return a
