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
from PIL import Image, ImageDraw, ImageEnhance, ImageFont, ImageTk

import nucleo
from nucleo import (ACCIONES, ACCIONES_MANTENER, ACCIONES_VIVO, ACCIONES_FIJO, TAM_INTERFAZ, Motor, Voz,
                    Cancelado, atajo_legible, atajo_pynput, log)
import ventanas
import envivo
import tema

# En Windows la traducción es "en vivo" (capa transparente que deja pasar los clics).
VIVO = envivo.ES_WINDOWS or os.environ.get("TRADUCTOR_VIVO") == "1"
ACCIONES_APP = ACCIONES_VIVO if VIVO else ACCIONES_FIJO

PUERTO_INSTANCIA = 47653
AVISO_NEGRO = ("No consigo ver la imagen del juego: la captura sale negra. Si el juego está en "
               "pantalla completa, cámbialo a «ventana sin bordes» (o «ventana») en sus opciones, o "
               "prueba Alt+Enter; así el traductor puede leerlo y dibujar encima.")
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
        self.paquete_listo = None          # versión completa descargada, se instala al cerrar
        self._lock_diccionarios = threading.Lock()
        if motor is None:                  # tras instalar una versión completa: borrar la copia vieja
            self.root.after(120000, lambda: threading.Thread(target=self._limpiar_anterior, daemon=True).start())
        if motor is not None:          # motor ya cargado (autoprueba)
            self.listo = True
        else:
            threading.Thread(target=self._cargar_motor, daemon=True).start()
        self.root.after(100, self._revisar_cola)

    # ======================================================================
    # Construcción de la ventana principal
    # ======================================================================
    def _crear_icono(self):
        import marca
        # bandeja: cada tamaño dibujado aparte (16-24 px sin medalla), no una reducción borrosa
        self._icono_pil = marca.emblema(32).copy()
        self._icono_pil.encoderinfo = {"sizes": [(16, 16), (20, 20), (24, 24), (32, 32)],
                                       "append_images": [marca.emblema(t) for t in (16, 20, 24)]}
        self._iconos = [ImageTk.PhotoImage(marca.emblema(t), master=self.root) for t in (16, 24, 32, 48, 64, 256)]
        return self._iconos[0]

    def poner_icono(self, ventana):
        ventana.es_del_traductor = True       # marca las ventanas propias del programa
        try:
            ventana.iconphoto(False, *self._iconos)     # Windows elige el tamaño justo
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
        self._dibujar_marca()

    def _construir(self):
        import marca
        r = self.root
        r.resizable(False, False)
        r.attributes("-topmost", bool(self.cfg["siempre_visible"]))
        f = ttk.Frame(r, padding=(20, 16, 20, 12))
        f.pack(fill="both", expand=True)
        ls = self.fuente_ui.metrics("linespace")
        ancho_txt = int(ls * 27)

        # ---- cabecera: emblema + nombre ----
        cab = ttk.Frame(f)
        cab.pack(fill="x")
        self._lbl_emblema = ttk.Label(cab)
        self._lbl_emblema.pack(side="left", padx=(0, 12))
        nombre = ttk.Frame(cab)
        nombre.pack(side="left", fill="x", expand=True)
        self._lbl_logo = ttk.Label(nombre, text="Traductor de Pantalla", style="Titulo.TLabel")
        self._lbl_logo.pack(anchor="w", pady=(2, 4))
        self.lbl_sub = ttk.Label(nombre, style="Lema.TLabel")
        self.lbl_sub.pack(anchor="w")
        tema.Filete(f).pack(fill="x", pady=(12, 14))

        # ---- el camino: un botón principal ----
        self.botones = {}
        self._dobles = set()          # botones de la rejilla: nombre arriba, atajo abajo
        f_titulo = tkfont.Font(r, family=self.fuente_ui.actual("family"),
                               size=self.fuente_ui.actual("size") + 1, weight="bold")
        f_tarjeta = tkfont.Font(r, family=self.fuente_ui.actual("family"),
                                size=self.fuente_ui.actual("size"), weight="bold")
        f_atajo = tkfont.Font(r, family=self.fuente_ui.actual("family"),
                              size=max(7, self.fuente_ui.actual("size") - 1))
        self._fuentes_botones = (f_titulo, f_tarjeta, f_atajo)
        if VIVO:
            principal = ("anclar", "Traducir un juego o programa", "mando")
            resto = (("subtitulos", "Subtítulos de pantalla", "subtitulos"), ("voz", "Subtítulos de voz", "voz"),
                     ("portapapeles", "Traducir lo copiado", "copiado"), ("detener", "Detener", "detener"))
        else:
            principal = ("zona", "Traducir una zona", "subtitulos")
            resto = (("pantalla", "Pantalla completa", "subtitulos"), ("subtitulos", "Subtítulos de pantalla", "subtitulos"),
                     ("voz", "Subtítulos de voz", "voz"), ("portapapeles", "Traducir lo copiado", "copiado"),
                     ("detener", "Detener", "detener"))
        b = tema.Boton(f, lambda a=principal[0]: self.accion(a), principal[1], icono=principal[2],
                       variante="principal", fuente=f_titulo, fuente_atajo=f_atajo)
        b.pack(fill="x")
        self.botones[principal[0]] = (b, principal[1])
        if VIVO:
            self._lbl_ayuda = ttk.Label(
                f, style="Tenue.TLabel", wraplength=ancho_txt, justify="left",
                text="Abre tu juego, pulsa el botón (o el atajo) y haz clic en el juego. La traducción "
                     "ocupa el lugar del texto, con su misma letra, y la próxima vez empieza sola.")
            self._lbl_ayuda.pack(anchor="w", pady=(7, 12))
        rej = ttk.Frame(f)
        rej.pack(fill="x", pady=(0, 12))
        for i, (accion, texto, icono) in enumerate(resto):
            b = tema.Boton(rej, (self.detener if accion == "detener" else (lambda a=accion: self.accion(a))),
                           texto, icono=icono, variante="tarjeta", fuente=f_tarjeta, fuente_atajo=f_atajo)
            b.grid(row=i // 2, column=i % 2, sticky="nsew", padx=(0, 4) if i % 2 == 0 else (4, 0), pady=(0, 8))
            self.botones[accion] = (b, texto)
            self._dobles.add(accion)
        rej.columnconfigure(0, weight=1, uniform="a")
        rej.columnconfigure(1, weight=1, uniform="a")
        self._textos_botones()

        # ---- idiomas ----
        ttk.Label(f, text="IDIOMAS", style="Seccion.TLabel").pack(anchor="w", pady=(0, 4))
        idi = ttk.Frame(f)
        idi.pack(fill="x")
        self.v_idioma = tk.StringVar(r, value=self.cfg["idioma_origen"])
        self._origenes = {"Detectar (inglés, japonés, chino)": "auto", "Inglés": "en", "Japonés": "ja",
                          "Chino": "zh"}
        self.cb_origen = ttk.Combobox(idi, state="readonly", width=27, values=list(self._origenes))
        self.cb_origen.pack(side="left")
        self.cb_origen.bind("<<ComboboxSelected>>",
                            lambda e: self.v_idioma.set(self._origenes[self.cb_origen.get()]))
        self.v_idioma.trace_add("write", lambda *_: self._origen_cambiado())
        self._lbl_flecha = ttk.Label(idi)
        self._lbl_flecha.pack(side="left", padx=8)
        import traduccion
        self._idiomas_dest = {v: k for k, v in traduccion.IDIOMAS_DESTINO.items()}
        self.cb_destino = ttk.Combobox(idi, state="readonly", width=16,
                                       values=list(traduccion.IDIOMAS_DESTINO.values()))
        self.cb_destino.set(traduccion.IDIOMAS_DESTINO.get(self.cfg["idioma_destino"], "Español"))
        self.cb_destino.pack(side="left", fill="x", expand=True)
        self.cb_destino.bind("<<ComboboxSelected>>", lambda e: self._cambiar_destino())
        self.lbl_motor = ttk.Label(f, style="Tenue.TLabel")
        self.lbl_motor.pack(anchor="w", pady=(4, 10))
        self._sincronizar_origen()

        # ---- última traducción ----
        ult = ttk.Frame(f)
        ult.pack(fill="x", pady=(0, 6))
        ttk.Label(ult, text="Última traducción", style="Tenue.TLabel").pack(side="left", padx=(0, 6))
        self.botones_ultima = []
        for texto, fn in (("Copiar", lambda: self.copiar_texto(ventanas.texto_de(self.ultimos_bloques))),
                          ("Leer en voz alta", lambda: self.leer(ventanas.texto_de(self.ultimos_bloques))),
                          ("Ver texto", lambda: self.panel.mostrar(self.ultimos_bloques))):
            b = ttk.Button(ult, text=texto, command=fn, style="Enlace.TButton", width=0)
            b.pack(side="left")
            self.botones_ultima.append(b)

        # ---- pie: herramientas y estado ----
        ttk.Frame(f, style="Linea2.TFrame", height=1).pack(fill="x", pady=(4, 6))
        pie = ttk.Frame(f)
        pie.pack(fill="x")
        for i, (texto, fn) in enumerate((("Historial", self.abrir_historial),
                                         ("Mi glosario", self.abrir_glosario),
                                         ("Ajustes", self.abrir_config),
                                         ("Ayuda", lambda: ventanas.ventana_ayuda(self)),
                                         ("Actualizar", lambda: self.buscar_actualizacion(True)))):
            ttk.Button(pie, text=texto, command=fn, style="Enlace.TButton", width=0).grid(
                row=0, column=i, sticky="we")
            pie.columnconfigure(i, weight=1, uniform="p")

        self.btn_novedad = ttk.Button(f, style="Grande.Acento.TButton", command=self._instalar_novedad)
        self.novedad = None
        self.barra_prog = ttk.Progressbar(f, mode="indeterminate", style="Quieta.Horizontal.TProgressbar")
        self.barra_prog.pack(fill="x", pady=(8, 6))
        est = ttk.Frame(f)
        est.pack(fill="x")
        self.punto = tema.Punto(est, lado=max(8, int(ls * 0.55)))
        self.punto.pack(side="left", anchor="n", pady=(int(ls * 0.25), 0), padx=(0, 8))
        self.lbl_estado = ttk.Label(est, text="Iniciando…", wraplength=ancho_txt - 20, justify="left")
        self.lbl_estado.pack(side="left", anchor="w", fill="x", expand=True)

        # teclado: Enter pulsa el botón enfocado
        r.bind_class("TButton", "<Return>", lambda e: e.widget.invoke())
        r.bind_class("TRadiobutton", "<Return>", lambda e: e.widget.invoke())
        r.bind_class("TCheckbutton", "<Return>", lambda e: e.widget.invoke())
        r.bind("<F1>", lambda e: ventanas.ventana_ayuda(self))
        self._dibujar_marca()
        self._actualizar_botones()

    def _dibujar_marca(self):
        """Emblema, nombre e iconos al tamaño de la letra actual (también al cambiar el tamaño)."""
        import marca
        if not hasattr(self, "_lbl_emblema"):
            return
        ls = self.fuente_ui.metrics("linespace")
        base = self.fuente_ui.actual("size")
        for fnt, tam in zip(self._fuentes_botones, (base + 1, base, max(7, base - 1))):
            fnt.configure(size=tam)
        for b, _ in self.botones.values():
            if isinstance(b, tema.Boton):
                b.reajustar()
        self._img_emblema = ImageTk.PhotoImage(marca.emblema(int(ls * 3.4)), master=self.root)
        self._lbl_emblema.configure(image=self._img_emblema)
        logo = None
        try:
            logo = marca.logotipo(int(ls * 0.86))
        except Exception:
            log.exception("Logotipo")
        if logo is not None:
            self._img_logo = ImageTk.PhotoImage(logo, master=self.root)
            self._lbl_logo.configure(image=self._img_logo, text="")
        self._img_flecha = ImageTk.PhotoImage(marca.icono_ui("flecha", int(ls * 0.95), tema._rgb(tema.VINO_TEXTO)),
                                              master=self.root)
        self._lbl_flecha.configure(image=self._img_flecha)
        ancho = int(ls * 27)
        for lbl in (getattr(self, "_lbl_ayuda", None), self.lbl_estado):
            if lbl is not None:
                lbl.configure(wraplength=ancho - (20 if lbl is self.lbl_estado else 0))
        self.punto.configure(width=max(8, int(ls * 0.55)), height=max(8, int(ls * 0.55)))
        self.punto.lado = max(8, int(ls * 0.55))
        self.punto.redibujar()

    # ---------- idiomas ----------
    def _sincronizar_origen(self):
        nombre = {v: k for k, v in self._origenes.items()}.get(self.v_idioma.get(), "Detectar (inglés, japonés, chino)")
        if self.cb_origen.get() != nombre:
            self.cb_origen.set(nombre)
        destino = self.cb_destino.get() if hasattr(self, "cb_destino") else "Español"
        origen = {"auto": "Inglés · Japonés · Chino", "en": "Inglés", "ja": "Japonés",
                  "zh": "Chino"}.get(self.v_idioma.get(), "Inglés · Japonés · Chino")
        self.lbl_sub.configure(text=f"{origen}  →  {destino}")

    def _origen_cambiado(self):
        self._cambiar("idioma_origen", self.v_idioma.get())
        self._sincronizar_origen()

    def _textos_botones(self):
        for accion, (b, texto) in self.botones.items():
            atajo = ""
            if accion in self.cfg["atajos"] and self.cfg["atajos_activos"]:
                atajo = atajo_legible(self.cfg["atajos"][accion])
            if isinstance(b, tema.Boton):
                b.poner_texto(texto, atajo)
            else:
                b.configure(text=f"{texto}    ({atajo})" if atajo else texto)

    def _actualizar_botones(self):
        activo = self.listo and not self.ocupado
        for accion in ("pantalla", "zona", "portapapeles", "anclar"):
            if accion in self.botones:
                self.botones[accion][0].state(["!disabled"] if activo else ["disabled"])
        hay_algo = (self.ocupado or self.voz.hablando() or self._overlay_abierto() or self.selector
                    or self.vivo is not None or self.voz_subs is not None)
        self.botones["voz"][0].state(["!disabled"] if self.listo else ["disabled"])
        self.botones["subtitulos"][0].state(["!disabled"] if self.listo and not self.ocupado else ["disabled"])
        for b in self.botones_ultima:
            b.state(["!disabled"] if self.ultimos_bloques else ["disabled"])
        detener = self.botones["detener"][0]
        detener.state(["!disabled"] if hay_algo else ["disabled"])
        if isinstance(detener, tema.Boton):
            detener.poner_variante("peligro" if hay_algo else "tarjeta")
        if self.ocupado != getattr(self, "_animando", False):
            self._animando = self.ocupado
            if self.ocupado:
                self.barra_prog.configure(style="Fina.Horizontal.TProgressbar")
                self.barra_prog.start(12)
            else:
                self.barra_prog.stop()
                self.barra_prog.configure(style="Quieta.Horizontal.TProgressbar")
        if hasattr(self, "punto"):
            if getattr(self, "_estado_error", False):
                tipo = "error"
            elif not self.listo:
                tipo = "cargando"
            elif self.vivo is not None or self.voz_subs is not None:
                tipo = "vivo"
            elif self.ocupado:
                tipo = "ocupado"
            else:
                tipo = "listo"
            self.punto.poner(tipo)

    def avisar(self, texto, error=False):
        """Mensaje importante: en la ventana y, si está escondida, como aviso de Windows."""
        self.estado(texto, error=error)
        if self.bandeja is not None and self.root.state() != "normal":
            self.bandeja.avisar(texto)

    def estado(self, texto, error=False):
        if texto.startswith("● "):              # el punto de estado ya lo indica
            texto = texto[2:]
        self._estado_error = error
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
                self._hk.detener()
            except Exception:
                pass
            self._hk = None
        if not self.cfg["atajos_activos"]:
            return
        try:
            import atajos
            mapa = {a: self.cfg["atajos"][a] for a in ACCIONES_APP if a not in ACCIONES_MANTENER}
            self._hk = atajos.Atajos(mapa, lambda a: self.cola.put(("atajo", a))).iniciar()
            if self._hk.respaldo:
                log.info("Atajos con respaldo pynput: %s", ", ".join(self._hk.respaldo))
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
                    self.root.after(9000, lambda: self._preparar_sin_internet(self.cfg["idioma_destino"]))
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
                elif tipo == "llamar":
                    dato()
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
                elif tipo == "aviso_juego":
                    if dato is self.vivo:
                        self.avisar(AVISO_NEGRO, error=True)
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
        if accion in ACCIONES_MANTENER or accion not in ACCIONES_APP:
            return
        if not self.listo:
            self.estado("Todavía estoy cargando, un momento…")
            return
        if self.ocupado or self.selector:
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
        if accion == "anclar":
            if self.vivo is not None and self.vivo.ventana and self.vivo.ventana == envivo.ventana_activa():
                self.detener()                      # el mismo atajo, en el mismo juego: apagar
                return
            self._anclar()
        else:                                       # subtítulos: congelar y elegir la franja
            self._estilo_zona = "barra"
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
        if ventana and exe:
            self._guardar_perfil(exe, ventana, mon, caja, estilo)
        try:
            self.vivo = envivo.SesionEnVivo(self, mon, caja, estilo=estilo, ventana=ventana,
                                            capturar=getattr(self, "capturar_vivo", None),
                                            ruta_exe=exe)
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

    def perfil_cambiado(self, clave):
        pass

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

    def _ofrecer_lector_chino(self):
        """Se vio texto que parece chino y Windows no tiene el lector chino: ofrecer instalarlo (una vez)."""
        lec = getattr(self.motor, "lector", None)
        if not getattr(lec, "falta_chino", False) or getattr(self, "_chino_ofrecido", False):
            return
        self._chino_ofrecido = True
        msg = ("Este texto parece chino, y a Windows le falta su lector de chino (sin él, los caracteres "
               "se leen mal). ¿Instalarlo ahora? Es gratis, pide permiso de administrador y tarda unos "
               "minutos; después, cierra y vuelve a abrir el traductor.")
        self.avisar("Parece chino: falta el lector chino de Windows (Ajustes → Traducción → «Instalar lectores»).")
        if getattr(self, "_sin_preguntas", False):
            return
        self.mostrar_principal()
        if messagebox.askyesno("Lector de chino", msg, parent=self.root):
            import lector
            if lector.instalar_lectores_windows(("zh",)):
                self.estado("Instalando el lector chino… Cuando termine, reinicia el traductor.")

    def _resultado_vivo(self, sesion, bloques, nuevos, duracion, piezas=None):
        if sesion is not self.vivo:
            sesion.procesando = False
            return
        nuevos = sesion.resultado(bloques, nuevos, duracion, piezas)
        self._ofrecer_lector_chino()
        if not getattr(self, "_chino_visto", False) and any(b["idioma"] == "zh" for b in bloques):
            self._chino_visto = True                      # el respaldo sin internet también para el chino
            self._preparar_sin_internet(self.cfg["idioma_destino"], ("zh",))
        if bloques:
            self.ultimos_bloques = bloques
        if nuevos:
            self.historial.agregar(nuevos, sesion.tipo)
            if self.cfg["voz_auto"]:
                self.leer(ventanas.texto_de(nuevos))
        if sesion.lecturas <= 1 or nuevos:
            self._ocultar_mis_ventanas(True)     # por si se abrió una ventana nueva
        if sesion.aviso_negro and sesion._negras > 0:
            return                               # el aviso de imagen negra sigue a la vista
        que = {"subtitulos": "subtítulos", "zona": "zona", "pantalla": "pantalla"}[sesion.tipo]
        if sesion.ventana:
            que += f" de {sesion.titulo[:30]}"
        detalle = self._resumen(bloques) if bloques else "sin texto para traducir"
        leer, trad = self.motor.tiempos
        exacto = " · texto exacto del guion (Ren'Py)" if sesion.guion is not None else ""
        self.estado(f"● EN VIVO ({que}): {detalle}\nlectura {leer:.2f} s + traducción {trad:.2f} s · "
                    f"se actualiza sola{exacto}.")
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
                    + (" Es grande (trae librerías nuevas): se descarga e instala desde aquí."
                       if novedad["necesita_base"] else " Es una actualización pequeña: un clic y listo."))

    def _instalar_novedad(self):
        n = self.novedad
        if not n:
            return
        if n["necesita_base"]:
            if not (envivo.ES_WINDOWS and getattr(sys, "frozen", False) and n.get("url_paquete")):
                if messagebox.askyesno("Actualización", f"La versión {n['version']} trae librerías nuevas: "
                                       "hay que descargar el paquete completo.\n\n¿Abrir la página de "
                                       "descarga?", parent=self.root):
                    import webbrowser
                    webbrowser.open(n["pagina"])
                return
            mb = (n.get("tam_paquete") or 0) / 1048576
            if not messagebox.askyesno(
                    "Actualización",
                    f"La versión {n['version']} trae librerías nuevas: se descarga el paquete completo"
                    f"{f' (unos {mb:.0f} MB)' if mb else ''} desde aquí, se comprueba y se instala al "
                    "cerrar. Tus ajustes, memoria, perfiles e historial se conservan.\n\nPuedes seguir "
                    "usando el traductor mientras se descarga. ¿Empezar?", parent=self.root):
                return
            self.btn_novedad.state(["disabled"])
            self._descargar_paquete(n)
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
        self._cerrar_para_actualizar()
        actualizar.reiniciar()

    def _descargar_paquete(self, n):
        import actualizar
        ultimo = [-1]

        def progreso(hecho, total):
            pct = int(100 * hecho / total) if total else 0
            if pct != ultimo[0]:
                ultimo[0] = pct
                self.cola.put(("estado", f"Descargando la versión {n['version']}: {pct} % "
                                         f"({hecho / 1048576:.0f} de {total / 1048576:.0f} MB)…"))

        def tarea():
            try:
                carpeta = actualizar.descargar_paquete(n, progreso)
                self.cola.put(("llamar", lambda: self._paquete_descargado(n, carpeta)))
            except Exception as e:
                log.exception("Descargando la versión completa")
                motivo = str(e)          # 'e' deja de existir al salir del except: guardar el texto

                def avisar():
                    self.estado(f"No se pudo descargar la versión completa: {motivo}", error=True)
                    self.btn_novedad.state(["!disabled"])
                self.cola.put(("llamar", avisar))
        threading.Thread(target=tarea, daemon=True).start()

    def _paquete_descargado(self, n, carpeta):
        self.paquete_listo = carpeta
        self.btn_novedad.pack_forget()
        if messagebox.askyesno("Actualización", f"La versión {n['version']} está descargada y comprobada.\n\n"
                               "¿Reiniciar ahora para terminar de instalarla? (Si dices que no, se "
                               "instala al cerrar el traductor.)", parent=self.root):
            self._instalar_paquete()
        else:
            self.estado(f"La versión {n['version']} se instalará al cerrar el traductor.")

    def _instalar_paquete(self):
        import actualizar
        carpeta, self.paquete_listo = self.paquete_listo, None
        self._cerrar_para_actualizar()
        actualizar.instalar_paquete(carpeta)

    def _cerrar_para_actualizar(self):
        self.detener_vivo()
        self.detener_voz()
        self._quitar_bandeja()
        self.historial.guardar()
        self.guardar_cfg()
        if self.motor.memoria is not None:
            self.motor.memoria.cerrar()
        if CANDADO is not None:
            CANDADO.close()

    @staticmethod
    def _limpiar_anterior():
        try:
            import actualizar
            actualizar.limpiar_anterior()
        except Exception:
            log.exception("Borrando la copia de la versión anterior")

    # ---------- idioma de destino ----------
    def _cambiar_destino(self):
        codigo = self._idiomas_dest.get(self.cb_destino.get(), "es")
        self._cambiar("idioma_destino", codigo)
        self._sincronizar_origen()
        self.estado(f"Ahora se traduce a {self.cb_destino.get()}.")
        if self.listo:
            self._preparar_sin_internet(codigo)

    def _preparar_sin_internet(self, codigo, origenes=None):
        """Diccionarios sin internet (el respaldo) para traducir a 'codigo': si faltan, se
        descargan solos en segundo plano. Así el paquete no los lleva y pesa mucho menos.
        El de chino solo cuando ya se vio un juego en chino."""
        if self.motor.traductor is None:
            return
        argos = self.motor.traductor.argos
        if origenes is None:
            origenes = ("en", "ja") + (("zh",) if getattr(self, "_chino_visto", False) else ())
        origenes = tuple(o for o in origenes if o != codigo)
        if all(argos.disponible(o, codigo) for o in origenes):
            return
        import traduccion
        nombre = traduccion.IDIOMAS_DESTINO.get(codigo, codigo)

        def preparar():
            with self._lock_diccionarios:          # uno a la vez (no descargar dos veces lo mismo)
                try:
                    if all(argos.disponible(o, codigo) for o in origenes):
                        return
                    self.cola.put(("estado", f"Descargando el diccionario sin internet ({nombre}). "
                                             f"Solo la primera vez; puedes usar el traductor mientras."))
                    for origen in origenes:
                        argos.descargar(origen, codigo, lambda m: None)
                    self.cola.put(("estado", f"Listo también sin internet: {nombre}."))
                except Exception:
                    log.exception("Descargando diccionario sin internet")
        threading.Thread(target=preparar, daemon=True, name="diccionarios").start()

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
                    f"Ajustes → Traducción → «Instalar lectores».")
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
        cuenta = {i: sum(b["idioma"] == i for b in bloques) for i in ("en", "ja", "zh")}
        partes = [f"{cuenta[i]} en {nombre}" for i, nombre in (("en", "inglés"), ("ja", "japonés"), ("zh", "chino"))
                  if cuenta[i]]
        return f"{len(bloques)} frase(s) traducidas (" + ", ".join(partes) + ")"

    def _mostrar_resultado(self, trabajo, img, mon, bloques, caja, segundos):
        self._ofrecer_lector_chino()
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
        if self.paquete_listo:                 # una versión completa espera: instalarla al cerrar
            try:
                self._instalar_paquete()
            except Exception:
                log.exception("Instalando la versión completa al cerrar")
        try:
            if self._hk is not None:
                self._hk.detener()
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


GOOGLE_BLOQUEADO = False


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
        bloqueado = 0
        for intento in range(3):
            for origen, textos, destino in (("en", frases, "es"), ("en", frases, "pt"),
                                            ("ja", ["魔法の剣を手に入れた！"], "es")):
                try:
                    t = time.perf_counter()
                    r = m.traductor.google.lote(textos, origen, destino)
                    lineas.append(f"EN LÍNEA {origen}→{destino}: {time.perf_counter() - t:.2f} s  {r}")
                    ok_linea = True
                except Exception as e:
                    bloqueado += "429" in str(e)
                    lineas.append(f"EN LÍNEA {origen}→{destino}: FALLO {str(e)[:60]}")
            if ok_linea or not bloqueado:
                break
            lineas.append("Google limita a este servidor de pruebas (429): se espera y se reintenta")
            time.sleep(60)
        if not ok_linea and bloqueado:
            # No es un fallo del programa: Google bloquea la IP del servidor de pruebas. Se sigue
            # probando todo con la traducción sin internet (lo que usa la app en ese caso).
            global GOOGLE_BLOQUEADO
            GOOGLE_BLOQUEADO = ok_linea = True
            lineas.append("EN LÍNEA: omitida (Google bloquea este servidor); el resto se prueba sin internet")
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
        if envivo.ES_WINDOWS:
            ok = autoprueba_banco(m, lineas) and ok
        if ok and VIVO:
            ok = autoprueba_vivo(m, lineas)
        if ok and envivo.ES_WINDOWS:
            ok = autoprueba_anclado(m, lineas)
        if ok and envivo.ES_WINDOWS:
            ok = autoprueba_natural(m, lineas)
        if ok and envivo.ES_WINDOWS:
            ok = autoprueba_robustez(m, lineas)
        if ok and envivo.ES_WINDOWS:
            ok = autoprueba_furigana(m, lineas)
        if ok and envivo.ES_WINDOWS:
            ok = autoprueba_estabilidad(m, lineas)
        if ok and envivo.ES_WINDOWS:
            ok = autoprueba_chino(m, lineas)
        if ok:
            ok = autoprueba_contexto_nombres_guion(m, lineas)
        if ok and envivo.ES_WINDOWS:
            ok = autoprueba_instalador(lineas)
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
    m.traducir_lote = lambda items, destino=None, contexto=None: (llamadas.append([t for t, _ in items]),
                                                                  original(items, destino, contexto))[1]
    app = _app_de_prueba(m)
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


def autoprueba_instalador(lineas):
    """Windows: el instalador de versiones completas cambia el programa y conserva los datos
    del usuario; si el paquete nuevo está roto, deja la versión anterior intacta."""
    import shutil
    import subprocess
    import tempfile
    import actualizar
    lineas.append("--- Prueba INSTALADOR de versiones completas ---")
    ok_total = True
    for caso in ("bueno", "roto"):
        raiz = tempfile.mkdtemp(prefix="traductor_prueba_")
        base, nueva = os.path.join(raiz, "programa"), os.path.join(raiz, "nueva", "Traductor de Pantalla")
        os.makedirs(os.path.join(base, "app"))
        os.makedirs(os.path.join(nueva, "app"))
        exe = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "System32", "whoami.exe")
        shutil.copyfile(exe, os.path.join(base, "Traductor de Pantalla.exe"))
        open(os.path.join(base, "app", "version.py"), "w").write("VERSION = 'vieja'")
        for d in ("config.json", "memoria.sqlite", "perfiles.json"):
            open(os.path.join(base, d), "w").write("mis datos")
        os.makedirs(os.path.join(base, "modelos", "argos"))      # ya descargados: el paquete no los trae
        open(os.path.join(base, "modelos", "argos", "modelo.bin"), "w").write("mis datos")
        if caso == "bueno":
            shutil.copyfile(exe, os.path.join(nueva, "Traductor de Pantalla.exe"))
        open(os.path.join(nueva, "app", "version.py"), "w").write("VERSION = 'nueva'")
        open(os.path.join(nueva, "config.json"), "w").write("de fábrica")      # no debe pisar el tuyo
        guion = actualizar.escribir_guion(nueva, base, pid=999999, nombre=f"prueba_instalar_{caso}.ps1")
        subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", guion],
                       timeout=120, capture_output=True)
        version_ahora = open(os.path.join(base, "app", "version.py")).read()
        datos_ok = all(os.path.isfile(os.path.join(base, d)) and open(os.path.join(base, d)).read() == "mis datos"
                       for d in ("config.json", "memoria.sqlite", "perfiles.json",
                                 os.path.join("modelos", "argos", "modelo.bin")))
        exe_ok = os.path.isfile(os.path.join(base, "Traductor de Pantalla.exe"))
        esperado = "nueva" if caso == "bueno" else "vieja"
        ok = esperado in version_ahora and datos_ok and exe_ok
        lineas.append(f"Instalador ({'paquete correcto' if caso == 'bueno' else 'paquete roto'}): versión "
                      f"{'nueva' if 'nueva' in version_ahora else 'anterior'}, datos del usuario "
                      f"{'intactos' if datos_ok else 'PERDIDOS'}, programa {'presente' if exe_ok else 'FALTA'}"
                      f" → {'OK' if ok else 'FALLO'}")
        ok_total = ok_total and ok
        shutil.rmtree(raiz, ignore_errors=True)
    return ok_total


def _escena_prueba(lineas=("I never thought I'd see this place again.", "The lanterns still glow the same way."),
                   variante=0, opacidad_caja=200):
    """Un cuadro de diálogo de novela visual (fondo con manchas, caja semitransparente,
    nombre en una etiqueta y líneas con sombra) para probar el estilo natural.
    variante > 0 mueve las manchas del fondo (fondo animado)."""
    import rotulos
    from PIL import ImageFilter
    W, H = 900, 300
    g = Image.linear_gradient("L").resize((W, H))
    img = Image.merge("RGB", (g.point(lambda v: 36 + v * 160 // 255), g.point(lambda v: 24 + v * 80 // 255),
                              g.point(lambda v: 60 + v * 30 // 255)))
    d = ImageDraw.Draw(img)
    dx = (variante * 97) % 300 - 150 if variante else 0
    for cx, cy, rr, col in ((90, 60, 80, (220, 120, 160)), (380, 40, 70, (90, 190, 210)),
                            (640, 80, 95, (240, 190, 90)), (820, 200, 70, (150, 90, 210)),
                            (250, 230, 60, (90, 200, 120)), (520, 210, 75, (230, 110, 90))):
        cx = cx + (dx if cy > 150 else 0)
        d.ellipse((cx - rr, cy - rr, cx + rr, cy + rr), fill=col)
    img = img.filter(ImageFilter.GaussianBlur(16)).convert("RGBA")
    capa = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    dc = ImageDraw.Draw(capa)
    dc.rounded_rectangle((30, 120, 870, 285), radius=16, fill=(24, 20, 48, opacidad_caja), outline=(200, 190, 230, 180),
                         width=2)
    dc.rounded_rectangle((50, 98, 190, 136), radius=8, fill=(122, 30, 60, 240))
    img.alpha_composite(capa)
    d = ImageDraw.Draw(img)
    f = ImageFont.truetype(os.path.join(rotulos.marca.CARPETA_FUENTES, "NotoSans.ttf"), 26)
    f.set_variation_by_axes([300, 100])
    fn = rotulos.fuente(22)
    d.text((70, 104), "Aria", font=fn, fill=(255, 225, 150))
    for y, t in zip((160, 202, 244), lineas):
        if t:
            d.text((62, y + 2), t, font=f, fill=(20, 16, 30))
            d.text((60, y), t, font=f, fill=(232, 230, 238))
    return img.convert("RGB")


def _escena_furigana():
    """Diálogo japonés con furigana (kana pequeña encima de los kanji)."""
    W, H = 900, 300
    img = Image.new("RGB", (W, H), (34, 30, 52))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((30, 110, 870, 285), radius=14, fill=(16, 14, 30), outline=(200, 190, 230), width=2)
    f = nucleo.cargar_fuente_cjk(36)
    fr = nucleo.cargar_fuente_cjk(16)
    if f is None or fr is None:
        return None
    x0, y0 = 64, 176
    texto = "魔法の剣を手に入れた。"
    d.text((x0, y0), texto, font=f, fill=(236, 232, 242))
    for palabra, lectura in (("魔法", "まほう"), ("剣", "けん")):
        i = texto.index(palabra)
        xa = x0 + f.getlength(texto[:i])
        ancho = f.getlength(palabra)
        d.text((xa + (ancho - fr.getlength(lectura)) / 2, y0 - 22), lectura, font=fr, fill=(236, 232, 242))
    return img


def autoprueba_furigana(m, lineas):
    """La furigana no se traduce aparte y se borra junto con su renglón."""
    lineas.append("--- Prueba FURIGANA ---")
    if not m.lector.tiene_japones:
        lineas.append("Sin lector japonés: prueba omitida")
        return True
    escena = _escena_furigana()
    if escena is None:
        lineas.append("Sin letra japonesa: prueba omitida")
        return True
    crudas = m.lector.leer(escena)
    lineas.append(f"Renglones del lector: {[(x['t'], round(x['y1'] - x['y0'])) for x in crudas]}")
    pars = m.leer_parrafos(escena, "auto", True)
    kana_suelta = [p for p in pars if nucleo._KANA_SOLO.match(p["t"]) and len(p["t"]) <= 6]
    principal = next((p for p in pars if "剣" in p["t"] or "魔" in p["t"]), None)
    con_rubi = principal is not None and principal["caja"][1] < principal["filas"][0][1][1] - 3
    hubo_rubi = any(nucleo._KANA_SOLO.match(x["t"]) for x in crudas)
    ok = principal is not None and not kana_suelta
    lineas.append(f"Furigana fuera del texto: {'OK' if ok else 'FALLO'} · párrafos {[p['t'] for p in pars]}"
                  f" · el lector la vio: {'sí' if hubo_rubi else 'no'} · se borra con su renglón: "
                  f"{'sí' if con_rubi else ('no hacía falta' if not hubo_rubi else 'NO')}")
    if hubo_rubi and not con_rubi:
        ok = False
    # verla de verdad en el estilo natural
    try:
        app = _app_de_prueba(m)
        r = app.root
        r.withdraw()

        def bombear(segundos, hasta=lambda: False):
            fin = time.time() + segundos
            while time.time() < fin:
                r.update()
                if hasta():
                    return True
                time.sleep(0.03)
            return hasta()
        juego = tk.Toplevel(r)
        juego.title("Novela japonesa de prueba")
        juego.geometry(f"{escena.width}x{escena.height}+120+140")
        juego.attributes("-topmost", True)            # que ninguna otra ventana de las pruebas lo tape
        foto = ImageTk.PhotoImage(escena, master=juego)
        tk.Label(juego, image=foto, bd=0, highlightthickness=0).pack()
        juego.foto = foto
        bombear(0.8)
        juego.lift()
        juego.focus_force()
        bombear(0.6)
        hw = envivo.hwnd_de(juego)
        rect = envivo.rect_cliente(hw)
        _activar_con_clic(hw, rect, bombear)          # como una persona: un clic la pone delante
        app.iniciar_vivo(rect, None, ventana=hw)
        listo = bombear(40, lambda: app.vivo is not None and app.vivo.natural is not None
                        and len(app.vivo.natural.piezas) >= 1
                        and all(p.alfa >= 255 for p in app.vivo.natural.piezas.values()))
        if not listo and app.vivo is not None:
            v = app.vivo
            import ctypes
            lineas.append(f"  diagnóstico: delante={ctypes.windll.user32.GetForegroundWindow() == hw} "
                          f"oculta={v.oculta} lecturas={v.lecturas} método={v._metodo} "
                          f"líneas={[(l.texto, l.trad) for l in v.seguidor.lineas]} "
                          f"piezas={len(v.natural.piezas) if v.natural else None} "
                          f"estado={app.lbl_estado.cget('text')!r}")
            for d in _ventanas_encima(rect):
                lineas.append(f"  ventana encima del juego: {d}")
        try:                                          # la captura se guarda siempre (también si falla)
            import mss
            if app.vivo is not None and app.vivo.natural is not None:
                app.vivo.natural.permitir_captura(True)
            bombear(0.4)
            with mss.mss() as sct:
                sh = sct.grab(rect)
            if app.vivo is not None and app.vivo.natural is not None:
                app.vivo.natural.permitir_captura(False)
            Image.frombytes("RGB", sh.size, sh.bgra, "raw", "BGRX").save(
                os.path.join(nucleo.BASE, "capturas_prueba", "8_furigana.png"))
        except Exception as e:
            lineas.append(f"  captura de la furigana: no se pudo ({e})")
        lineas.append(f"Furigana en el estilo natural: {'OK' if listo else 'FALLO'} · "
                      f"{[b['traduccion'] for b in app.vivo.bloques] if app.vivo else None}")
        ok = ok and listo
        app.detener()
        bombear(0.4)
        for w in r.winfo_children():
            if isinstance(w, tk.Toplevel):
                w.destroy()
        r.withdraw()
    except Exception:
        import traceback
        lineas.append("Furigana en el estilo natural: FALLO\n" + traceback.format_exc())
        ok = False
    return ok


def _ventanas_encima(rect):
    """Diagnóstico: ventanas visibles de este programa que tapan 'rect' (y de qué prueba son)."""
    import ctypes
    import ctypes.wintypes as wt
    u32 = ctypes.windll.user32
    pid = os.getpid()
    raices = {}
    for i, a in enumerate(_APPS_PRUEBA):
        try:
            raices[int(a.root.wm_frame(), 16)] = f"principal de la prueba nº{i + 1} (estado {a.root.state()})"
        except Exception:
            pass
    vistas = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cada(hw, _):
        p = wt.DWORD()
        u32.GetWindowThreadProcessId(hw, ctypes.byref(p))
        if p.value == pid and u32.IsWindowVisible(hw):
            rc = wt.RECT()
            u32.GetWindowRect(hw, ctypes.byref(rc))
            if (rc.left < rect["left"] + rect["width"] and rc.right > rect["left"]
                    and rc.top < rect["top"] + rect["height"] and rc.bottom > rect["top"]):
                t = ctypes.create_unicode_buffer(256)
                c = ctypes.create_unicode_buffer(256)
                u32.GetWindowTextW(hw, t, 256)
                u32.GetClassNameW(hw, c, 256)
                ex = u32.GetWindowLongW(hw, -20)
                vistas.append(f"{t.value!r} clase {c.value} {(rc.left, rc.top, rc.right, rc.bottom)} "
                              f"{'encima-siempre ' if ex & 0x8 else ''}{raices.get(hw, '')}")
        return True
    u32.EnumWindows(cada, 0)
    return vistas


TEXTO_GACHA = ("異世界募集【森の外-紅ずきん】にて使用される募集アイテム。黒月と学",
               "会が共同開発した新型通信協定で、期間と適用範囲が限られているが、",
               "時には予想外の効果を発揮し、長らく待ち望んだ人物を見つけることが",
               "できる。")


def _escena_gacha(con_texto=True, fecha="2026-12-31 12:00"):
    """Como la descripción de un objeto en un juego: japonés pequeño, gris sobre oscuro."""
    W, H = 980, 430
    img = Image.new("RGB", (W, H), (22, 22, 26))
    d = ImageDraw.Draw(img)
    titulo = nucleo.cargar_fuente_cjk(28)
    cuerpo = nucleo.cargar_fuente_cjk(18)
    if titulo is None or cuerpo is None:
        return None
    d.text((40, 30), "SSR", font=titulo, fill=(255, 190, 60))
    d.text((40, 80), "「森の外」特別募集協定", font=titulo, fill=(240, 240, 240))
    d.rectangle((40, 140, 700, 400), fill=(30, 30, 32), outline=(70, 70, 74))
    if con_texto:
        for i, t in enumerate(TEXTO_GACHA):
            d.text((54, 154 + i * 27), t, font=cuerpo, fill=(168, 168, 172))
        d.text((54, 270), "締切：" + fecha, font=cuerpo, fill=(220, 30, 30))
    return img


def autoprueba_banco(m, lineas):
    """Banco de decisiones en vivo con el lector de Windows: ¿cuántas veces se quedaría una
    traducción vieja (desfase) o se quitaría una buena (parpadeo)?"""
    import banco
    t0 = time.time()
    texto, desfase, parpadeo = banco.medir(m, n=int(os.environ.get("TRADUCTOR_BANCO", "500")))
    lineas.append(texto + f"\n    ({time.time() - t0:.0f} s; letras {len(banco.fuentes()[0])} latinas, "
                          f"{len(banco.fuentes()[1])} japonesas)")
    return desfase <= 0.02 and parpadeo <= 0.02


def autoprueba_estabilidad(m, lineas):
    """Letra pequeña japonesa, y que lo traducido no parpadee (ni con el cursor encima) y se
    vaya en cuanto se va el texto."""
    import ctypes
    import difflib
    lineas.append("--- Prueba LETRA PEQUEÑA y ESTABILIDAD ---")
    if not m.lector.tiene_japones:
        lineas.append("Sin lector japonés: prueba omitida")
        return True
    escena = _escena_gacha()
    if escena is None:
        lineas.append("Sin letra japonesa: prueba omitida")
        return True
    verdad = "".join(TEXTO_GACHA)
    medidas = {}
    for pasada in (False, True):
        m.lector.segunda_pasada = pasada
        pars = m.leer_parrafos(escena, "auto", True)
        mejor = max((p["t"] for p in pars), key=lambda t: difflib.SequenceMatcher(None, t, verdad).ratio(), default="")
        medidas[pasada] = difflib.SequenceMatcher(None, mejor, verdad).ratio()
    m.lector.segunda_pasada = True
    ok_l = medidas[True] >= 0.85
    lineas.append(f"Japonés pequeño gris sobre oscuro: {'OK' if ok_l else 'FALLO'} · parecido al texto real "
                  f"{medidas[False]:.2f} sin la segunda pasada → {medidas[True]:.2f} con ella")
    app = _app_de_prueba(m)
    r = app.root
    r.withdraw()

    def bombear(segundos, hasta=lambda: False):
        fin = time.time() + segundos
        while time.time() < fin:
            r.update()
            if hasta():
                return True
            time.sleep(0.02)
        return hasta()
    juego = tk.Toplevel(r)
    juego.title("Juego con letra pequeña")
    juego.geometry(f"{escena.width}x{escena.height}+20+120")
    juego.attributes("-topmost", True)
    foto = ImageTk.PhotoImage(escena, master=juego)
    lbl = tk.Label(juego, image=foto, bd=0, highlightthickness=0)
    lbl.pack()
    juego.foto = foto
    bombear(0.8)
    hw = envivo.hwnd_de(juego)
    rect = envivo.rect_cliente(hw)
    _activar_con_clic(hw, rect, bombear)
    app.iniciar_vivo(rect, None, ventana=hw)
    v = app.vivo
    listo = bombear(40, lambda: v.natural is not None and len(v.natural.piezas) >= 2
                    and all(p.alfa >= 255 for p in v.natural.piezas.values()) and not v.ocupada())
    lineas.append(f"Traducido: {'OK' if listo else 'FALLO'} · {[b['traduccion'][:40] for b in v.bloques]}")
    ok_e = ok_q = False
    ok_t = True
    if listo:
        # la traducción del cuerpo debe caber en su cuadro, con letra parecida a la original
        for c, p in v.natural.piezas.items():
            if p.img is None:
                continue
            x, y = p.x - rect["left"], p.y - rect["top"]
            w, h = p.img.size
            if y + h / 2 > 140 and (x + w > 730 or y < 120 or h > 290):
                ok_t = False
        lineas.append(f"La traducción cabe en su cuadro: {'OK' if ok_t else 'FALLO'} · "
                      f"{[(p.x - rect['left'], p.y - rect['top']) + tuple(p.img.size) for p in v.natural.piezas.values() if p.img is not None]}")
        try:
            import mss
            v.natural.permitir_captura(True)
            bombear(0.3)
            with mss.mss() as sct:
                sh = sct.grab(rect)
            v.natural.permitir_captura(False)
            Image.frombytes("RGB", sh.size, sh.bgra, "raw", "BGRX").save(
                os.path.join(nucleo.BASE, "capturas_prueba", "10_letra_pequena.png"))
        except Exception:
            pass
        # 8 s con el cursor paseando por encima del texto: nada debe parpadear ni releerse
        claves = set(v.natural.piezas)
        lect = v.lecturas
        cambios, quitadas = 0, 0
        detalle = []
        u32 = ctypes.windll.user32
        fin = time.time() + 8
        k = 0
        while time.time() < fin:
            u32.SetCursorPos(rect["left"] + 60 + (k * 37) % 520, rect["top"] + 160 + (k * 13) % 120)
            k += 1
            bombear(0.1)
            ahora = set(c for c, p in v.natural.piezas.items() if not p.saliendo)
            if ahora != claves:
                cambios += 1
                quitadas += len(claves - ahora)
                detalle.append(f"{time.time() - (fin - 8):.1f}s -{sorted(claves - ahora)} +{sorted(ahora - claves)}")
                claves = ahora
        ok_e = cambios == 0
        lineas.append(f"Quieto 8 s con el cursor encima: {'OK' if ok_e else 'FALLO'} · cambios {cambios}, "
                      f"traducciones quitadas {quitadas}, lecturas extra {v.lecturas - lect} {detalle}")
        # cambia SOLO una línea (la fecha): se lee solo esa franja; el resto no se toca
        quietas = {b["id"]: b["traduccion"] for b in v.bloques if b["caja"][1] < 260}
        v.perfilar = True
        otra = ImageTk.PhotoImage(_escena_gacha(fecha="2027-01-15 18:00"), master=juego)
        juego.foto3 = otra
        t0 = time.time()
        lbl.configure(image=otra)
        llego = bombear(10, lambda: any("2027" in b["original"] for b in v.bloques)
                        and not any(b["traduccion"] != quietas.get(b["id"], b["traduccion"]) for b in v.bloques))
        tarda_f = time.time() - t0
        fr = v.ultimas_franjas
        siguen = all(i in {b["id"] for b in v.bloques} for i in quietas)
        ok_z = llego and siguen and isinstance(fr, list) and sum(b - a for a, b in fr) < 300
        lineas.append(f"Cambia solo la fecha: {'OK' if ok_z else 'FALLO'} en {tarda_f:.2f} s · lo demás intacto: "
                      f"{siguen} · franjas leídas {[(int(a), int(b)) for a, b in fr] if isinstance(fr, list) else fr} · "
                      f"{[b['traduccion'][:30] for b in v.bloques if b['id'] not in quietas]} · pasos "
                      f"{[(round(t - t0, 2), q) for t, q, _ in getattr(v, 'eventos', []) if t >= t0]}")
        ok_e = ok_e and ok_z
        v.perfilar = False
        if getattr(v, "perfil_texto", None):
            lineas.append("  perfil de componer:\n" + "\n".join("    " + x for x in v.perfil_texto.splitlines()[:40]))
        # el texto se va: su traducción también, ya
        vacia = ImageTk.PhotoImage(_escena_gacha(con_texto=False), master=juego)
        juego.foto2 = vacia
        del_cuerpo = {b["id"] for b in v.bloques if b["caja"][1] >= 140}
        t0 = time.time()
        lbl.configure(image=vacia)
        se_fue = bool(del_cuerpo) and bombear(5, lambda: not any(b["id"] in del_cuerpo for b in v.bloques))
        tarda = time.time() - t0
        restantes = [b["original"][:12] for b in v.bloques]
        ok_q = se_fue and tarda < 0.6
        lineas.append(f"El texto se va y la traducción también: {'OK' if ok_q else 'FALLO'} en {tarda:.2f} s · "
                      f"quedan {restantes}")
    app.detener()
    bombear(0.4)
    for w in r.winfo_children():
        if isinstance(w, tk.Toplevel):
            w.destroy()
    r.withdraw()
    return ok_l and listo and ok_e and ok_q and ok_t


FRASES_CHINO = ("你喜欢童话故事吗？喜欢的话，请务必", "让我读给你听。我也非常、非常喜欢。")


def _escena_chino():
    """Como un juego en chino: globo claro semitransparente con texto gris oscuro."""
    W, H = 900, 300
    import numpy as np
    yy, xx = np.mgrid[0:H, 0:W]
    fondo = np.dstack([235 - yy * 0.1, 205 + xx * 0.03, 215 + yy * 0.05]).clip(0, 255).astype("uint8")
    img = Image.fromarray(fondo, "RGB").convert("RGBA")
    d = ImageDraw.Draw(img)
    d.ellipse((520, -80, 860, 260), fill=(248, 226, 160, 255))            # "pelo" del personaje
    globo = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(globo).rounded_rectangle((40, 150, 860, 285), radius=18, fill=(255, 255, 255, 226))
    img.alpha_composite(globo)
    f = None
    for nombre in ("msyh.ttc", "Deng.ttf", "simhei.ttf", "simsun.ttc", "NotoSansCJK-Regular.ttc"):
        try:
            f = ImageFont.truetype(nombre, 30)
            break
        except Exception:
            continue
    if f is None:
        return None
    for i, t in enumerate(FRASES_CHINO):
        d.text((76, 170 + i * 48), t, font=f, fill=(62, 62, 66))
    return img.convert("RGB")


def _vivo_natural(m, lineas, escena, titulo, archivo):
    """Muestra 'escena' como un juego, lo traduce en vivo (estilo natural) y guarda la captura.
    Devuelve (todo bien, textos traducidos)."""
    app = _app_de_prueba(m)
    r = app.root
    r.withdraw()

    def bombear(segundos, hasta=lambda: False):
        fin = time.time() + segundos
        while time.time() < fin:
            r.update()
            if hasta():
                return True
            time.sleep(0.03)
        return hasta()
    juego = tk.Toplevel(r)
    juego.title(titulo)
    juego.geometry(f"{escena.width}x{escena.height}+120+140")
    juego.attributes("-topmost", True)
    foto = ImageTk.PhotoImage(escena, master=juego)
    tk.Label(juego, image=foto, bd=0, highlightthickness=0).pack()
    juego.foto = foto
    bombear(0.8)
    juego.lift()
    juego.focus_force()
    bombear(0.6)
    hw = envivo.hwnd_de(juego)
    rect = envivo.rect_cliente(hw)
    _activar_con_clic(hw, rect, bombear)
    app.iniciar_vivo(rect, None, ventana=hw)
    listo = bombear(40, lambda: app.vivo is not None and app.vivo.natural is not None
                    and len(app.vivo.natural.piezas) >= 1
                    and all(p.alfa >= 255 for p in app.vivo.natural.piezas.values()))
    textos = [b["traduccion"] for b in app.vivo.bloques] if app.vivo else []
    try:
        import mss
        if app.vivo is not None and app.vivo.natural is not None:
            app.vivo.natural.permitir_captura(True)
        bombear(0.4)
        with mss.mss() as sct:
            sh = sct.grab(rect)
        if app.vivo is not None and app.vivo.natural is not None:
            app.vivo.natural.permitir_captura(False)
        Image.frombytes("RGB", sh.size, sh.bgra, "raw", "BGRX").save(
            os.path.join(nucleo.BASE, "capturas_prueba", archivo))
    except Exception as e:
        lineas.append(f"  captura: no se pudo ({e})")
    if not listo:
        lineas.append(f"  estado: {app.lbl_estado.cget('text')!r}")
    app.detener()
    bombear(0.4)
    for w in r.winfo_children():
        if isinstance(w, tk.Toplevel):
            w.destroy()
    r.withdraw()
    return listo, textos


def autoprueba_chino(m, lineas):
    """El texto chino (como el de un juego) se lee como chino y se traduce."""
    lineas.append("--- Prueba CHINO ---")
    if not getattr(m.lector, "tiene_chino", False):
        lineas.append("Sin lector chino en Windows: prueba omitida")
        return True
    escena = _escena_chino()
    if escena is None:
        lineas.append("Sin letra china: prueba omitida")
        return True
    import difflib
    pars = m.leer_parrafos(escena, "auto", True)
    leido = "".join(p["t"] for p in pars if p["idioma"] == "zh")
    parecido = difflib.SequenceMatcher(None, leido, "".join(FRASES_CHINO)).ratio()
    ok = parecido >= 0.85 and all(p["idioma"] == "zh" for p in pars) and len(pars) == 1
    lineas.append(f"Leído como chino: {'OK' if ok else 'FALLO'} · parecido {parecido:.2f} · "
                  f"{[(p['idioma'], p['t']) for p in pars]}")
    try:
        bloques = m.procesar(escena, "auto", True)
    except Exception:
        if not GOOGLE_BLOQUEADO:
            raise
        bloques = []                      # sin internet no hay traducción del chino
    trad = " ".join(b["traduccion"] for b in bloques).lower()
    ok_t = "cuento" in trad or "hadas" in trad or GOOGLE_BLOQUEADO
    lineas.append(f"Traducido del chino: {'OK' if ok_t else 'FALLO'} · {trad}")
    # el japonés sigue siendo japonés
    jp = _escena_furigana()
    if jp is not None and m.lector.tiene_japones:
        pj = m.leer_parrafos(jp, "auto", True)
        ok_j = bool(pj) and all(p["idioma"] == "ja" for p in pj)
        lineas.append(f"El japonés sigue siendo japonés: {'OK' if ok_j else 'FALLO'} · {[p['idioma'] for p in pj]}")
        ok = ok and ok_j
    if GOOGLE_BLOQUEADO:
        lineas.append("Chino en el estilo natural: omitida (sin internet no hay traducción del chino)")
        return ok and ok_t
    listo, textos = _vivo_natural(m, lineas, escena, "Juego en chino de prueba", "9_chino.png")
    lineas.append(f"Chino en el estilo natural: {'OK' if listo else 'FALLO'} · {textos}")
    return ok and ok_t and listo


def _rpyc_de_prueba(frases):
    """Un .rpyc con el mismo formato que los de Ren'Py (cabecera RPC2, pickle comprimido de nodos
    renpy.ast.Say), creado aquí con clases falsas solo para la prueba."""
    import pickle
    import struct
    import types
    import zlib
    falso = types.ModuleType("renpy.ast")

    class Say:
        def __init__(self, who, what):
            self.who, self.what = who, what

        def __getstate__(self):
            return (None, {"who": self.who, "what": self.what, "filename": "game/script.rpy"})
    Say.__module__, Say.__qualname__ = "renpy.ast", "Say"
    falso.Say = Say
    raiz = types.ModuleType("renpy")
    raiz.ast = falso
    viejos = {k: sys.modules.get(k) for k in ("renpy", "renpy.ast")}
    sys.modules["renpy"], sys.modules["renpy.ast"] = raiz, falso
    try:
        blob = zlib.compress(pickle.dumps(({"version": 5003000}, [Say("e", f) for f in frases]), protocol=2))
    finally:
        for k, v in viejos.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
    return b"RENPY RPC2" + struct.pack("III", 1, 34, len(blob)) + struct.pack("III", 0, 0, 0) + blob


def _rpa_de_prueba(ruta, archivos):
    """Un archive.rpa (RPA-3.0) con esos archivos {nombre: bytes}."""
    import pickle
    import zlib
    clave = 0x42424242
    with open(ruta, "wb") as f:
        f.write(b"RPA-3.0 %016x %08x\n" % (0, clave))
        indice = {}
        for nombre, datos in archivos.items():
            pos = f.tell()
            f.write(datos)
            indice[nombre] = [(pos ^ clave, len(datos) ^ clave, b"")]
        pos_indice = f.tell()
        f.write(zlib.compress(pickle.dumps(indice, protocol=2)))
        f.seek(0)
        f.write(b"RPA-3.0 %016x %08x\n" % (pos_indice, clave))


def autoprueba_contexto_nombres_guion(m, lineas):
    """Traducción con contexto, nombres de personaje y texto exacto de Ren'Py."""
    import tempfile
    import guion
    lineas.append("--- Prueba CONTEXTO, NOMBRES y REN'PY ---")
    ok = True
    # contexto: la frase nueva sale sola (sin la anterior pegada)
    try:
        sola = m.traductor.lote(["とても有名です。"], "ja", "es")[0]
        con = m.traductor.con_contexto("とても有名です。", "彼女は医者です。", "ja", "es")
        ok_c = (bool(con) and "médic" not in con.lower() and "doctor" not in con.lower()) or GOOGLE_BLOQUEADO
        lineas.append(f"Contexto: {'OK' if ok_c else 'FALLO'} · sin contexto «{sola}» · con contexto «{con}»")
        ok = ok and ok_c
    except Exception as e:
        lineas.append(f"Contexto: sin internet, no se pudo probar ({e})")
    # nombres: el cuadro del nombre no se traduce, el diálogo sí
    if envivo.ES_WINDOWS:
        W, H = 900, 260
        img = Image.new("RGB", (W, H), (30, 28, 44))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle((40, 60, 860, 240), radius=12, fill=(14, 12, 24))
        d.rounded_rectangle((40, 20, 190, 62), radius=8, fill=(90, 30, 50))
        f = nucleo.cargar_fuente("Segoe UI", 30)
        d.text((62, 24), "Rose", font=f, fill=(250, 240, 230))
        d.text((70, 84), "I never thought I would see this place again.", font=f, fill=(240, 240, 240))
        pars = m.leer_parrafos(img, "auto", True)
        textos = [p["t"] for p in pars]
        ok_n = not any(t.strip() == "Rose" for t in textos) and any("place" in t for t in textos)
        lineas.append(f"Nombre de personaje sin traducir: {'OK' if ok_n else 'FALLO'} · {textos}")
        ok = ok and ok_n
    # Ren'Py: un juego falso con su guion dentro de archive.rpa (.rpyc) y en un .rpy suelto
    carpeta = tempfile.mkdtemp()
    juego = os.path.join(carpeta, "game")
    os.makedirs(juego)
    os.makedirs(os.path.join(carpeta, "renpy"))
    frases = ["It's only when I hear the sounds of shuffling feet that I realize the lecture's over.",
              "魔法の剣を手に入れた。もう怖いものはない。", "你喜欢童话故事吗？喜欢的话，请务必让我读给你听。"]
    _rpa_de_prueba(os.path.join(juego, "archive.rpa"), {"script.rpyc": _rpyc_de_prueba(frases)})
    with open(os.path.join(juego, "extra.rpy"), "w", encoding="utf-8") as fh:
        fh.write('label extra:\n    e "The lanterns still {i}shine{/i} the same way."\n')
    g = guion.cargar_guion(guion.carpeta_juego(os.path.join(carpeta, "Juego.exe")))
    pruebas = [("lt's only when l hear the sounds of shuffling feet that I realize the lecture's over.", frases[0]),
               ("魔法の剣を手に人れた。もう怖いものはない。", frases[1]),
               ("The lanterns still shine the same way.", "The lanterns still shine the same way."),
               ("It's only when I hear the", None)]
    malas = [(o, g.exacta(o)) for o, e in pruebas if g.exacta(o) != e]
    ok_g = len(g) == 4 and not malas
    lineas.append(f"Ren'Py, frase exacta del guion: {'OK' if ok_g else 'FALLO'} · {len(g)} frases · fallos {malas}")
    # y la sesión en vivo carga el guion sola cuando el programa es un juego Ren'Py
    if envivo.ES_WINDOWS:
        app = _app_de_prueba(m)
        app.root.withdraw()
        s = envivo.SesionEnVivo(app, {"left": 0, "top": 0, "width": 400, "height": 300}, None,
                                capturar=lambda: Image.new("RGB", (400, 300)),
                                ruta_exe=os.path.join(carpeta, "Juego.exe"))
        fin = time.time() + 10
        while s.guion is None and time.time() < fin:
            app.root.update()
            time.sleep(0.05)
        ok_s = s.guion is not None and len(s.guion) == 4
        lineas.append(f"La sesión en vivo usa el guion del juego: {'OK' if ok_s else 'FALLO'}")
        s.detener()
        ok_g = ok_g and ok_s
    return ok and ok_g


_APPS_PRUEBA = []


def _app_de_prueba(m):
    """Una ventana del programa nueva para una prueba. Las de pruebas anteriores dejan de vigilar
    perfiles y atajos: en el uso real solo hay una, y aquí se estorbarían (una vieja podía
    empezar sola sobre el juego de la prueba siguiente porque todos son el mismo .exe)."""
    for vieja in _APPS_PRUEBA:
        vieja.listo = False
        vieja.cfg["perfiles_auto"] = False
        if vieja._hk is not None:
            try:
                vieja._hk.detener()
            except Exception:
                pass
            vieja._hk = None
        try:
            vieja.root.withdraw()
        except tk.TclError:
            pass
        try:
            vieja.root.attributes("-topmost", False)
        except tk.TclError:
            pass
    app = App(motor=m)
    app._sin_preguntas = True
    _APPS_PRUEBA.append(app)
    return app


def _activar_con_clic(hw, rect, bombear):
    """Pone delante la ventana de un juego de prueba con un clic en su esquina (Windows no
    siempre deja que un programa se ponga delante solo)."""
    import ctypes
    u32 = ctypes.windll.user32
    for _ in range(3):
        if u32.GetForegroundWindow() == hw:
            return True
        u32.SetCursorPos(rect["left"] + 12, rect["top"] + 12)
        u32.mouse_event(0x0002, 0, 0, 0, 0)
        u32.mouse_event(0x0004, 0, 0, 0, 0)
        bombear(0.4)
    return u32.GetForegroundWindow() == hw


def autoprueba_robustez(m, lineas):
    """Atajos con RegisterHotKey, captura negra (leer la ventana y avisar), una sola copia
    abierta (y mostrar la que ya estaba) y registro con tamaño limitado."""
    import ctypes
    import logging
    import tempfile
    import types
    import atajos
    lineas.append("--- Prueba ROBUSTEZ ---")
    u32 = ctypes.windll.user32
    # --- atajos ---
    recibido = []
    hk = atajos.Atajos({"prueba": {"ctrl": True, "alt": True, "shift": True, "tecla": "F11"}},
                       recibido.append).iniciar()
    time.sleep(0.3)
    for vk in (0x11, 0x12, 0x10, 0x7A):
        u32.keybd_event(vk, 0, 0, 0)
    time.sleep(0.05)
    for vk in (0x7A, 0x10, 0x12, 0x11):
        u32.keybd_event(vk, 0, 2, 0)
    fin = time.time() + 3
    while not recibido and time.time() < fin:
        time.sleep(0.05)
    hk.detener()
    ok_at = recibido == ["prueba"] and "prueba" in hk.registrados
    lineas.append(f"Atajo con RegisterHotKey: {'OK' if ok_at else 'FALLO'} (registrados {hk.registrados}, "
                  f"respaldo {hk.respaldo}, recibido {recibido})")
    # --- una sola copia: el segundo intento no toma el candado y la ventana abierta se muestra ---
    c1 = _tomar_candado(1)
    c2 = _tomar_candado(1)
    ok_c = c1 is not None and c2 is None
    if c1 is not None:
        c1.close()
    c3 = _tomar_candado(1)
    ok_c = ok_c and c3 is not None
    if c3 is not None:
        c3.close()
    app = _app_de_prueba(m)
    r = app.root

    def bombear(segundos, hasta=lambda: False):
        fin = time.time() + segundos
        while time.time() < fin:
            r.update()
            if hasta():
                return True
            time.sleep(0.03)
        return hasta()
    _escuchar_otras_aperturas(app)
    r.withdraw()
    bombear(0.5)
    avisado = _pedir_que_se_muestre()
    visible = bombear(3, lambda: r.state() == "normal")
    ok_c = ok_c and avisado and visible
    lineas.append(f"Una sola copia y mostrar la abierta: {'OK' if ok_c else 'FALLO'} (aviso {avisado}, "
                  f"ventana visible {visible})")
    r.withdraw()
    # --- captura negra: se lee la ventana del juego; si tampoco se ve, se avisa ---
    escena = _escena_prueba()
    juego = tk.Toplevel(r)
    juego.title("Juego en pantalla completa (simulado)")
    juego.geometry(f"{escena.width}x{escena.height}+120+140")
    foto = ImageTk.PhotoImage(escena, master=juego)
    tk.Label(juego, image=foto, bd=0, highlightthickness=0).pack()
    juego.foto = foto
    bombear(0.8)
    juego.lift()
    juego.focus_force()
    bombear(0.6)
    hw = envivo.hwnd_de(juego)
    rect = envivo.rect_cliente(hw)
    app.iniciar_vivo(rect, None, ventana=hw)
    v = app.vivo
    negro = types.SimpleNamespace(grab=lambda reg: types.SimpleNamespace(
        size=(reg["width"], reg["height"]), bgra=bytes(reg["width"] * reg["height"] * 4)), close=lambda: None)
    v._sct = negro                                  # la captura normal "no ve" el juego
    ok_v = bombear(40, lambda: v._metodo == "ventana" and len(v.bloques) >= 2 and v.natural is not None
                   and len(v.natural.piezas) >= 1)
    lineas.append(f"Captura negra → leer la ventana del juego: {'OK' if ok_v else 'FALLO'} (método {v._metodo}, "
                  f"textos {[b['traduccion'] for b in v.bloques]})")
    original = envivo.capturar_hwnd
    envivo.capturar_hwnd = lambda hwnd, reg: Image.new("RGB", (reg["width"], reg["height"]))
    v._metodo, v._negras, v.aviso_negro = "pantalla", 0, False
    ok_a = bombear(25, lambda: app.lbl_estado.cget("text") == AVISO_NEGRO)
    envivo.capturar_hwnd = original
    lineas.append(f"Captura negra sin remedio → aviso: {'OK' if ok_a else 'FALLO'}")
    app.detener()
    bombear(0.4)
    for w in r.winfo_children():
        if isinstance(w, tk.Toplevel):
            w.destroy()
    r.withdraw()
    # --- registro con tamaño limitado ---
    carpeta = tempfile.mkdtemp()
    ruta = os.path.join(carpeta, "registro.txt")
    h = nucleo._RegistroLimitado(ruta, encoding="utf-8", delay=True)
    lg = logging.getLogger("prueba_registro")
    lg.propagate = False
    lg.addHandler(h)
    for _ in range(2600):
        lg.warning("x" * 1000)
    h.close()
    ok_r = os.path.exists(os.path.join(carpeta, "registro_anterior.txt")) and os.path.getsize(ruta) < 2_100_000
    lineas.append(f"Registro limitado a 2 MB: {'OK' if ok_r else 'FALLO'}")
    return ok_at and ok_c and ok_v and ok_a and ok_r


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
    app = _app_de_prueba(m)
    app.cfg["modo_vista"] = "superpuesto"
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
        ok4 = _prueba_texto_automatico(app, juego, lbl, bombear, captura, lineas)
        ok3 = ok3 and ok4
        try:                                  # la ventana principal mientras traduce (Detener activo)
            app.estado(f"Traduciendo en vivo: {juego.title()}")
            r.deiconify()
            r.geometry("+470+0")
            bombear(0.3)
            envivo.excluir_de_captura(r, False)       # en vivo no sale en capturas: solo para la foto
            _foto_ventana(r, os.path.join(carpeta, "4b_ventana_en_vivo.png"))
            envivo.excluir_de_captura(r, True)
            r.withdraw()
            bombear(0.3)
        except Exception as e:
            lineas.append(f"Captura en vivo: no se pudo hacer ({e})")
    app.detener()
    bombear(0.6)
    # capturas de la interfaz (para revisar el diseño)
    try:
        app.estado("Listo. F1 = ayuda")
        r.deiconify()
        r.geometry("+470+0")
        _foto_ventana(r, os.path.join(carpeta, "4_ventana_principal.png"))
        app.abrir_config()
        bombear(0.8)
        app.config_win.nb.select(0)
        _foto_ventana(app.config_win.win, os.path.join(carpeta, "5_ajustes_general.png"))
        app.config_win.nb.select(1)
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


def _prueba_texto_automatico(app, juego, lbl, bombear, captura, lineas):
    """El diálogo avanza solo (máquina de escribir): lo anterior se quita enseguida, lo nuevo
    se traduce al terminar de escribirse, y un fondo animado no hace parpadear la traducción."""
    def mostrar(img):
        f = ImageTk.PhotoImage(img, master=juego)
        lbl.configure(image=f)
        lbl.foto = f

    def en_pantalla():
        v = app.vivo
        if v is None or v.natural is None:
            return {}
        res = {}
        for k, pz in v.natural.piezas.items():
            if not pz.saliendo:
                for x in k.split("-"):
                    res[int(x)] = pz.alfa
        return res

    def id_de(palabra):
        for b in (app.vivo.bloques if app.vivo else []):
            if palabra in b["original"].lower() and b.get("traduccion"):
                return b["id"]
        return None
    ids_a = set(en_pantalla())
    ok = True
    frases = (("Do you remember the festival we had here?", "festival"),
              ("Everyone was dancing until sunrise.", "sunrise"))
    for frase, clave in frases:
        antes = set(en_pantalla())
        t0 = time.time()
        quitada = None
        for k in range(0, len(frase) + 1, 2):             # se escribe sola, ~40 letras por segundo
            mostrar(_escena_prueba((frase[:k], "")))
            bombear(0.05)
            if quitada is None and not (antes & set(en_pantalla())):
                quitada = time.time() - t0
        if quitada is None:
            bombear(2, lambda: not (antes & set(en_pantalla())))
            quitada = (time.time() - t0) if not (antes & set(en_pantalla())) else None
        t1 = time.time()
        lista = bombear(8, lambda: (id_de(clave) is not None and en_pantalla().get(id_de(clave), 0) >= 255))
        tarda = time.time() - t1
        lineas.append(f"Texto automático «{frase[:24]}…»: lo anterior se quitó "
                      f"{'a los %.2f s' % quitada if quitada is not None else 'NUNCA'} de empezar; traducido "
                      f"{'%.2f s después de escribirse' % tarda if lista else 'NO'}")
        ok = ok and lista and quitada is not None and quitada < 1.5
    # vuelve la escena original: recupera su traducción al instante (sin confirmar ni traducir)
    mostrar(_escena_prueba())
    t0 = time.time()
    vuelve = bombear(5, lambda: ids_a and ids_a <= {i for i, a in en_pantalla().items() if a >= 255})
    lineas.append(f"La frase anterior vuelve: {'traducida a los %.2f s' % (time.time() - t0) if vuelve else 'NO volvió'}")
    # una flecha que parpadea junto al texto (▼ de «siguiente»): la traducción no debe parpadear
    def con_flecha(si):
        im = _escena_prueba().convert("RGB")
        if si:
            ImageDraw.Draw(im).polygon([(540, 214), (556, 214), (548, 226)], fill=(240, 240, 255))
        return im
    t0, perdidas, prev = time.time(), 0, set(en_pantalla())
    while time.time() - t0 < 4.0:
        mostrar(con_flecha(int((time.time() - t0) / 0.5) % 2 == 0))
        bombear(0.08)
        ahora = set(en_pantalla())
        perdidas += len(prev - ahora)
        prev = ahora
    ok_f = perdidas == 0
    lineas.append(f"Flecha parpadeando 4 s junto al texto: {'OK' if ok_f else 'FALLO'} · traducciones "
                  f"perdidas {perdidas} · comprobadas leyéndolas {getattr(app.vivo, 'verificaciones', 0)}")
    ok = ok and ok_f
    # fondo que se mueve detrás del texto: la traducción no parpadea ni se vuelve a traducir
    t0, var, vacios, vistas = time.time(), 1, 0, 0
    while time.time() - t0 < 3.0:
        mostrar(_escena_prueba(variante=var, opacidad_caja=90))
        var += 1
        bombear(0.12)
        vistas += 1
        vacios += time.time() - t0 > 0.5 and not en_pantalla()
    sigue = bool(en_pantalla()) and vacios <= 1
    lineas.append(f"Fondo animado 3 s: la traducción sigue: "
                  f"{'sí' if sigue else 'NO'} · momentos sin traducción {vacios} de {vistas} · "
                  f"líneas comprobadas leyéndolas {getattr(app.vivo, 'verificaciones', 0)}")
    captura("3_fondo_animado.png")
    mostrar(_escena_prueba())
    return ok and sigue


def autoprueba_vivo(m, lineas):
    """Prueba real en Windows: una ventana con texto, la traducción en vivo encima,
    un clic que debe atravesar la capa y un cambio de texto que debe re-traducirse."""
    import mss
    lineas.append("--- Prueba EN VIVO ---")
    app = _app_de_prueba(m)
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
    if not ok2 and app.vivo is not None:
        v = app.vivo
        try:
            img = v._capturar()
            lineas.append("  diagnóstico: " + str([(l.texto, tuple(int(c) for c in l.caja),
                                                     None if l.huella is None else int(l.huella.sum()),
                                                     round(envivo.cambio_huella(l.huella, envivo.huella(img, l.caja)), 3))
                                                    for l in v.seguidor.lineas])
                          + f" procesando={v.procesando} lecturas={v.lecturas} natural={v.natural is not None}")
            img.save(os.path.join(nucleo.BASE, "capturas_prueba", "0_vivo_fallo.png"))
        except Exception as e:
            lineas.append(f"  diagnóstico: no se pudo ({e})")
    lineas.append(f"Actualización automática al cambiar el texto: {'OK' if ok2 else 'FALLO'} "
                  f"en {time.time() - t1:.1f} s  {[b['traduccion'] for b in (app.vivo.bloques if app.vivo else [])]}"
                  f" · franjas {getattr(app.vivo, 'ultimas_franjas', None)}")
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
    # Evitar abrirlo dos veces (los atajos chocarían). Si ya está abierto, se muestra ese.
    global CANDADO
    reinicio = bool(os.environ.pop("TRADUCTOR_REINICIO", ""))
    candado = _tomar_candado(30 if reinicio else 1)     # tras actualizar, esperar a que cierre el anterior
    if candado is None:
        if not _pedir_que_se_muestre():
            r = tk.Tk()
            r.withdraw()
            messagebox.showinfo("Traductor de Pantalla", "El traductor ya está abierto.")
        return
    CANDADO = candado
    try:
        app = App()
        _escuchar_otras_aperturas(app)
        app.correr()
    except Exception:
        log.exception("Error fatal")
        raise
    finally:
        candado.close()


class _Candado:
    """Exclusión entre copias del programa: mutex con nombre en Windows (no depende de que un
    puerto esté libre); en otros sistemas, un puerto local."""

    def __init__(self):
        self._mutex = None
        self._sock = None
        if envivo.ES_WINDOWS:
            import ctypes
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.CreateMutexW.restype = ctypes.c_void_p
            k32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
            k32.CloseHandle.argtypes = [ctypes.c_void_p]
            h = k32.CreateMutexW(None, False, "Local\\TraductorDePantalla")
            if not h:
                raise OSError("CreateMutexW")
            if ctypes.get_last_error() == 183:               # ERROR_ALREADY_EXISTS
                k32.CloseHandle(h)
                raise OSError("ya abierto")
            self._mutex, self._k32 = h, k32
        else:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                self._sock.bind(("127.0.0.1", PUERTO_INSTANCIA))
            except OSError:
                self._sock.close()
                raise

    def close(self):
        if self._mutex:
            self._k32.CloseHandle(self._mutex)
            self._mutex = None
        if self._sock is not None:
            self._sock.close()
            self._sock = None


def _tomar_candado(intentos):
    for _ in range(intentos):
        try:
            return _Candado()
        except OSError:
            time.sleep(0.2)
    return None


def _pedir_que_se_muestre():
    """Avisa a la copia abierta para que muestre su ventana. False si no contesta."""
    try:
        with socket.create_connection(("127.0.0.1", PUERTO_INSTANCIA + 1), timeout=1.5) as c:
            c.sendall(b"mostrar")
        return True
    except OSError:
        return False


def _escuchar_otras_aperturas(app):
    def bucle():
        try:
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            srv.bind(("127.0.0.1", PUERTO_INSTANCIA + 1))
            srv.listen(2)
        except OSError:
            log.info("Sin aviso entre copias (puerto ocupado)")
            return
        while True:
            try:
                c, _ = srv.accept()
                with c:
                    c.settimeout(2)
                    if c.recv(16).startswith(b"mostrar"):
                        app.cola.put(("mostrar", None))
            except Exception:
                time.sleep(0.5)
    threading.Thread(target=bucle, daemon=True, name="otra-copia").start()


if __name__ == "__main__":
    main()
