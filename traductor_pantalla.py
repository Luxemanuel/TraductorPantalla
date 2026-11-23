# -*- coding: utf-8 -*-
"""
Traductor de Pantalla  ·  Inglés / Japonés  →  Español
=======================================================
Captura la pantalla (o una zona), reconoce el texto (OCR), lo traduce sin
internet (glosario propio + Argos Translate) y lo muestra encima de las
palabras o en un panel de texto.  Abre la Ayuda del programa para ver todo.
"""
import os
import queue
import socket
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk, messagebox

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageTk

import nucleo
from nucleo import (ACCIONES, ACCIONES_MANTENER, TAM_INTERFAZ, Motor, Voz, Cancelado, atajo_legible,
                    atajo_pynput, log)
import ventanas
import envivo
import tema

# En Windows la traducción es "en vivo" (capa transparente que deja pasar los clics).
VIVO = envivo.ES_WINDOWS or os.environ.get("TRADUCTOR_VIVO") == "1"

PUERTO_INSTANCIA = 47653
CANDADO = None          # socket que impide abrir el programa dos veces


def activar_dpi_windows():
    """Evita que Windows escale la ventana y desalinee los recuadros."""
    if sys.platform.startswith("win"):
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                import ctypes
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass


class App:
    def __init__(self, motor=None):
        activar_dpi_windows()
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title("Traductor de Pantalla")
        self.root.report_callback_exception = self._error_tk
        self.cfg = nucleo.cargar_config()
        self.voz = Voz()
        self.cola = queue.Queue()
        self.motor = motor or Motor(avisar=lambda m: self.cola.put(("estado", m)), cfg=lambda: self.cfg)
        self.voz_subs = None          # subtítulos de voz activos
        self.barra_voz = None
        self.ultimo_estilo = "palabras"
        self._estilo_zona = "palabras"
        self.vivo = None              # sesión de traducción en vivo activa
        self.listo = False
        self.ocupado = False
        self.trabajo = 0              # identifica cada traducción (para poder cancelar)
        self.overlay = None
        self.selector = None
        self.ultima_zona = None       # (monitor, caja)
        self.ultimos_bloques = []
        self.config_win = None
        self.glosario_win = None
        self.historial_win = None
        self.fuente_actual = "pantalla"
        self.historial = nucleo.Historial(self.cfg)
        self.historial.al_cambiar = self._historial_cambio
        self._hk = None
        self._panel_estaba = False
        self.panel = ventanas.Panel(self)
        self.perfiles = nucleo.cargar_perfiles()
        self._auto_suprimidas = set()      # ventanas en las que el usuario detuvo el inicio automático
        self._viendo_original = False
        self.bandeja = None
        self.menu_win = None

        self.fuente_ui = tkfont.nametofont("TkDefaultFont")
        self.fuente_titulo = tkfont.Font(family=self.fuente_ui.actual("family"), weight="bold")
        self.fuente_mono = tkfont.nametofont("TkFixedFont")
        self._icono = self._crear_icono()
        self.poner_icono(self.root)
        self._aplicar_tam_interfaz()
        self._construir()
        self._registrar_atajos()
        self.root.protocol("WM_DELETE_WINDOW", self.salir)
        self.root.deiconify()
        tema.barra_oscura(self.root)
        self.root.after(1500, self._vigilar_perfiles)
        self.root.after(60, self._vigilar_tecla_original)
        if self.cfg.get("bandeja", True) and motor is None:
            self.root.after(800, self._poner_bandeja)
        if motor is not None:          # motor ya cargado (autoprueba)
            self.listo = True
        else:
            threading.Thread(target=self._cargar_motor, daemon=True).start()
        self.root.after(100, self._revisar_cola)

    # ======================================================================
    # Construcción de la ventana principal
    # ======================================================================
    def _crear_icono(self):
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle((2, 2, 62, 62), radius=14, fill=(74, 8, 22))
        f = nucleo.cargar_fuente("Arial", 30, True)
        d.text((10, 14), "A", font=f, fill=(245, 238, 220))
        d.text((32, 22), "ñ", font=f, fill=(255, 210, 90))
        self._icono_pil = img
        return ImageTk.PhotoImage(img, master=self.root)

    def poner_icono(self, ventana):
        ventana.es_del_traductor = True       # marca las ventanas propias del programa
        try:
            ventana.iconphoto(False, self._icono)
        except Exception:
            pass
        try:
            if not ventana.overrideredirect():
                ventana.after(1, lambda: tema.barra_oscura(ventana))
        except tk.TclError:
            pass

    def _aplicar_tam_interfaz(self):
        tam = TAM_INTERFAZ.get(self.cfg["tam_interfaz"], 10)
        for nombre in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont",
                       "TkCaptionFont", "TkSmallCaptionFont", "TkIconFont", "TkTooltipFont"):
            try:
                tkfont.nametofont(nombre).configure(size=tam)
            except tk.TclError:
                pass
        self.fuente_titulo.configure(size=tam + 4)
        self.fuente_mono.configure(size=tam)
        tema.aplicar(self.root, tam)

    def _construir(self):
        r = self.root
        r.resizable(False, False)
        r.attributes("-topmost", bool(self.cfg["siempre_visible"]))
        f = ttk.Frame(r, padding=(18, 14, 18, 12))
        f.pack(fill="both", expand=True)
        ttk.Label(f, text="Traductor de Pantalla", style="Titulo.TLabel").pack(anchor="w")
        self.lbl_sub = ttk.Label(f, style="Tenue.TLabel")
        self.lbl_sub.pack(anchor="w")
        ttk.Frame(f, style="Linea.TFrame", height=2).pack(fill="x", pady=(8, 12))

        self.botones = {}
        self._dobles = set()          # botones de la rejilla: nombre arriba, atajo abajo
        textos = ((("anclar", "Anclar a un juego o programa"), ("zona", "Zona en vivo"),
                   ("subtitulos", "Subtítulos de pantalla"),
                   ("voz", "Subtítulos de voz"), ("pantalla", "Pantalla completa"),
                   ("repetir", "Volver a la última zona"), ("portapapeles", "Traducir lo copiado"))
                  if VIVO else
                  (("pantalla", "Traducir pantalla completa"), ("zona", "Traducir una zona"),
                   ("repetir", "Repetir la última zona"), ("portapapeles", "Traducir lo copiado"),
                   ("subtitulos", "Subtítulos de pantalla"), ("voz", "Subtítulos de voz")))
        principal = textos[0][0]
        b = ttk.Button(f, style="Grande.Acento.TButton", command=lambda: self.accion(principal))
        b.pack(fill="x", pady=(0, 3))
        self.botones[principal] = (b, textos[0][1])
        rej = ttk.Frame(f)
        rej.pack(fill="x")
        for i, (accion, texto) in enumerate(textos[1:]):
            b = ttk.Button(rej, style="Doble.TButton", command=lambda a=accion: self.accion(a))
            b.grid(row=i // 2, column=i % 2, sticky="nsew", padx=(0, 3) if i % 2 == 0 else (0, 0), pady=(0, 3))
            self.botones[accion] = (b, texto)
            self._dobles.add(accion)
        rej.columnconfigure(0, weight=1, uniform="a")
        rej.columnconfigure(1, weight=1, uniform="a")
        b = ttk.Button(f, style="TButton", command=self.detener)
        b.pack(fill="x", pady=(0, 10))
        self.botones["detener"] = (b, "■ Detener")
        self._textos_botones()

        ttk.Label(f, text="ESTILO EN PANTALLA", style="Seccion.TLabel").pack(anchor="w", pady=(2, 3))
        est = ttk.Frame(f)
        est.pack(fill="x")
        self.v_estilo = tk.StringVar(value=self._estilo_actual())
        for i, (t, v) in enumerate((("Natural", "natural"), ("Vidrio", "vidrio"), ("Caja", "caja"),
                                    ("Panel", "panel"))):
            ttk.Radiobutton(est, text=t, value=v, variable=self.v_estilo, style="Toolbutton"
                            ).grid(row=0, column=i, sticky="we", padx=1)
            est.columnconfigure(i, weight=1, uniform="e")
        self.v_estilo.trace_add("write", lambda *_: self._cambiar_estilo_pantalla())
        self.lbl_estilo = ttk.Label(f, style="Tenue.TLabel", wraplength=470, justify="left")
        self.lbl_estilo.pack(anchor="w", pady=(4, 8))

        ttk.Label(f, text="IDIOMAS", style="Seccion.TLabel").pack(anchor="w", pady=(2, 3))
        idi = ttk.Frame(f)
        idi.pack(fill="x")
        self.v_idioma = tk.StringVar(value=self.cfg["idioma_origen"])
        self._origenes = {"Detectar (inglés y japonés)": "auto", "Inglés": "en", "Japonés": "ja"}
        self.cb_origen = ttk.Combobox(idi, state="readonly", width=24, values=list(self._origenes))
        self.cb_origen.pack(side="left")
        self.cb_origen.bind("<<ComboboxSelected>>",
                            lambda e: self.v_idioma.set(self._origenes[self.cb_origen.get()]))
        self.v_idioma.trace_add("write", lambda *_: self._origen_cambiado())
        ttk.Label(idi, text="→", style="Tenue.TLabel").pack(side="left", padx=8)
        import traduccion
        self._idiomas_dest = {v: k for k, v in traduccion.IDIOMAS_DESTINO.items()}
        self.cb_destino = ttk.Combobox(idi, state="readonly", width=16,
                                       values=list(traduccion.IDIOMAS_DESTINO.values()))
        self.cb_destino.set(traduccion.IDIOMAS_DESTINO.get(self.cfg["idioma_destino"], "Español"))
        self.cb_destino.pack(side="left", fill="x", expand=True)
        self.cb_destino.bind("<<ComboboxSelected>>", lambda e: self._cambiar_destino())
        self.lbl_motor = ttk.Label(f, style="Tenue.TLabel")
        self.lbl_motor.pack(anchor="w", pady=(3, 8))
        self._sincronizar_origen()

        ult = ttk.Frame(f)
        ult.pack(fill="x", pady=(0, 8))
        ttk.Label(ult, text="Última traducción:", style="Tenue.TLabel").pack(side="left")
        self.botones_ultima = []
        for texto, fn in (("Copiar", lambda: self.copiar_texto(ventanas.texto_de(self.ultimos_bloques))),
                          ("Leer", lambda: self.leer(ventanas.texto_de(self.ultimos_bloques))),
                          ("Ver texto", lambda: self.panel.mostrar(self.ultimos_bloques))):
            b = ttk.Button(ult, text=texto, command=fn, width=9)
            b.pack(side="left", padx=2)
            self.botones_ultima.append(b)

        fila = ttk.Frame(f)
        fila.pack(fill="x")
        for i, (texto, fn) in enumerate((("Historial", self.abrir_historial),
                                         ("Mi glosario", self.abrir_glosario),
                                         ("Ajustes", self.abrir_config),
                                         ("Ayuda", lambda: ventanas.ventana_ayuda(self)),
                                         ("Actualizar", lambda: self.buscar_actualizacion(True)),
                                         ("Salir", self.salir))):
            ttk.Button(fila, text=texto, command=fn).grid(row=i // 3, column=i % 3, sticky="we",
                                                          padx=1, pady=1)
        for c in range(3):
            fila.columnconfigure(c, weight=1, uniform="b")

        self.btn_novedad = ttk.Button(f, style="Grande.Acento.TButton", command=self._instalar_novedad)
        self.novedad = None
        self.barra_prog = ttk.Progressbar(f, mode="indeterminate")
        self.barra_prog.pack(fill="x", pady=(10, 4))
        self.lbl_estado = ttk.Label(f, text="Iniciando…", wraplength=470, justify="left")
        self.lbl_estado.pack(anchor="w")

        # teclado: Enter pulsa el botón enfocado
        r.bind_class("TButton", "<Return>", lambda e: e.widget.invoke())
        r.bind_class("TRadiobutton", "<Return>", lambda e: e.widget.invoke())
        r.bind_class("TCheckbutton", "<Return>", lambda e: e.widget.invoke())
        r.bind("<F1>", lambda e: ventanas.ventana_ayuda(self))
        self._explicar_estilo()
        self._actualizar_botones()

    # ---------- estilo en pantalla e idiomas ----------
    def _estilo_actual(self):
        if self.cfg["modo_vista"] == "panel":
            return "panel"
        return self.cfg.get("modo_texto", "natural")

    _EXPLICA = {"natural": "Reemplaza el texto con la letra, el color y el contorno del juego.",
                "vidrio": "Desenfoca el original y escribe encima (fondos que se mueven).",
                "caja": "Recuadro de color clásico (colores en Ajustes → Caja y panel).",
                "panel": "La traducción va a una ventana de texto aparte."}

    def _explicar_estilo(self):
        self.lbl_estilo.configure(text=self._EXPLICA.get(self.v_estilo.get(), ""))

    def _cambiar_estilo_pantalla(self):
        v = self.v_estilo.get()
        if v == "panel":
            self.cfg["modo_vista"] = "panel"
        else:
            self.cfg["modo_vista"] = "superpuesto"
            self.cfg["modo_texto"] = v
        self.guardar_cfg()
        self._explicar_estilo()
        if self.vivo is not None:
            self.vivo.aplicar_modo()

    def _sincronizar_origen(self):
        nombre = {v: k for k, v in self._origenes.items()}.get(self.v_idioma.get(), "Detectar (inglés y japonés)")
        if self.cb_origen.get() != nombre:
            self.cb_origen.set(nombre)
        destino = self.cb_destino.get() if hasattr(self, "cb_destino") else "Español"
        origen = {"auto": "Inglés · Japonés", "en": "Inglés", "ja": "Japonés"}[self.v_idioma.get()]
        self.lbl_sub.configure(text=f"{origen}  →  {destino}")

    def _origen_cambiado(self):
        self._cambiar("idioma_origen", self.v_idioma.get())
        self._sincronizar_origen()

    def _textos_botones(self):
        for accion, (b, texto) in self.botones.items():
            atajo = atajo_legible(self.cfg["atajos"][accion]) if self.cfg["atajos_activos"] else ""
            if accion in getattr(self, "_dobles", ()):
                b.configure(text=f"{texto}\n{atajo}" if atajo else texto)
            else:
                b.configure(text=f"{texto}    ({atajo})" if atajo else texto)

    def _actualizar_botones(self):
        activo = self.listo and not self.ocupado
        for accion in ("pantalla", "zona", "portapapeles", "anclar"):
            if accion in self.botones:
                self.botones[accion][0].state(["!disabled"] if activo else ["disabled"])
        self.botones["repetir"][0].state(["!disabled"] if activo and self.ultima_zona else ["disabled"])
        hay_algo = (self.ocupado or self.voz.hablando() or self._overlay_abierto() or self.selector
                    or self.vivo is not None or self.voz_subs is not None)
        self.botones["voz"][0].state(["!disabled"] if self.listo else ["disabled"])
        self.botones["subtitulos"][0].state(["!disabled"] if self.listo and not self.ocupado else ["disabled"])
        for b in self.botones_ultima:
            b.state(["!disabled"] if self.ultimos_bloques else ["disabled"])
        self.botones["detener"][0].state(["!disabled"] if hay_algo else ["disabled"])
        if self.ocupado != getattr(self, "_animando", False):
            self._animando = self.ocupado
            if self.ocupado:
                self.barra_prog.start(12)
            else:
                self.barra_prog.stop()

    def estado(self, texto, error=False):
        self.lbl_estado.configure(text=texto, foreground=tema.ERROR if error else tema.HUESO)

    def _cambiar(self, clave, valor):
        self.cfg[clave] = valor
        self.guardar_cfg()

    def guardar_cfg(self):
        try:
            nucleo.guardar_config(self.cfg)
        except Exception:
            log.exception("No se pudo guardar config.json")

    # ======================================================================
    # Carga, atajos y bucle de eventos
    # ======================================================================
    def _cargar_motor(self):
        try:
            t0 = time.time()
            self.motor.cargar()
            self.cola.put(("listo", time.time() - t0))
        except Exception as e:
            log.exception("Fallo al cargar el motor")
            if "Download failed" in str(e) or "URLError" in repr(e) or "Connection" in repr(e):
                msg = ("No pude descargar los idiomas. Conéctate a internet y vuelve a abrir "
                       "el traductor (solo hace falta la primera vez).")
            else:
                msg = f"No se pudo cargar: {e}"
            self.cola.put(("error", msg + "\n(detalles en registro.txt)"))

    def _registrar_atajos(self):
        if self._hk is not None:
            try:
                self._hk.stop()
            except Exception:
                pass
            self._hk = None
        if not self.cfg["atajos_activos"]:
            return
        try:
            from pynput import keyboard
            mapa = {atajo_pynput(self.cfg["atajos"][a]): (lambda a=a: self.cola.put(("atajo", a)))
                    for a in ACCIONES if a not in ACCIONES_MANTENER}
            self._hk = keyboard.GlobalHotKeys(mapa)
            self._hk.daemon = True
            self._hk.start()
        except Exception as e:
            log.exception("Atajos globales no disponibles")
            self.cola.put(("estado", f"Atajos globales no disponibles ({e}). Usa los botones."))

    def _revisar_cola(self):
        try:
            while True:
                tipo, dato = self.cola.get_nowait()
                if tipo == "estado":
                    self.estado(dato)
                elif tipo == "novedad":
                    self._mostrar_novedad(*dato)
                elif tipo == "listo":
                    self.root.after(4000, lambda: self.buscar_actualizacion(False))
                    self.listo = True
                    self.estado(f"Listo ({dato:.0f} s). {self._info_lector()}  F1 = ayuda")
                elif tipo == "error":
                    self.ocupado = False
                    self.estado(dato, error=True)
                elif tipo == "atajo":
                    self.accion(dato)
                elif tipo == "resultado":
                    self._mostrar_resultado(*dato)
                elif tipo == "resultado_texto":
                    self._mostrar_texto(*dato)
                elif tipo == "vivo":
                    self._resultado_vivo(*dato)
                elif tipo == "mostrar":
                    self.mostrar_principal()
                elif tipo == "abrir":
                    {"historial": self.abrir_historial, "config": self.abrir_config,
                     "salir": self.salir}.get(dato, lambda: None)()
                elif tipo == "voz":
                    self._resultado_voz(*dato)
                elif tipo == "voz_estado":
                    sesion, texto = dato
                    if sesion is self.voz_subs:
                        self.estado(texto)
                        if self.barra_voz is not None and self.barra_voz.existe():
                            self.barra_voz.estado(texto if len(texto) < 90 else texto[:87] + "…")
                        if not sesion.activo:
                            self.detener_voz()
                elif tipo == "vivo_cerrada":
                    if dato is self.vivo:
                        self.detener_vivo()
                        self.estado("El programa anclado se cerró: traducción detenida.")
                elif tipo == "vivo_error":
                    sesion, msg = dato
                    sesion.error()
                    if sesion is self.vivo:
                        self.estado(f"Error al leer: {msg}", error=True)
        except queue.Empty:
            pass
        self._actualizar_botones()
        self.root.after(100, self._revisar_cola)

    def _error_tk(self, exc, val, tb):
        log.error("Error en la interfaz", exc_info=(exc, val, tb))
        self.ocupado = False
        self.estado(f"Error: {val}  (detalles en registro.txt)", error=True)
        try:
            self.root.deiconify()
        except Exception:
            pass

    # ======================================================================
    # Acciones
    # ======================================================================
    def accion(self, accion):
        if accion == "detener":
            self.detener()
            return
        if accion == "menu":
            self.menu_rapido()
            return
        if accion in ACCIONES_MANTENER:
            return
        if not self.listo:
            self.estado("Todavía estoy cargando, un momento…")
            return
        if self.ocupado or self.selector:
            return
        if accion == "repetir" and not self.ultima_zona:
            self.estado("Primero elige una zona con «Traducir una zona».")
            return
        if accion == "portapapeles":
            self._traducir_copiado()
            return
        if VIVO or accion == "voz":
            self._accion_vivo(accion)
            return
        self.fuente_actual = accion
        if self._overlay_abierto():
            self.overlay.cerrar()
        self.voz.detener()
        self.ocupado = True
        self.trabajo += 1
        self._panel_estaba = self.panel.visible()
        self.panel.ocultar()
        self.root.withdraw()
        self.root.after(300, lambda: self._capturar(accion, self.trabajo))

    # ---------- modo en vivo ----------
    def _accion_vivo(self, accion):
        # pulsar otra vez el mismo atajo apaga la traducción en vivo
        if accion == "voz":
            if self.voz_subs is not None:
                self.detener_voz()
                self.estado("Subtítulos de voz apagados.")
            else:
                self.iniciar_voz()
            return
        if self.vivo is not None and (
                (accion == "pantalla" and self.vivo.tipo == "pantalla") or
                (accion == "repetir" and self.ultima_zona and self.vivo.caja == self.ultima_zona[1])):
            self.detener_vivo()
            self.estado("Traducción en vivo detenida.")
            return
        if accion == "pantalla":
            import mss
            with mss.mss() as sct:
                mon = self._monitor_actual(sct)
            self.iniciar_vivo(mon, None)
        elif accion == "repetir":
            mon, caja = self.ultima_zona
            hw = getattr(self, "ultima_ventana", None)
            if hw:
                rect = envivo.rect_cliente(hw)
                if rect is None:
                    self.estado("El programa anclado ya no está abierto.")
                    return
                mon = rect
            self.iniciar_vivo(mon, caja, estilo=self.ultimo_estilo, ventana=hw)
        elif accion == "anclar":
            self._anclar()
        else:                                       # zona / subtítulos: congelar y elegir
            self._estilo_zona = "barra" if accion == "subtitulos" else "palabras"
            self.detener_vivo()
            self.voz.detener()
            self.ocupado = True
            self.trabajo += 1
            self._panel_estaba = self.panel.visible()
            self.panel.ocultar()
            self.root.withdraw()
            self.root.after(250, lambda: self._capturar("zona", self.trabajo))

    def iniciar_vivo(self, mon, caja, estilo="palabras", ventana=None, desde_perfil=False):
        self.detener_vivo()
        self.ultimo_estilo = estilo
        self.ultima_ventana = ventana
        if self._overlay_abierto():
            self.overlay.cerrar()
        self.ocupado = False
        self.trabajo += 1
        self.fuente_actual = "zona" if caja else "pantalla"
        # ¿qué programa hay debajo? (para su perfil y para buscar las fuentes del juego)
        exe = None
        try:
            hw = ventana
            if hw is None and envivo.ES_WINDOWS:
                x0, y0, x1, y1 = caja if caja else (0, 0, mon["width"], mon["height"])
                hw = envivo.ventana_en(mon["left"] + (x0 + x1) / 2, mon["top"] + (y0 + y1) / 2)
                if hw in {envivo.hwnd_de(w) for w in self._mis_ventanas()}:
                    hw = None
            exe = envivo.exe_de_ventana(hw) if hw else None
            if exe and os.path.normcase(exe) == os.path.normcase(sys.executable):
                exe = None
        except Exception:
            log.exception("Buscando el programa de la zona")
        perfil = self.perfiles.get(nucleo.clave_perfil(exe), {}) if exe else {}
        if ventana and exe:
            self._guardar_perfil(exe, ventana, mon, caja, estilo)
        try:
            self.vivo = envivo.SesionEnVivo(self, mon, caja, estilo=estilo, ventana=ventana,
                                            capturar=getattr(self, "capturar_vivo", None),
                                            ruta_exe=exe, fuente=perfil.get("fuente"),
                                            modo_texto=perfil.get("modo_texto"))
        except Exception as e:
            log.exception("No se pudo iniciar el modo en vivo")
            self.vivo = None
            self.estado(f"No se pudo iniciar la traducción en vivo: {e}", error=True)
            return
        self._ocultar_mis_ventanas(True)
        que = "los subtítulos" if estilo == "barra" else ("la zona elegida" if caja else "la pantalla")
        if ventana:
            que += f" de «{self.vivo.titulo[:40]}» (anclado: se oculta si cambias de programa)"
        if desde_perfil:
            self.estado(f"● Perfil de «{self.vivo.titulo[:40]}»: traducción automática. "
                        f"{atajo_legible(self.cfg['atajos']['detener'])} la detiene.")
        else:
            self.estado(f"● EN VIVO: leyendo {que}… Sigue usando tus programas con normalidad.")

    # ---------- perfiles por juego ----------
    def _guardar_perfil(self, exe, ventana, mon, caja, estilo):
        clave = nucleo.clave_perfil(exe)
        p = dict(self.perfiles.get(clave, {}))
        p.setdefault("auto", True)
        p.update({"exe": exe, "nombre": envivo.titulo_ventana(ventana)[:60] or os.path.basename(exe),
                  "zona": [int(v) for v in caja] if caja else None,
                  "tam": [int(mon["width"]), int(mon["height"])], "estilo": estilo, "usado": time.time()})
        self.perfiles[clave] = p
        nucleo.guardar_perfiles(self.perfiles)

    def _vigilar_perfiles(self):
        """Si abres (o vuelves a) un juego con perfil, la traducción empieza sola."""
        try:
            self._revisar_perfiles()
        except Exception:
            log.exception("Perfiles automáticos")
        self.root.after(1500, self._vigilar_perfiles)

    def _revisar_perfiles(self):
        if not (envivo.ES_WINDOWS and VIVO and self.listo and self.cfg.get("perfiles_auto", True)):
            return
        import ctypes
        self._auto_suprimidas = {h for h in self._auto_suprimidas if ctypes.windll.user32.IsWindow(h)}
        if (self.vivo is not None or self.ocupado or self.selector or self.voz_subs is not None
                or not self.perfiles):
            return
        hw = envivo.ventana_activa()
        if not hw or hw in self._auto_suprimidas or hw in {envivo.hwnd_de(w) for w in self._mis_ventanas()}:
            return
        exe = envivo.exe_de_ventana(hw)
        p = self.perfiles.get(nucleo.clave_perfil(exe)) if exe else None
        if not p or not p.get("auto", True):
            return
        rect = envivo.rect_cliente(hw)
        if not rect or rect["width"] < 160 or rect["height"] < 120:
            return
        caja = None
        if p.get("zona"):
            ancho, alto = p.get("tam") or (rect["width"], rect["height"])
            sx, sy = rect["width"] / max(1, ancho), rect["height"] / max(1, alto)
            x0, y0, x1, y1 = p["zona"]
            caja = (x0 * sx, y0 * sy, x1 * sx, y1 * sy)
        self.ultima_zona = (rect, caja)
        self.iniciar_vivo(rect, caja, estilo=p.get("estilo", "palabras"), ventana=hw, desde_perfil=True)

    # ---------- mantener una tecla para ver el original ----------
    def _vigilar_tecla_original(self):
        try:
            pulsada = (self.vivo is not None and self.cfg["atajos_activos"]
                       and envivo.atajo_pulsado(self.cfg["atajos"].get("original")))
            if pulsada != self._viendo_original:
                self._viendo_original = pulsada
                if self.vivo is not None:
                    self.vivo.ver_original(pulsada)
        except Exception:
            log.exception("Tecla para ver el original")
        self.root.after(50, self._vigilar_tecla_original)

    # ---------- menú rápido e icono de la bandeja ----------
    def menu_rapido(self):
        if self.menu_win is not None and self.menu_win.existe():
            self.menu_win.cerrar()
            return
        previa = envivo.ventana_activa()
        if previa in {envivo.hwnd_de(w) for w in self._mis_ventanas()}:
            previa = None
        self.menu_win = ventanas.MenuRapido(self, previa)

    def anclar_ventana(self, hw):
        """Anclar a una ventana concreta (desde el menú rápido)."""
        if hw and envivo.ES_WINDOWS and not envivo.es_del_escritorio(hw) and envivo.rect_cliente(hw):
            self._anclar_a(hw)
        else:
            self._anclar()

    def mostrar_principal(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def _poner_bandeja(self):
        if self.bandeja is not None or not self.cfg.get("bandeja", True):
            return
        try:
            import bandeja
            self.bandeja = bandeja.Bandeja(self)
        except Exception as e:
            log.warning("Icono de la bandeja no disponible: %s", e)
            self.bandeja = None

    def _quitar_bandeja(self):
        if self.bandeja is not None:
            self.bandeja.cerrar()
            self.bandeja = None

    # ---------- corregir una traducción ----------
    def corregir_traduccion(self, entrada, nueva):
        nueva = nueva.strip()
        if not nueva:
            return
        self.motor.corregir(entrada["original"], entrada["idioma"], nueva)
        entrada["traduccion"] = nueva
        self.historial.guardar()
        self._historial_cambio()
        if self.vivo is not None:
            self.vivo.seguidor.olvidar_traducciones(entrada["original"])
            self.vivo.ref = None
        self.estado("Corrección guardada: se usará siempre para esa frase.")

    def _mis_ventanas(self):
        return [self.root] + [w for w in self.root.winfo_children()
                              if isinstance(w, tk.Toplevel) and getattr(w, "es_del_traductor", False)]

    def _ocultar_mis_ventanas(self, ocultar):
        """Mientras hay traducción en vivo, las ventanas del traductor no salen en las
        capturas (así no se lee a sí mismo si quedan encima de la zona)."""
        if not envivo.ES_WINDOWS or getattr(self, "sin_exclusion", False):
            return
        for w in self._mis_ventanas():
            try:
                if w.winfo_ismapped() or not ocultar:
                    envivo.excluir_de_captura(w, ocultar)
            except tk.TclError:
                pass

    def detener_vivo(self):
        if self.vivo is not None:
            self.vivo.detener()
            self.vivo = None
            self._ocultar_mis_ventanas(False)
            return True
        return False

    def _resultado_vivo(self, sesion, bloques, nuevos, duracion, piezas=None):
        if sesion is not self.vivo:
            sesion.procesando = False
            return
        nuevos = sesion.resultado(bloques, nuevos, duracion, piezas)
        if bloques:
            self.ultimos_bloques = bloques
        if nuevos:
            self.historial.agregar(nuevos, sesion.tipo)
            if self.cfg["voz_auto"]:
                self.leer(ventanas.texto_de(nuevos))
        if self.cfg["modo_vista"] == "panel" and bloques and nuevos:
            self.panel.mostrar(bloques, enfocar=False)
        if sesion.lecturas <= 1 or nuevos:
            self._ocultar_mis_ventanas(True)     # por si se abrió una ventana nueva
        que = {"subtitulos": "subtítulos", "zona": "zona", "pantalla": "pantalla"}[sesion.tipo]
        if sesion.ventana:
            que += f" de {sesion.titulo[:30]}"
        detalle = self._resumen(bloques) if bloques else "sin texto para traducir"
        leer, trad = self.motor.tiempos
        self.estado(f"● EN VIVO ({que}): {detalle}\nlectura {leer:.2f} s + traducción {trad:.2f} s · "
                    f"se actualiza sola.")
        self._mostrar_motor()

    # ---------- subtítulos de voz ----------
    def iniciar_voz(self):
        import subtitulos_voz
        import mss
        with mss.mss() as sct:
            mon = self._monitor_actual(sct)
        try:
            self.barra_voz = envivo.BarraSubtitulos(self, mon)
            self.voz_subs = subtitulos_voz.SubtitulosDeVoz(self)
            self.voz_subs.iniciar()
        except Exception as e:
            log.exception("Subtítulos de voz")
            self.detener_voz()
            self.estado(f"No se pudieron iniciar los subtítulos de voz: {e}", error=True)
            return
        self.estado("● Subtítulos de voz: preparando… (Ctrl+Alt+A para apagar)")

    def detener_voz(self):
        if self.voz_subs is not None:
            self.voz_subs.detener()
            self.voz_subs = None
        if self.barra_voz is not None:
            self.barra_voz.cerrar()
            self.barra_voz = None

    def _resultado_voz(self, sesion, r):
        if sesion is not self.voz_subs:
            return
        if self.barra_voz is not None and self.barra_voz.existe():
            self.barra_voz.mostrar(r["traduccion"], r["original"])
        bloque = {"caja": None, "original": r["original"], "traduccion": r["traduccion"], "idioma": r["idioma"]}
        self.ultimos_bloques = [bloque]
        if r.get("reemplaza"):
            self.historial.quitar_original(r["reemplaza"])
        self.historial.agregar([bloque], "voz")
        self.estado(f"● Subtítulos de voz ({'inglés' if r['idioma'] == 'en' else 'japonés'}): "
                    f"apareció {r['demora']:.1f} s después de terminar la frase.")
        self._mostrar_motor()

    # ---------- actualizaciones ligeras ----------
    def buscar_actualizacion(self, manual):
        if manual:
            self.estado("Buscando actualizaciones…")

        def tarea():
            try:
                import actualizar
                self.cola.put(("novedad", (actualizar.buscar(), manual, None)))
            except Exception as e:
                log.warning("Buscar actualización: %s", e)
                self.cola.put(("novedad", (None, manual, str(e))))
        threading.Thread(target=tarea, daemon=True).start()

    def _mostrar_novedad(self, novedad, manual, error):
        import version
        if error:
            if manual:
                self.estado(f"No pude revisar si hay actualizaciones ({error}).", error=True)
            return
        if not novedad:
            if manual:
                self.estado(f"Ya tienes la última versión ({version.VERSION}).")
            return
        self.novedad = novedad
        self.btn_novedad.configure(text=f"⬆ Actualizar a la versión {novedad['version']}")
        self.btn_novedad.pack(fill="x", pady=(8, 0), before=self.barra_prog)
        self.estado(f"Hay una versión nueva ({novedad['version']})."
                    + (" Necesita descargar el paquete completo." if novedad["necesita_base"]
                       else " Es una actualización pequeña: un clic y listo."))

    def _instalar_novedad(self):
        n = self.novedad
        if not n:
            return
        if n["necesita_base"]:
            if messagebox.askyesno("Actualización", f"La versión {n['version']} trae librerías nuevas: hay "
                                   "que descargar el paquete completo una vez.\n\n¿Abrir la página de "
                                   "descarga?", parent=self.root):
                import webbrowser
                webbrowser.open(n["pagina"])
            return
        if not messagebox.askyesno("Actualización", f"¿Instalar la versión {n['version']} ahora?\n"
                                   "El traductor se cerrará y se volverá a abrir solo.", parent=self.root):
            return
        self.estado("Descargando la actualización…")
        self.root.update_idletasks()
        try:
            import actualizar
            actualizar.descargar_y_aplicar(n)
        except Exception as e:
            log.exception("Aplicando actualización")
            self.estado(f"No se pudo actualizar: {e}", error=True)
            return
        self.detener_vivo()
        self.detener_voz()
        self.historial.guardar()
        self.guardar_cfg()
        global CANDADO
        if CANDADO is not None:
            CANDADO.close()
        actualizar.reiniciar()

    # ---------- idioma de destino ----------
    def _cambiar_destino(self):
        codigo = self._idiomas_dest.get(self.cb_destino.get(), "es")
        self._cambiar("idioma_destino", codigo)
        self._sincronizar_origen()
        self.estado(f"Ahora se traduce a {self.cb_destino.get()}.")
        if self.listo and self.motor.traductor is not None:
            argos = self.motor.traductor.argos

            def preparar():   # diccionario sin internet para ese idioma (respaldo)
                try:
                    if not argos.disponible("en", codigo) or not argos.disponible("ja", codigo):
                        for origen in ("en", "ja"):
                            argos.descargar(origen, codigo, lambda m: self.cola.put(("estado", m)))
                        self.cola.put(("estado", f"Listo también sin internet: {self.cb_destino.get()}."))
                except Exception:
                    log.exception("Descargando diccionario sin internet")
            threading.Thread(target=preparar, daemon=True).start()

    def _mostrar_motor(self):
        t = self.motor.traductor
        if t is not None and t.ultimo_motor:
            self.lbl_motor.configure(text=f"Traducción: {t.ultimo_motor}")

    def _info_lector(self):
        lec = self.motor.lector
        if lec is None:
            return ""
        if getattr(lec, "faltan", None):
            return (f"Falta el lector de {' y '.join(lec.faltan)} de Windows: "
                    f"Configuración → «Instalar lectores».")
        return f"Lector: {lec.nombre}."

    def detener(self):
        if self.voz_subs is not None:
            self.detener_voz()
            self.estado("Subtítulos de voz apagados.")
            if self.vivo is None:
                return
        if self.vivo is not None and self.vivo.ventana:
            self._auto_suprimidas.add(self.vivo.ventana)     # no volver a empezar sola en esa ventana
        if self.detener_vivo():
            self.trabajo += 1
            self.voz.detener()
            self.estado("Traducción en vivo detenida.")
            return
        algo = self.ocupado or self.selector or self._overlay_abierto() or self.voz.hablando()
        self.trabajo += 1           # cualquier traducción en curso queda descartada
        self.ocupado = False
        self.voz.detener()
        if self.selector is not None:
            try:
                self.selector.destroy()
            except tk.TclError:
                pass
            self.selector = None
        if self._overlay_abierto():
            self.overlay.cerrar()
        self._restaurar_ventanas()
        if algo:
            self.estado("Detenido.")

    def _restaurar_ventanas(self):
        self.root.deiconify()
        if self._panel_estaba and self.panel.abierto():
            self.panel.win.deiconify()
        self._panel_estaba = False

    def _monitor_actual(self, sct):
        px, py = self.root.winfo_pointerxy()
        for m in sct.monitors[1:]:
            if m["left"] <= px < m["left"] + m["width"] and m["top"] <= py < m["top"] + m["height"]:
                return dict(m)
        return dict(sct.monitors[1])

    def _capturar(self, accion, trabajo):
        if trabajo != self.trabajo:
            return
        try:
            import mss
            with mss.mss() as sct:
                mon = self.ultima_zona[0] if accion == "repetir" else self._monitor_actual(sct)
                shot = sct.grab(mon)
            img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
            if img.size != (mon["width"], mon["height"]):   # pantallas Retina / HiDPI
                img = img.resize((mon["width"], mon["height"]), Image.LANCZOS)
        except Exception as e:
            log.exception("Captura")
            self.ocupado = False
            self._restaurar_ventanas()
            self.estado(f"No pude capturar la pantalla: {e}", error=True)
            return
        if accion == "zona":
            self._elegir_zona(img, mon, trabajo)
        elif accion == "repetir":
            self._procesar(img, mon, self.ultima_zona[1], trabajo)
        else:
            self._procesar(img, mon, None, trabajo)

    # ---------- anclar a un programa ----------
    def _anclar(self):
        if not envivo.ES_WINDOWS:
            self.estado("Anclar a un programa solo funciona en Windows.")
            return
        propias = {envivo.hwnd_de(w) for w in self._mis_ventanas()}
        hw = envivo.ventana_activa()
        if hw is None or hw in propias or envivo.es_del_escritorio(hw):
            # Se pulsó el botón: esperar a que el usuario haga clic en el programa
            self.estado("Haz clic en la ventana del programa o juego que quieres traducir…")
            self._esperar_ventana(time.time() + 20, propias)
            return
        self._anclar_a(hw)

    def _esperar_ventana(self, limite, propias):
        hw = envivo.ventana_activa()
        if hw and hw not in propias and not envivo.es_del_escritorio(hw) and envivo.rect_cliente(hw):
            self._anclar_a(hw)
            return
        if time.time() > limite:
            self.estado("No elegiste ningún programa. Vuelve a intentarlo.")
            return
        self.root.after(200, lambda: self._esperar_ventana(limite, propias))

    def _anclar_a(self, hw):
        rect = envivo.rect_cliente(hw)
        if not rect:
            self.estado("No pude usar esa ventana (¿está minimizada?).", error=True)
            return
        self.detener_vivo()
        self.voz.detener()
        self.ocupado = True
        self.trabajo += 1
        trabajo = self.trabajo
        self._estilo_zona = "palabras"
        self._panel_estaba = self.panel.visible()
        self.panel.ocultar()
        self.root.withdraw()

        def capturar():
            if trabajo != self.trabajo:
                return
            try:
                import mss
                with mss.mss() as sct:
                    shot = sct.grab(rect)
                img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
                if img.size != (rect["width"], rect["height"]):
                    img = img.resize((rect["width"], rect["height"]), Image.LANCZOS)
            except Exception as e:
                log.exception("Captura de la ventana")
                self.ocupado = False
                self._restaurar_ventanas()
                self.estado(f"No pude capturar la ventana: {e}", error=True)
                return
            self._elegir_zona(img, rect, trabajo, ventana=hw)
        self.root.after(250, capturar)

    def _elegir_zona(self, img, mon, trabajo, ventana=None):
        self.ocupado = False          # mientras eliges, no hay nada procesándose
        w = self.selector = tk.Toplevel(self.root)
        w.overrideredirect(True)
        w.geometry(f"{mon['width']}x{mon['height']}+{mon['left']}+{mon['top']}")
        w.attributes("-topmost", True)
        oscura = ImageEnhance.Brightness(img).enhance(0.5)
        foto = ImageTk.PhotoImage(oscura, master=w)
        c = tk.Canvas(w, highlightthickness=0, cursor="crosshair", bd=0)
        c.pack(fill="both", expand=True)
        c.create_image(0, 0, image=foto, anchor="nw")
        c.foto = foto
        aviso = "Arrastra con el ratón para elegir la zona   ·   Esc o clic derecho para cancelar"
        if ventana:
            aviso = "Arrastra para elegir una zona  ·  Enter = toda la ventana  ·  Esc cancelar"
        tam = max(14, self.fuente_titulo.actual("size"))
        c.create_rectangle(mon["width"] // 2 - 380, 14, mon["width"] // 2 + 380, 14 + tam * 3,
                           fill="#111", outline="#ffd400")
        c.create_text(mon["width"] // 2, 14 + tam * 1.5, text=aviso, fill="white",
                      font=(self.fuente_ui.actual("family"), tam, "bold"))
        estado = {"ini": None, "rect": None, "ret": None}
        brillante = img

        def abajo(e):
            estado["ini"] = (e.x, e.y)
            estado["rect"] = c.create_rectangle(e.x, e.y, e.x, e.y, outline="#ffd400", width=3)

        def mover(e):
            if estado["rect"]:
                x, y = estado["ini"]
                c.coords(estado["rect"], x, y, e.x, e.y)
                # mostrar la zona elegida sin oscurecer
                caja = (min(x, e.x), min(y, e.y), max(x, e.x), max(y, e.y))
                if caja[2] - caja[0] > 2 and caja[3] - caja[1] > 2:
                    estado["ret"] = ImageTk.PhotoImage(brillante.crop(caja), master=w)
                    c.delete("zona")
                    c.create_image(caja[0], caja[1], image=estado["ret"], anchor="nw", tags="zona")
                    c.tag_raise(estado["rect"])

        def arriba(e):
            if not estado["ini"]:
                return
            x0, y0 = estado["ini"]
            caja = (min(x0, e.x), min(y0, e.y), max(x0, e.x), max(y0, e.y))
            cerrar_selector()
            if caja[2] - caja[0] > 10 and caja[3] - caja[1] > 10:
                self.ultima_zona = (mon, caja)
                if VIVO:
                    self._restaurar_ventanas()
                    self.iniciar_vivo(mon, caja, estilo=self._estilo_zona, ventana=ventana)
                    return
                self.ocupado = True
                self._procesar(img, mon, caja, trabajo)
            else:
                self._restaurar_ventanas()
                self.estado("Zona demasiado pequeña. Inténtalo de nuevo.")

        def cerrar_selector():
            try:
                w.destroy()
            except tk.TclError:
                pass
            self.selector = None

        def cancelar(_=None):
            cerrar_selector()
            self._restaurar_ventanas()
            self.estado("Selección cancelada.")

        c.bind("<ButtonPress-1>", abajo)
        c.bind("<B1-Motion>", mover)
        c.bind("<ButtonRelease-1>", arriba)
        c.bind("<Button-3>", cancelar)
        w.bind("<Escape>", cancelar)

        def toda(_=None):
            cerrar_selector()
            self.ultima_zona = (mon, None)
            self._restaurar_ventanas()
            self.iniciar_vivo(mon, None, estilo=self._estilo_zona, ventana=ventana)
        if ventana:
            w.bind("<Return>", toda)
        w.focus_force()
        w.after(120, w.focus_force)

    def _procesar(self, img, mon, caja, trabajo):
        self.root.deiconify()
        self.estado("Leyendo y traduciendo…  (puedes pulsar Detener)")
        modo = self.cfg["idioma_origen"]
        omitir = self.cfg["omitir_espanol"]

        def tarea():
            t0 = time.time()
            try:
                recorte = img.crop(caja) if caja else img
                bloques = self.motor.procesar(recorte, modo, omitir,
                                              seguir=lambda: trabajo == self.trabajo)
                if caja:
                    dx, dy = caja[0], caja[1]
                    for b in bloques:
                        a, b_, c_, d = b["caja"]
                        b["caja"] = (a + dx, b_ + dy, c_ + dx, d + dy)
                self.cola.put(("resultado", (trabajo, img, mon, bloques, caja, time.time() - t0)))
            except Cancelado:
                pass
            except Exception as e:
                log.exception("Procesando")
                if trabajo == self.trabajo:
                    self.cola.put(("error", f"Error al traducir: {e}"))
        threading.Thread(target=tarea, daemon=True).start()

    # ---------- traducir lo copiado (portapapeles) ----------
    def _traducir_copiado(self):
        try:
            texto = self.root.clipboard_get()
        except tk.TclError:
            texto = ""
        if not texto.strip():
            self.estado("El portapapeles está vacío: copia un texto (Ctrl+C) y vuelve a intentarlo.")
            return
        self.voz.detener()
        self.ocupado = True
        self.trabajo += 1
        trabajo = self.trabajo
        modo, omitir = self.cfg["idioma_origen"], self.cfg["omitir_espanol"]
        aviso = " (solo los primeros 6000 caracteres)" if len(texto) > Motor.MAX_COPIADO else ""
        self.estado("Traduciendo lo copiado…" + aviso)

        def tarea():
            t0 = time.time()
            try:
                bloques = self.motor.traducir_texto(texto, modo, omitir,
                                                    seguir=lambda: trabajo == self.trabajo)
                self.cola.put(("resultado_texto", (trabajo, bloques, time.time() - t0)))
            except Cancelado:
                pass
            except Exception as e:
                log.exception("Traduciendo lo copiado")
                if trabajo == self.trabajo:
                    self.cola.put(("error", f"Error al traducir: {e}"))
        threading.Thread(target=tarea, daemon=True).start()

    def _mostrar_texto(self, trabajo, bloques, segundos):
        if trabajo != self.trabajo:
            return
        self.ocupado = False
        if not bloques:
            self.estado("Lo copiado no tiene texto en inglés ni en japonés para traducir.")
            return
        self.ultimos_bloques = bloques
        self.historial.agregar(bloques, "portapapeles")
        self.estado(f"Texto copiado: {self._resumen(bloques)} en {segundos:.1f} s.")
        self.panel.mostrar(bloques)
        if self.cfg["voz_auto"]:
            self.leer(ventanas.texto_de(bloques))

    @staticmethod
    def _resumen(bloques):
        en = sum(b["idioma"] == "en" for b in bloques)
        ja = len(bloques) - en
        partes = [f"{n} en {nombre}" for n, nombre in ((en, "inglés"), (ja, "japonés")) if n]
        return f"{len(bloques)} frase(s) traducidas (" + ", ".join(partes) + ")"

    def _mostrar_resultado(self, trabajo, img, mon, bloques, caja, segundos):
        if trabajo != self.trabajo:
            return                    # se pulsó Detener: se descarta
        self.ocupado = False
        self.ultimos_bloques = bloques
        if not bloques:
            self._restaurar_ventanas()
            self.estado("No encontré texto en inglés ni en japonés para traducir.")
            return
        self.historial.agregar(bloques, self.fuente_actual)
        self.estado(f"{self._resumen(bloques)} en {segundos:.1f} s.")
        if self.cfg["modo_vista"] == "panel":
            self._restaurar_ventanas()
            self.panel.mostrar(bloques)
        else:
            self.root.withdraw()
            final = nucleo.componer(img, bloques, self.cfg)
            if caja:
                ImageDraw.Draw(final).rectangle(caja, outline=(255, 212, 0, 255), width=2)
            self.overlay = ventanas.Superposicion(self, mon, img, final, bloques)
        if self.cfg["voz_auto"]:
            self.leer(ventanas.texto_de(bloques))

    def _overlay_abierto(self):
        return self.overlay is not None and self.overlay.existe()

    def al_cerrar_superposicion(self, ov):
        if self.overlay is ov:
            self.overlay = None
        self._restaurar_ventanas()

    # ======================================================================
    # Utilidades usadas por las ventanas
    # ======================================================================
    def copiar_texto(self, texto):
        self.root.clipboard_clear()
        self.root.clipboard_append(texto)
        self.estado("Texto copiado al portapapeles.")

    def leer(self, texto):
        if not self.voz.decir(texto, self.cfg["voz_velocidad"]):
            self.estado("No hay voz disponible en este sistema.", error=True)

    def abrir_config(self):
        if self.config_win is not None:
            self.config_win.win.lift()
            self.config_win.win.focus_force()
            return
        self.config_win = ventanas.VentanaConfig(self)

    def abrir_glosario(self, relleno=None):
        if self.glosario_win is not None:
            self.glosario_win.win.lift()
            if relleno:
                self.glosario_win.rellenar(*relleno)
            return
        self.glosario_win = ventanas.EditorGlosario(self, relleno)

    def abrir_historial(self):
        if self.historial_win is not None and self.historial_win.existe():
            self.historial_win.win.deiconify()
            self.historial_win.win.lift()
            self.historial_win.win.focus_force()
            return
        self.historial_win = ventanas.VentanaHistorial(self)

    def _historial_cambio(self):
        if self.historial_win is not None and self.historial_win.existe():
            self.historial_win.llenar()

    def aplicar_config(self, nueva):
        cambia_atajos = (nueva["atajos"] != self.cfg["atajos"] or
                         nueva["atajos_activos"] != self.cfg["atajos_activos"])
        self.cfg = nueva
        self.guardar_cfg()
        self._aplicar_tam_interfaz()
        self.root.attributes("-topmost", bool(nueva["siempre_visible"]))
        self.v_idioma.set(nueva["idioma_origen"])
        if self.v_estilo.get() != self._estilo_actual():
            self.v_estilo.set(self._estilo_actual())
        elif self.vivo is not None:
            self.vivo.aplicar_modo()
        if nueva.get("bandeja", True):
            self._poner_bandeja()
        else:
            self._quitar_bandeja()
        if cambia_atajos:
            self._registrar_atajos()
        self._textos_botones()
        self.panel.refrescar()
        self.historial.cfg = nueva
        if len(self.historial.entradas) > nueva["historial_max"]:
            self.historial.entradas = self.historial.entradas[: nueva["historial_max"]]
        self.historial.guardar()
        self._historial_cambio()
        self.estado("Configuración guardada.")

    def salir(self):
        try:
            if self._hk is not None:
                self._hk.stop()
            self._quitar_bandeja()
            self.detener_vivo()
            self.detener_voz()
            self.voz.detener()
            self.historial.guardar()
            self.guardar_cfg()
            if self.motor.memoria is not None:
                self.motor.memoria.cerrar()
            self.root.destroy()
        finally:
            os._exit(0)   # cierra también los hilos de carga que sigan activos

    def correr(self):
        self.root.mainloop()


def autoprueba():
    """Comprueba sin interfaz el lector, la traducción (en línea y sin internet), la voz y el
    modo en vivo (usado al fabricar el .exe). Escribe autoprueba.txt; código 0 si todo va bien."""
    import traceback
    import statistics
    lineas, ok = [], False
    try:
        img = Image.new("RGB", (980, 270), "white")
        d = ImageDraw.Draw(img)
        f = nucleo.cargar_fuente("Arial", 34)
        d.text((20, 20), "Welcome to the village, traveler.", font=f, fill="black")
        d.text((20, 90), "Do you want to save your progress?", font=f, fill="black")
        fj = nucleo.cargar_fuente_cjk(40)
        if fj:
            d.text((20, 180), "魔法の剣を手に入れた！", font=fj, fill="black")
        cfg = nucleo.cargar_config()
        t0 = time.time()
        m = Motor(avisar=lambda x: lineas.append("· " + x), cfg=cfg)
        m.cargar()
        lec = m.lector
        lineas.append(f"Carga: {time.time() - t0:.1f} s · lector {lec.nombre} · idiomas de Windows: "
                      f"{getattr(lec, 'etiquetas', '?')} · faltan: {lec.faltan}")
        # --- lector: velocidad ---
        veces = []
        for _ in range(6):
            t = time.perf_counter()
            filas = lec.leer(img)
            veces.append(time.perf_counter() - t)
        lineas.append(f"LECTOR: {statistics.median(veces[1:]):.3f} s por lectura (mediana) · líneas: "
                      f"{[x['t'] for x in filas]}")
        # --- pantalla completa con traducción ---
        t = time.perf_counter()
        bloques = m.procesar(img)
        lineas.append(f"Lectura + traducción: {time.perf_counter() - t:.2f} s "
                      f"(lectura {m.tiempos[0]:.2f} + traducción {m.tiempos[1]:.2f}, {m.traductor.ultimo_motor})")
        for b in bloques:
            lineas.append(f"  [{b['idioma']}] {b['original']}  =>  {b['traduccion']}")
        idiomas = {b["idioma"] for b in bloques}
        ok_lector = len(bloques) >= 2 and "en" in idiomas
        ok_japones = (not fj or not lec.tiene_japones) or "ja" in idiomas
        lineas.append(f"Inglés y japonés en la misma imagen: {'OK' if ok_japones else 'FALLO'} "
                      f"(lector japonés {'sí' if lec.tiene_japones else 'NO instalado'}, fuente {'sí' if fj else 'no'})")
        # --- motores de traducción ---
        frases = ["Welcome to the village, traveler.", "Do you want to save your progress?"]
        ok_linea = False
        for origen, textos, destino in (("en", frases, "es"), ("en", frases, "pt"),
                                        ("ja", ["魔法の剣を手に入れた！"], "es")):
            try:
                t = time.perf_counter()
                r = m.traductor.google.lote(textos, origen, destino)
                lineas.append(f"EN LÍNEA {origen}→{destino}: {time.perf_counter() - t:.2f} s  {r}")
                ok_linea = True
            except Exception as e:
                lineas.append(f"EN LÍNEA {origen}→{destino}: FALLO {e}")
        ok_offline = False
        for origen, textos in (("en", frases), ("ja", ["魔法の剣を手に入れた！"])):
            try:
                t = time.perf_counter()
                r = m.traductor.argos.lote(textos, origen, "es")
                lineas.append(f"SIN INTERNET {origen}→es: {time.perf_counter() - t:.2f} s  {r}")
                ok_offline = True
            except Exception as e:
                lineas.append(f"SIN INTERNET {origen}→es: FALLO {e}")
        ok_voz = autoprueba_voz(m, cfg, lineas)
        ok = ok_lector and ok_japones and ok_linea and ok_offline and ok_voz
        if ok and VIVO:
            ok = autoprueba_vivo(m, lineas)
        if ok and envivo.ES_WINDOWS:
            ok = autoprueba_anclado(m, lineas)
        if ok and envivo.ES_WINDOWS:
            ok = autoprueba_natural(m, lineas)
        lineas.append("RESULTADO: " + ("OK" if ok else "FALLO"))
    except Exception:
        lineas.append(traceback.format_exc())
        lineas.append("RESULTADO: FALLO")
    with open(os.path.join(nucleo.BASE, "autoprueba.txt"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lineas))
    os._exit(0 if ok else 1)


def autoprueba_voz(m, cfg, lineas):
    """Pasa un audio con voz por el mismo camino que el audio del sistema."""
    import subtitulos_voz as sv
    ruta = os.path.join(nucleo.BASE, "voz_prueba.wav")
    if not os.path.isfile(ruta):
        lineas.append("VOZ: no hay audio de prueba (se omite)")
        return True

    class AppFalsa:
        pass
    a = AppFalsa()
    a.cfg, a.motor, a.cola = cfg, m, queue.Queue()
    servicio = sv.SubtitulosDeVoz(a)
    t = time.time()
    servicio._transcriptor = sv.Transcriptor(cfg.get("modelo_voz", "base"), lambda x: None)
    lineas.append(f"VOZ: modelo cargado en {time.time() - t:.1f} s")
    audio = sv.leer_wav(ruta)
    silencio = np.zeros(int(0.8 * sv.SR), dtype=np.float32)
    audio = np.concatenate([silencio, audio, silencio])
    seg, n, resultados = sv.Segmentador(), int(sv.PASO * sv.SR), []
    for i in range(0, len(audio), n):
        for frase, hueco in seg.agregar(audio[i:i + n]):
            resultados.append(servicio.procesar_frase(frase, time.time(), hueco))
    for frase, hueco in seg.terminar():
        resultados.append(servicio.procesar_frase(frase, time.time(), hueco))
    for r in resultados:
        if r:
            lineas.append(f"  VOZ [{r['idioma']}] {r['original']}  =>  {r['traduccion']}  "
                          f"(listo {r['demora']:.2f} s después de terminar la frase)")
    finales = [r for r in resultados if r]
    reemplazados = {r["reemplaza"] for r in finales if r.get("reemplaza")}
    finales = [r for r in finales if r["original"] not in reemplazados]
    lineas.append(f"VOZ: subtítulos finales: {[r['traduccion'] for r in finales]}")
    texto = " ".join(r["original"].lower() for r in finales)
    ok = "village" in texto and "forest" in texto and texto.count("village") == 1
    lineas.append(f"VOZ: {'OK' if ok else 'FALLO'}")
    return ok


def autoprueba_anclado(m, lineas):
    """Windows: anclar a una ventana, fondo animado, mover la ventana, otro programa delante."""
    import random
    import subprocess
    lineas.append("--- Prueba ANCLADO A UN PROGRAMA ---")
    llamadas = []
    original = m.traducir_lote
    m.traducir_lote = lambda items, destino=None: (llamadas.append([t for t, _ in items]),
                                                   original(items, destino))[1]
    app = App(motor=m)
    app.cfg["modo_vista"] = "superpuesto"
    r = app.root

    def bombear(segundos, hasta=lambda: False, cada=None):
        fin = time.time() + segundos
        while time.time() < fin:
            if cada:
                cada()
            r.update()
            if hasta():
                return True
            time.sleep(0.05)
        return hasta()

    juego = tk.Toplevel(r)
    juego.title("Juego de prueba")
    juego.geometry("900x220+150+250")
    lbl = tk.Label(juego, text="Welcome to the village, traveler.", font=("Arial", 30), bg="white", fg="black")
    lbl.pack(expand=True, fill="both", padx=0, pady=0)
    caja = tk.Frame(lbl, bg="#3366aa", width=120, height=40)
    caja.place(x=760, y=160)
    bombear(1.0)
    juego.lift()
    juego.focus_force()
    bombear(0.8)
    hw = envivo.hwnd_de(juego)
    app.iniciar_vivo(envivo.rect_cliente(hw), None, ventana=hw)
    t = time.time()
    ok1 = bombear(30, lambda: app.vivo is not None and any("village" in b["original"].lower() for b in app.vivo.bloques))
    propio = [b["original"] for b in (app.vivo.bloques if app.vivo else []) if "ctrl" in b["original"].lower()]
    ok1 = ok1 and not propio
    lineas.append(f"Anclado y traducido: {'OK' if ok1 else 'FALLO'} en {time.time() - t:.1f} s "
                  f"{[b['traduccion'] for b in (app.vivo.bloques if app.vivo else [])]}")
    # fondo animado: un recuadro que cambia de color y de lugar sin parar
    n0 = len(llamadas)
    lecturas0 = app.vivo.lecturas if app.vivo else 0

    def animar():
        caja.configure(bg="#%06x" % random.randint(0, 0xFFFFFF))
        caja.place(x=random.randint(700, 780), y=random.randint(150, 170))
    bombear(5, cada=animar)
    extra = len(llamadas) - n0
    lecturas = (app.vivo.lecturas if app.vivo else 0) - lecturas0
    ok2 = extra == 0 and app.vivo is not None and bool(app.vivo.bloques)
    lineas.append(f"Fondo animado 5 s: {lecturas} lecturas, {extra} traducciones nuevas, sigue visible: "
                  f"{bool(app.vivo and app.vivo.bloques)} → {'OK' if ok2 else 'FALLO'}")
    # mover la ventana: la capa la sigue
    juego.geometry("+320+330")
    bombear(1.5)
    rect = envivo.rect_cliente(hw)
    ok3 = app.vivo is not None and app.vivo.capa.mon["left"] == rect["left"] and app.vivo.capa.mon["top"] == rect["top"]
    lineas.append(f"Mover la ventana: la traducción la sigue → {'OK' if ok3 else 'FALLO'}")
    # otro programa delante: se oculta; al volver, reaparece sin retraducir
    n1 = len(llamadas)
    bloc = subprocess.Popen(["notepad.exe"])
    oculto = bombear(8, lambda: app.vivo is not None and not getattr(app.vivo.capa, "_visible", True))
    lineas.append(f"Otro programa delante: {'se ocultó (OK)' if oculto else 'NO se ocultó'}")
    bloc.kill()
    bombear(0.8)
    juego.lift()
    juego.focus_force()
    vuelve = bombear(8, lambda: app.vivo is not None and getattr(app.vivo.capa, "_visible", True)
                     and bool(app.vivo.bloques))
    extra2 = len(llamadas) - n1
    lineas.append(f"Al volver al programa: {'todo sigue traducido (OK)' if vuelve else 'FALLO'}, "
                  f"traducciones nuevas: {extra2}")
    app.detener()
    bombear(0.5)
    m.traducir_lote = original
    juego.destroy()
    for w in r.winfo_children():
        if isinstance(w, tk.Toplevel):
            w.destroy()
    r.withdraw()                  # que no quede encima de las pruebas siguientes
    return ok1 and ok2 and ok3 and oculto and vuelve and extra2 == 0


def _escena_prueba():
    """Un cuadro de diálogo de novela visual (fondo con manchas, caja semitransparente,
    nombre en una etiqueta y dos líneas con sombra) para probar el estilo natural."""
    import estilo
    from PIL import ImageFilter
    W, H = 900, 300
    g = Image.linear_gradient("L").resize((W, H))
    img = Image.merge("RGB", (g.point(lambda v: 36 + v * 160 // 255), g.point(lambda v: 24 + v * 80 // 255),
                              g.point(lambda v: 60 + v * 30 // 255)))
    d = ImageDraw.Draw(img)
    for cx, cy, rr, col in ((90, 60, 80, (220, 120, 160)), (380, 40, 70, (90, 190, 210)),
                            (640, 80, 95, (240, 190, 90)), (820, 200, 70, (150, 90, 210)),
                            (250, 230, 60, (90, 200, 120))):
        d.ellipse((cx - rr, cy - rr, cx + rr, cy + rr), fill=col)
    img = img.filter(ImageFilter.GaussianBlur(16)).convert("RGBA")
    capa = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    dc = ImageDraw.Draw(capa)
    dc.rounded_rectangle((30, 120, 870, 285), radius=16, fill=(24, 20, 48, 215), outline=(200, 190, 230, 180), width=2)
    dc.rounded_rectangle((50, 98, 190, 136), radius=8, fill=(122, 30, 60, 240))
    img.alpha_composite(capa)
    d = ImageDraw.Draw(img)
    nunito, mont = estilo._buscar_archivo("Nunito.ttf"), estilo._buscar_archivo("Montserrat.ttf")
    f = estilo.cargar(nunito, 26, "ExtraLight") if nunito else nucleo.cargar_fuente("Arial", 26)
    fn = estilo.cargar(mont, 22, "SemiBold") if mont else nucleo.cargar_fuente("Arial", 22, True)
    d.text((70, 104), "Aria", font=fn, fill=(255, 225, 150))
    for y, t in ((160, "I never thought I'd see this place again."), (202, "The lanterns still glow the same way.")):
        d.text((62, y + 2), t, font=f, fill=(20, 16, 30))
        d.text((60, y), t, font=f, fill=(232, 230, 238))
    return img.convert("RGB")


def _foto_ventana(win, ruta):
    """Captura de una ventana del programa tal y como se ve (con su barra de título)."""
    import ctypes
    import ctypes.wintypes as wt
    import mss
    for _ in range(6):
        win.update()
        time.sleep(0.05)
    hwnd = ctypes.c_void_p(int(win.wm_frame(), 16))
    rc = wt.RECT()
    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rc))
    try:
        ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(rc), ctypes.sizeof(rc))
    except Exception:
        pass
    with mss.mss() as sct:
        sh = sct.grab({"left": rc.left, "top": rc.top, "width": max(1, rc.right - rc.left),
                       "height": max(1, rc.bottom - rc.top)})
    Image.frombytes("RGB", sh.size, sh.bgra, "raw", "BGRX").save(ruta)


def autoprueba_natural(m, lineas):
    """Windows: estilo natural de verdad (ventanas con transparencia por píxel), clic que las
    atraviesa, «ver el original», vidrio, y capturas de la interfaz para revisarlas."""
    import ctypes
    import mss
    lineas.append("--- Prueba ESTILO NATURAL ---")
    carpeta = os.path.join(nucleo.BASE, "capturas_prueba")
    os.makedirs(carpeta, exist_ok=True)
    app = App(motor=m)
    app.cfg["modo_vista"] = "superpuesto"
    app.cfg["modo_texto"] = "natural"
    app.v_estilo.set("natural")
    r = app.root
    r.withdraw()                  # que no tape el juego de prueba (pantalla de 1024 px)

    def bombear(segundos, hasta=lambda: False):
        fin = time.time() + segundos
        while time.time() < fin:
            r.update()
            if hasta():
                return True
            time.sleep(0.03)
        return hasta()

    escena = _escena_prueba()
    juego = tk.Toplevel(r)
    juego.title("Novela visual de prueba")
    juego.geometry(f"{escena.width}x{escena.height}+120+140")
    foto = ImageTk.PhotoImage(escena, master=juego)
    lbl = tk.Label(juego, image=foto, bd=0, highlightthickness=0)
    lbl.foto = foto
    lbl.pack()
    clics = []
    lbl.bind("<Button-1>", lambda e: clics.append((e.x, e.y)))
    bombear(1.0)
    juego.lift()
    juego.focus_force()
    bombear(0.8)
    hw = envivo.hwnd_de(juego)
    rect = envivo.rect_cliente(hw)
    t0 = time.time()
    app.iniciar_vivo(rect, None, ventana=hw)

    def listas():
        v = app.vivo
        return (v is not None and v.natural is not None and len(v.natural.piezas) >= 1
                and len(v.bloques) >= 2 and all(p.alfa >= 255 for p in v.natural.piezas.values()))
    ok1 = bombear(45, listas)
    v = app.vivo
    lineas.append(f"Natural en pantalla: {'OK' if ok1 else 'FALLO'} en {time.time() - t0:.1f} s · piezas "
                  f"{list(v.natural.piezas) if v and v.natural else None} · "
                  f"{[b['traduccion'] for b in v.bloques] if v else None}")
    if v is not None and v.pintor is not None:
        lineas.append("  letras: " + "; ".join(
            f"«{e['texto'][:22]}» → {os.path.basename(e['fuente'][0] or '?')} {e['fuente'][1] or ''} "
            f"({e['puntos']:.2f}, fondo {e['est']['fondo']})" for e in v.pintor.lineas.values()))

    def captura(nombre):
        app.vivo.natural.permitir_captura(True)
        bombear(0.4)
        with mss.mss() as sct:
            sh = sct.grab(rect)
        app.vivo.natural.permitir_captura(False)
        img = Image.frombytes("RGB", sh.size, sh.bgra, "raw", "BGRX")
        img.save(os.path.join(carpeta, nombre))
        return img

    def diferencia(a, b, caja=(40, 150, 860, 240)):
        if b.size != a.size:
            b = b.resize(a.size)
        return float(np.abs(np.asarray(a.crop(caja), np.float32) - np.asarray(b.crop(caja), np.float32)).mean())
    ok2 = ok3 = False
    if v is not None and v.natural is not None:
        real = captura("1_natural_real.png")
        app.vivo.natural.instantanea(escena, (rect["left"], rect["top"])).save(
            os.path.join(carpeta, "1_natural_compuesta.png"))
        d1 = diferencia(real, escena)
        ok2 = d1 > 4
        lineas.append(f"La traducción se ve sobre el juego: {'OK' if ok2 else 'FALLO'} (cambio medio {d1:.1f})")
        # un clic sobre la traducción debe llegar al juego
        try:
            u32 = ctypes.windll.user32
            u32.SetCursorPos(rect["left"] + 220, rect["top"] + 176)
            u32.mouse_event(0x0002, 0, 0, 0, 0)
            u32.mouse_event(0x0004, 0, 0, 0, 0)
            bombear(1.5, lambda: bool(clics))
            lineas.append(f"Clic a través de la traducción: {'OK' if clics else 'NO llegó'}")
        except Exception as e:
            lineas.append(f"Clic: no se pudo probar ({e})")
        # mantener la tecla: se ve el original
        app.vivo.ver_original(True)
        bombear(0.3)
        orig = captura("2_ver_original.png")
        app.vivo.ver_original(False)
        d2 = diferencia(orig, escena)
        ok3 = d2 < 3
        lineas.append(f"Ver el original (tecla mantenida): {'OK' if ok3 else 'FALLO'} (cambio medio {d2:.1f})")
        # vidrio
        n0 = app.vivo.lecturas
        app.v_estilo.set("vidrio")
        bombear(15, lambda: app.vivo is not None and app.vivo.lecturas >= n0 + 2 and listas())
        bombear(0.6)
        captura("3_vidrio_real.png")
        app.v_estilo.set("natural")
        bombear(15, lambda: app.vivo is not None and app.vivo.lecturas >= n0 + 4 and listas())
    app.detener()
    bombear(0.6)
    # capturas de la interfaz (para revisar el diseño)
    try:
        app.estado("Listo. F1 = ayuda")
        r.deiconify()
        r.geometry("+600+20")
        _foto_ventana(r, os.path.join(carpeta, "4_ventana_principal.png"))
        app.abrir_config()
        bombear(0.8)
        app.config_win.nb.select(1)
        _foto_ventana(app.config_win.win, os.path.join(carpeta, "5_ajustes_en_pantalla.png"))
        app.config_win.nb.select(4)
        _foto_ventana(app.config_win.win, os.path.join(carpeta, "6_ajustes_juegos.png"))
        app.config_win.cancelar()
        juego.lift()
        juego.focus_force()
        bombear(0.5)
        app.menu_rapido()
        bombear(0.6)
        if app.menu_win is not None:
            _foto_ventana(app.menu_win.win, os.path.join(carpeta, "7_menu_rapido.png"))
            app.menu_win.cerrar()
    except Exception as e:
        lineas.append(f"Capturas de la interfaz: no se pudieron hacer ({e})")
    for w in r.winfo_children():
        if isinstance(w, tk.Toplevel):
            w.destroy()
    r.withdraw()
    return ok1 and ok2 and ok3


def autoprueba_vivo(m, lineas):
    """Prueba real en Windows: una ventana con texto, la traducción en vivo encima,
    un clic que debe atravesar la capa y un cambio de texto que debe re-traducirse."""
    import mss
    lineas.append("--- Prueba EN VIVO ---")
    app = App(motor=m)
    app.cfg["modo_vista"] = "superpuesto"
    r = app.root

    def bombear(segundos, hasta=lambda: False):
        fin = time.time() + segundos
        while time.time() < fin:
            r.update()
            if hasta():
                return True
            time.sleep(0.03)
        return hasta()

    fondo = tk.Toplevel(r)
    fondo.overrideredirect(True)
    fondo.geometry("900x220+150+250")
    fondo.configure(bg="white")
    clics = []
    lbl = tk.Label(fondo, text="Welcome to the village, traveler.", font=("Arial", 30),
                   bg="white", fg="black")
    lbl.pack(expand=True, fill="both")
    lbl.bind("<Button-1>", lambda e: clics.append(1))
    bombear(1.0)
    with mss.mss() as sct:
        mon = dict(sct.monitors[1])
    caja = (150 - mon["left"], 250 - mon["top"], 1050 - mon["left"], 470 - mon["top"])
    t0 = time.time()
    app.iniciar_vivo(mon, caja)
    ok1 = bombear(40, lambda: app.vivo is not None and any(
        "welcome to the village" in b["original"].lower() for b in app.vivo.bloques))
    propio = [b["original"] for b in (app.vivo.bloques if app.vivo else []) if "ctrl" in b["original"].lower()]
    lineas.append(f"¿Leyó sus propias ventanas?: {'SÍ ' + str(propio) if propio else 'no (bien)'}")
    ok1 = ok1 and not propio
    lineas.append(f"Traducción inicial: {'OK' if ok1 else 'FALLO'} en {time.time() - t0:.1f} s  "
                  f"{[b['traduccion'] for b in (app.vivo.bloques if app.vivo else [])]}")
    v = app.vivo
    lineas.append(f"Capa traspasable: {v.capa.traspasable if v else None} · "
                  f"excluida de capturas: {v.capa.excluida if v else None}")
    # ¿la capa aparece en una captura? (debe verse el fondo blanco, no el recuadro negro)
    with mss.mss() as sct:
        s = sct.grab({"left": 160, "top": 330, "width": 400, "height": 60})
    claridad = sum(s.rgb[0::3]) / max(1, len(s.rgb) // 3)
    lineas.append(f"Brillo bajo la capa en la captura: {claridad:.0f}/255 "
                  f"({'la capa NO sale en capturas' if claridad > 150 else 'la capa SÍ sale en capturas'})")
    # clic que debe atravesar la capa
    try:
        import ctypes
        u32 = ctypes.windll.user32
        cx, cy = lbl.winfo_rootx() + lbl.winfo_width() // 2, lbl.winfo_rooty() + lbl.winfo_height() // 2
        u32.SetCursorPos(cx, cy)
        u32.mouse_event(0x0002, 0, 0, 0, 0)
        u32.mouse_event(0x0004, 0, 0, 0, 0)
        bombear(1.5, lambda: bool(clics))
        lineas.append(f"Clic a través de la capa: {'OK (llegó a la ventana de debajo)' if clics else 'NO llegó'}")
    except Exception as e:
        lineas.append(f"Clic: no se pudo probar ({e})")
    # cambio de texto -> debe re-traducirse solo
    lbl.configure(text="Do you want to save your progress?")
    t1 = time.time()
    ok2 = bombear(40, lambda: app.vivo is not None and any(
        "do you want to save your progress" in b["original"].lower() for b in app.vivo.bloques))
    lineas.append(f"Actualización automática al cambiar el texto: {'OK' if ok2 else 'FALLO'} "
                  f"en {time.time() - t1:.1f} s  {[b['traduccion'] for b in (app.vivo.bloques if app.vivo else [])]}")
    # modo subtítulos (barra) sobre la misma ventana
    t2 = time.time()
    app.iniciar_vivo(mon, caja, estilo="barra")
    ok3 = bombear(30, lambda: app.vivo is not None and any(
        "progress" in b["original"].lower() for b in app.vivo.bloques))
    lineas.append(f"Subtítulos de pantalla (barra): {'OK' if ok3 else 'FALLO'} en {time.time() - t2:.1f} s")
    ok2 = ok2 and ok3
    # sin cambios no debe volver a leer
    n = app.vivo.lecturas if app.vivo else 0
    bombear(3)
    extra = (app.vivo.lecturas if app.vivo else 0) - n
    lineas.append(f"Lecturas extra con la pantalla quieta (3 s): {extra}")
    app.detener()
    bombear(0.5)
    lineas.append(f"Detener: {'OK' if app.vivo is None else 'FALLO'}")
    for w in r.winfo_children():
        if isinstance(w, tk.Toplevel):
            w.destroy()
    r.withdraw()                  # que no quede encima de las pruebas siguientes
    return ok1 and ok2 and app.vivo is None


def demostracion():
    """Graba-ble: un 'juego' de ejemplo traducido en vivo, manejado con el ratón real.
    Coordina con el flujo de GitHub mediante los archivos demo_listo / demo_grabando."""
    import ctypes
    u32 = ctypes.windll.user32
    nucleo.iniciar_registro()
    m = Motor(avisar=lambda x: None)
    m.cargar()
    app = App(motor=m)
    app.cfg.update({"modo_vista": "superpuesto", "idioma_origen": "auto", "voz_auto": False})
    r = app.root
    sw, sh = r.winfo_screenwidth(), r.winfo_screenheight()

    def bombear(seg, hasta=lambda: False):
        fin = time.time() + seg
        while time.time() < fin:
            r.update()
            if hasta():
                return True
            time.sleep(0.02)
        return hasta()

    def mover(x, y, seg=0.7):
        import ctypes.wintypes as wt
        p = wt.POINT()
        u32.GetCursorPos(ctypes.byref(p))
        x0, y0, pasos = p.x, p.y, max(1, int(seg / 0.02))
        for i in range(1, pasos + 1):
            t = i / pasos
            t = t * t * (3 - 2 * t)
            u32.SetCursorPos(int(x0 + (x - x0) * t), int(y0 + (y - y0) * t))
            bombear(0.02)

    def clic(x, y):
        mover(x, y)
        u32.mouse_event(0x0002, 0, 0, 0, 0)
        bombear(0.08)
        u32.mouse_event(0x0004, 0, 0, 0, 0)
        bombear(0.3)

    def arrastrar(x0, y0, x1, y1):
        mover(x0, y0)
        u32.mouse_event(0x0002, 0, 0, 0, 0)
        bombear(0.15)
        mover(x1, y1, 1.2)
        bombear(0.2)
        u32.mouse_event(0x0004, 0, 0, 0, 0)
        bombear(0.3)

    # --- "juego" de ejemplo ---
    gx, gy, gw, gh = 15, 60, 560, 450
    juego = tk.Toplevel(r)
    juego.title("Aventura (juego de ejemplo)")
    juego.geometry(f"{gw}x{gh}+{gx}+{gy}")
    juego.configure(bg="#1d2b3a")
    juego.attributes("-topmost", True)    # que ninguna otra ventana del servidor lo tape
    tk.Label(juego, text="⚔  THE LOST KINGDOM", font=("Georgia", 22, "bold"), fg="#ffd27a",
             bg="#1d2b3a").pack(pady=(18, 6))
    tk.Label(juego, text="🌲🏠🌲🌲🏠🌲", font=("Segoe UI Emoji", 34), bg="#1d2b3a").pack(pady=6)
    caja = tk.Frame(juego, bg="#0d1520", highlightthickness=3, highlightbackground="#c9d6ff")
    caja.pack(fill="x", padx=24, pady=(14, 8), ipady=6)
    quien = tk.Label(caja, font=("Segoe UI", 15, "bold"), fg="#ffd27a", bg="#0d1520", anchor="w")
    quien.pack(fill="x", padx=14, pady=(8, 0))
    dialogo = tk.Label(caja, font=("Segoe UI", 18), fg="white", bg="#0d1520", anchor="w",
                       justify="left", wraplength=480, height=2)
    dialogo.pack(fill="x", padx=14, pady=(2, 8))
    tk.Label(juego, text="(click the dialog to continue)", font=("Segoe UI", 11, "italic"),
             fg="#8fa3b8", bg="#1d2b3a").pack()
    lineas = [("Old Man", "Welcome to the village, traveler."),
              ("Old Man", "The forest to the north is dangerous at night."),
              ("Old Man", "Take this sword. It belonged to my son."),
              ("お知らせ", "魔法の剣を手に入れた！"),
              ("Old Man", "Do you want to save your progress?")]
    paso = [0]

    def avanzar(_=None):
        paso[0] = min(paso[0] + 1, len(lineas) - 1)
        q, t = lineas[paso[0]]
        quien.configure(text=q)
        dialogo.configure(text=t)
    for wdg in (caja, quien, dialogo):
        wdg.bind("<Button-1>", avanzar)
    paso[0] = -1
    avanzar()

    # --- narración (fuera de la zona) ---
    nar = tk.Toplevel(r)
    nar.overrideredirect(True)
    nar.attributes("-topmost", True)
    nar.geometry(f"{sw}x54+0+{sh - 100}")
    nar.configure(bg="#4a0816")
    txt_nar = tk.Label(nar, font=("Segoe UI", 17, "bold"), fg="#f5eedc", bg="#4a0816")
    txt_nar.pack(expand=True)

    def narrar(t):
        txt_nar.configure(text=t)
        bombear(0.05)

    r.geometry(f"+{min(gx + gw + 15, sw - 470)}+10")
    # La capa debe verse en el video: no se excluye de las capturas, y el programa
    # lee el juego directamente desde su ventana.
    app.sin_exclusion = True
    app.capturar_vivo = lambda: envivo.capturar_ventana(juego, app.vivo.region)
    bombear(1.5)
    with open(os.path.join(nucleo.BASE, "demo_listo"), "w") as fh:
        fh.write("1")
    bombear(60, lambda: os.path.exists(os.path.join(nucleo.BASE, "demo_grabando")))
    bombear(1.0)

    narrar("Demostración: Traductor de Pantalla  ·  inglés / japonés → español en vivo")
    bombear(2.5)
    narrar("1. Pulso «Elegir zona en vivo» (o Ctrl+Alt+Z)")
    b = app.botones["zona"][0]
    clic(b.winfo_rootx() + b.winfo_width() // 2, b.winfo_rooty() + b.winfo_height() // 2)
    bombear(10, lambda: app.selector is not None)
    bombear(0.8)
    narrar("2. Arrastro el ratón sobre el cuadro de diálogo")
    bombear(0.6)
    x0, y0 = caja.winfo_rootx() - 6, caja.winfo_rooty() - 6
    x1, y1 = x0 + caja.winfo_width() + 12, y0 + caja.winfo_height() + 12
    if app.selector is not None:
        arrastrar(x0, y0, x1, y1)
    else:
        app.ultima_zona = None
        with __import__("mss").mss() as sct:
            mon = dict(sct.monitors[1])
        app.iniciar_vivo(mon, (x0, y0, x1, y1))
    narrar("3. La traducción aparece encima, en vivo")
    bombear(15, lambda: app.vivo is not None and bool(app.vivo.bloques))
    bombear(3.0)
    for i in range(len(lineas) - 1):
        narrar("4. Hago clic ATRAVESANDO la traducción: el juego responde normal")
        cx, cy = dialogo.winfo_rootx() + 200, dialogo.winfo_rooty() + 25
        clic(cx, cy)
        narrar("5. El texto cambió → se traduce solo, sin pulsar nada")
        bombear(2.5)
        bombear(3.5)
    narrar("6. Ctrl+Alt+X para detener")
    mover(gx + gw // 2, gy + gh + 40)
    for vk in (0x11, 0x12, 0x58):
        u32.keybd_event(vk, 0, 0, 0)
    bombear(0.1)
    for vk in (0x58, 0x12, 0x11):
        u32.keybd_event(vk, 0, 2, 0)
    bombear(1.5)
    if app.vivo is not None:
        app.detener()
    narrar("Listo: sin bloquear tu juego, sin internet, en 1-2 segundos por cambio")
    bombear(4)
    with open(os.path.join(nucleo.BASE, "demo_fin"), "w") as fh:
        fh.write(f"avances={paso[0]}")
    os._exit(0)


def main():
    nucleo.iniciar_registro()
    if "--demo" in sys.argv:
        demostracion()
    if "--autoprueba" in sys.argv:
        autoprueba()
    # Evitar abrirlo dos veces (los atajos chocarían)
    global CANDADO
    candado = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    intentos = 30 if os.environ.pop("TRADUCTOR_REINICIO", "") else 1
    for intento in range(intentos):  # tras una actualización, esperar a que se cierre la anterior
        try:
            candado.bind(("127.0.0.1", PUERTO_INSTANCIA))
            break
        except OSError:
            time.sleep(0.2)
    else:
        r = tk.Tk()
        r.withdraw()
        messagebox.showinfo("Traductor de Pantalla", "El traductor ya está abierto.")
        return
    CANDADO = candado
    try:
        App().correr()
    except Exception:
        log.exception("Error fatal")
        raise
    finally:
        candado.close()


if __name__ == "__main__":
    main()
