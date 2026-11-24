# -*- coding: utf-8 -*-
"""
Atajos de teclado de todo el sistema.

En Windows se registran con RegisterHotKey: Windows avisa directamente al programa, sin
"gancho" de teclado. Así los atajos:
  · no se apagan solos cuando el equipo va cargado (Windows quita en silencio los ganchos
    que tardan en responder),
  · funcionan aunque el juego se ejecute como administrador,
  · y la combinación no le llega al juego.
Si otra aplicación ya usa una combinación, esa se vigila con pynput (respaldo).
"""
import sys
import threading

from nucleo import atajo_pynput, log

ES_WINDOWS = sys.platform.startswith("win")

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x4000
WM_HOTKEY, WM_QUIT = 0x0312, 0x0012


def codigo_tecla(tecla):
    """Código virtual de Windows para las teclas que se pueden elegir (A-Z, 0-9, F1-F12, Espacio)."""
    if tecla == "Espacio":
        return 0x20
    if tecla.startswith("F") and tecla[1:].isdigit():
        n = int(tecla[1:])
        return 0x6F + n if 1 <= n <= 24 else None
    if len(tecla) == 1 and tecla.upper() in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789":
        return ord(tecla.upper())
    return None


def modificadores(at):
    return ((MOD_CONTROL if at.get("ctrl") else 0) | (MOD_ALT if at.get("alt") else 0)
            | (MOD_SHIFT if at.get("shift") else 0))


class Atajos:
    """mapa: {acción: atajo (dict ctrl/alt/shift/tecla)}; al_pulsar(acción) se llama desde otro hilo."""

    def __init__(self, mapa, al_pulsar):
        self.mapa = dict(mapa)
        self.al_pulsar = al_pulsar
        self.registrados = []          # acciones con RegisterHotKey
        self.respaldo = []             # acciones vigiladas con pynput
        self._hilo = None
        self._tid = None
        self._pynput = None

    def iniciar(self):
        pendientes = dict(self.mapa)
        if ES_WINDOWS:
            listo = threading.Event()
            self._hilo = threading.Thread(target=self._bucle, args=(listo,), daemon=True, name="atajos")
            self._hilo.start()
            listo.wait(5)
            for a in self.registrados:
                pendientes.pop(a, None)
        if pendientes:
            self._iniciar_pynput(pendientes)
        return self

    # ---------- Windows: RegisterHotKey en un hilo con su propia cola de mensajes ----------
    def _bucle(self, listo):
        import ctypes
        import ctypes.wintypes as wt
        u32 = ctypes.WinDLL("user32", use_last_error=True)
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        u32.RegisterHotKey.argtypes = [wt.HWND, ctypes.c_int, wt.UINT, wt.UINT]
        u32.UnregisterHotKey.argtypes = [wt.HWND, ctypes.c_int]
        u32.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT]
        u32.PeekMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT, wt.UINT]
        msg = wt.MSG()
        u32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)        # crea la cola de mensajes del hilo
        self._tid = k32.GetCurrentThreadId()
        ids = {}
        try:
            for n, (accion, at) in enumerate(self.mapa.items(), start=1):
                vk = codigo_tecla(at.get("tecla", ""))
                if vk is None:
                    continue
                if u32.RegisterHotKey(None, n, modificadores(at) | MOD_NOREPEAT, vk):
                    ids[n] = accion
                    self.registrados.append(accion)
                else:
                    log.info("Atajo %s ocupado por otro programa (error %s): se usa el respaldo",
                             accion, ctypes.get_last_error())
        finally:
            listo.set()
        try:
            while True:
                r = u32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if r == 0 or r == -1:            # WM_QUIT o error
                    break
                if msg.message == WM_HOTKEY and msg.wParam in ids:
                    try:
                        self.al_pulsar(ids[msg.wParam])
                    except Exception:
                        log.exception("Atajo")
        finally:
            for n in ids:
                u32.UnregisterHotKey(None, n)

    # ---------- respaldo: pynput ----------
    def _iniciar_pynput(self, pendientes):
        try:
            from pynput import keyboard
            mapa = {atajo_pynput(at): (lambda a=a: self.al_pulsar(a)) for a, at in pendientes.items()}
            self._pynput = keyboard.GlobalHotKeys(mapa)
            self._pynput.daemon = True
            self._pynput.start()
            self.respaldo = list(pendientes)
        except Exception:
            log.exception("Atajos de respaldo (pynput) no disponibles")
            raise

    def detener(self):
        if self._tid is not None:
            try:
                import ctypes
                ctypes.windll.user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)
            except Exception:
                pass
            if self._hilo is not None:
                self._hilo.join(2)
            self._tid = None
        if self._pynput is not None:
            try:
                self._pynput.stop()
            except Exception:
                pass
            self._pynput = None
