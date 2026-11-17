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

from PIL import Image, ImageDraw, ImageEnhance, ImageTk

import nucleo
from nucleo import ACCIONES, TAM_INTERFAZ, Motor, Voz, Cancelado, atajo_legible, atajo_pynput, log
import ventanas

PUERTO_INSTANCIA = 47653


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
    def __init__(self):
        activar_dpi_windows()
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title("Traductor de Pantalla")
        self.root.report_callback_exception = self._error_tk
        self.cfg = nucleo.cargar_config()
        self.voz = Voz()
        self.cola = queue.Queue()
        self.motor = Motor(avisar=lambda m: self.cola.put(("estado", m)))
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
        return ImageTk.PhotoImage(img)

    def poner_icono(self, ventana):
        try:
            ventana.iconphoto(False, self._icono)
        except Exception:
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
        estilo = ttk.Style(self.root)
        pad = 4 + (tam - 10) // 2
        estilo.configure("TButton", padding=(10, pad))
        estilo.configure("Grande.TButton", padding=(12, pad + 3))
        estilo.configure("Treeview", rowheight=int(tam * 2.4))

    def _construir(self):
        r = self.root
        r.resizable(False, False)
        r.attributes("-topmost", bool(self.cfg["siempre_visible"]))
        f = ttk.Frame(r, padding=14)
        f.pack(fill="both", expand=True)
        ttk.Label(f, text="Traductor de Pantalla", font=self.fuente_titulo).pack(anchor="w")
        ttk.Label(f, text="Inglés / Japonés  →  Español").pack(anchor="w", pady=(0, 10))

        self.botones = {}
        for accion, texto in (("pantalla", "Traducir pantalla completa"),
                              ("zona", "Traducir una zona"),
                              ("repetir", "Repetir la última zona"),
                              ("portapapeles", "Traducir lo copiado")):
            b = ttk.Button(f, style="Grande.TButton",
                           command=lambda a=accion: self.accion(a))
            b.pack(fill="x", pady=2)
            self.botones[accion] = (b, texto)
        b = ttk.Button(f, style="Grande.TButton", command=self.detener)
        b.pack(fill="x", pady=(2, 8))
        self.botones["detener"] = (b, "■ Detener")
        self._textos_botones()

        idi = ttk.LabelFrame(f, text="Idioma de origen", padding=6)
        idi.pack(fill="x", pady=4)
        self.v_idioma = tk.StringVar(value=self.cfg["idioma_origen"])
        for t, v in (("Detectar solo", "auto"), ("Inglés", "en"), ("Japonés", "ja")):
            ttk.Radiobutton(idi, text=t, value=v, variable=self.v_idioma).pack(side="left", padx=4)
        self.v_idioma.trace_add("write", lambda *_: self._cambiar("idioma_origen", self.v_idioma.get()))

        vis = ttk.LabelFrame(f, text="Mostrar la traducción", padding=6)
        vis.pack(fill="x", pady=4)
        self.v_vista = tk.StringVar(value=self.cfg["modo_vista"])
        for t, v in (("Sobre las palabras", "superpuesto"), ("En panel de texto", "panel")):
            ttk.Radiobutton(vis, text=t, value=v, variable=self.v_vista).pack(side="left", padx=4)
        self.v_vista.trace_add("write", lambda *_: self._cambiar("modo_vista", self.v_vista.get()))

        fila = ttk.Frame(f)
        fila.pack(fill="x", pady=(8, 0))
        for i, (texto, fn) in enumerate((("Historial", self.abrir_historial),
                                         ("Mi glosario", self.abrir_glosario),
                                         ("Configuración", self.abrir_config),
                                         ("Ayuda", lambda: ventanas.ventana_ayuda(self)),
                                         ("Salir", self.salir))):
            ttk.Button(fila, text=texto, command=fn).grid(row=i // 3, column=i % 3, sticky="we",
                                                          padx=1, pady=1, columnspan=2 if i == 4 else 1)
        for c in range(3):
            fila.columnconfigure(c, weight=1, uniform="b")

        self.barra_prog = ttk.Progressbar(f, mode="indeterminate")
        self.barra_prog.pack(fill="x", pady=(10, 2))
        self.lbl_estado = ttk.Label(f, text="Iniciando…", wraplength=340, justify="left")
        self.lbl_estado.pack(anchor="w")

        # teclado: Enter pulsa el botón enfocado
        r.bind_class("TButton", "<Return>", lambda e: e.widget.invoke())
        r.bind_class("TRadiobutton", "<Return>", lambda e: e.widget.invoke())
        r.bind_class("TCheckbutton", "<Return>", lambda e: e.widget.invoke())
        r.bind("<F1>", lambda e: ventanas.ventana_ayuda(self))
        self._actualizar_botones()

    def _textos_botones(self):
        for accion, (b, texto) in self.botones.items():
            atajo = atajo_legible(self.cfg["atajos"][accion]) if self.cfg["atajos_activos"] else ""
            b.configure(text=f"{texto}    ({atajo})" if atajo else texto)

    def _actualizar_botones(self):
        activo = self.listo and not self.ocupado
        for accion in ("pantalla", "zona", "portapapeles"):
            self.botones[accion][0].state(["!disabled"] if activo else ["disabled"])
        self.botones["repetir"][0].state(["!disabled"] if activo and self.ultima_zona else ["disabled"])
        hay_algo = self.ocupado or self.voz.hablando() or self._overlay_abierto() or self.selector
        self.botones["detener"][0].state(["!disabled"] if hay_algo else ["disabled"])
        if self.ocupado != getattr(self, "_animando", False):
            self._animando = self.ocupado
            if self.ocupado:
                self.barra_prog.start(12)
            else:
                self.barra_prog.stop()

    def estado(self, texto, error=False):
        self.lbl_estado.configure(text=texto, foreground="#b00020" if error else "")

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
                    for a in ACCIONES}
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
                elif tipo == "listo":
                    self.listo = True
                    self.estado(f"Listo ({dato:.0f} s). Usa los botones o los atajos.  F1 = ayuda")
                elif tipo == "error":
                    self.ocupado = False
                    self.estado(dato, error=True)
                elif tipo == "atajo":
                    self.accion(dato)
                elif tipo == "resultado":
                    self._mostrar_resultado(*dato)
                elif tipo == "resultado_texto":
                    self._mostrar_texto(*dato)
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

    def detener(self):
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

    def _elegir_zona(self, img, mon, trabajo):
        self.ocupado = False          # mientras eliges, no hay nada procesándose
        w = self.selector = tk.Toplevel(self.root)
        w.overrideredirect(True)
        w.geometry(f"{mon['width']}x{mon['height']}+{mon['left']}+{mon['top']}")
        w.attributes("-topmost", True)
        oscura = ImageEnhance.Brightness(img).enhance(0.5)
        foto = ImageTk.PhotoImage(oscura)
        c = tk.Canvas(w, highlightthickness=0, cursor="crosshair", bd=0)
        c.pack(fill="both", expand=True)
        c.create_image(0, 0, image=foto, anchor="nw")
        c.foto = foto
        aviso = "Arrastra con el ratón para elegir la zona   ·   Esc o clic derecho para cancelar"
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
                    estado["ret"] = ImageTk.PhotoImage(brillante.crop(caja))
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
        self.v_vista.set(nueva["modo_vista"])
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
            self.voz.detener()
            self.guardar_cfg()
            self.root.destroy()
        finally:
            os._exit(0)   # cierra también los hilos de carga que sigan activos

    def correr(self):
        self.root.mainloop()


def autoprueba():
    """Comprueba sin interfaz que el OCR y la traducción funcionan (usado al fabricar el .exe).
    Escribe el resultado en autoprueba.txt y sale con código 0 si todo va bien."""
    import traceback
    lineas, ok = [], False
    try:
        img = Image.new("RGB", (980, 270), "white")
        d = ImageDraw.Draw(img)
        f = nucleo.cargar_fuente("Arial", 34)
        d.text((20, 20), "Welcome to the village, traveler.", font=f, fill="black")
        d.text((20, 90), "Do you want to save your progress?", font=f, fill="black")
        fj = nucleo.cargar_fuente_cjk(40)
        if fj:
            d.text((20, 180), "ありがとうございます", font=fj, fill="black")
        t0 = time.time()
        m = Motor(avisar=lambda x: lineas.append("· " + x))
        m.cargar()
        lineas.append(f"Carga: {time.time() - t0:.1f} s")
        t0 = time.time()
        bloques = m.procesar(img)
        lineas.append(f"Lectura + traducción: {time.time() - t0:.1f} s")
        for b in bloques:
            lineas.append(f"[{b['idioma']}] {b['original']}  =>  {b['traduccion']}")
        idiomas = {b["idioma"] for b in bloques}
        ok = len(bloques) >= 2 and "en" in idiomas and (fj is None or "ja" in idiomas)
        lineas.append("RESULTADO: " + ("OK" if ok else "FALLO"))
    except Exception:
        lineas.append(traceback.format_exc())
        lineas.append("RESULTADO: FALLO")
    with open(os.path.join(nucleo.BASE, "autoprueba.txt"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lineas))
    os._exit(0 if ok else 1)


def main():
    nucleo.iniciar_registro()
    if "--autoprueba" in sys.argv:
        autoprueba()
    # Evitar abrirlo dos veces (los atajos chocarían)
    candado = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        candado.bind(("127.0.0.1", PUERTO_INSTANCIA))
    except OSError:
        r = tk.Tk()
        r.withdraw()
        messagebox.showinfo("Traductor de Pantalla", "El traductor ya está abierto.")
        return
    try:
        App().correr()
    except Exception:
        log.exception("Error fatal")
        raise
    finally:
        candado.close()


if __name__ == "__main__":
    main()
