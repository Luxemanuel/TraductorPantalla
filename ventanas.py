# -*- coding: utf-8 -*-
"""Ventanas secundarias: superposición, panel de texto, configuración, glosario y ayuda."""
import copy
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

from PIL import Image, ImageDraw, ImageTk

import nucleo
from nucleo import ESQUEMAS, FUENTES, TAM_INTERFAZ, TECLAS_ATAJO, ACCIONES, atajo_legible


def _hex(rgb):
    return "#%02x%02x%02x" % rgb


def _mezcla(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def centrar(win, padre, ancho, alto):
    win.update_idletasks()
    try:
        px, py = padre.winfo_rootx(), padre.winfo_rooty()
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        x = min(max(0, px - ancho // 3), sw - ancho)
        y = min(max(0, py), sh - alto - 60)
    except tk.TclError:
        x, y = 100, 100
    win.geometry(f"{ancho}x{alto}+{x}+{y}")


# ============================================================================
# SUPERPOSICIÓN (traducción encima de las palabras)
# ============================================================================
class Superposicion:
    def __init__(self, app, mon, img_original, img_traducida, bloques):
        self.app = app
        self.bloques = bloques
        self.viendo_original = False
        w = self.win = tk.Toplevel(app.root)
        w.overrideredirect(True)
        w.geometry(f"{mon['width']}x{mon['height']}+{mon['left']}+{mon['top']}")
        w.attributes("-topmost", True)
        w.configure(bg="black", cursor="hand2")
        self.fotos = (ImageTk.PhotoImage(img_traducida), ImageTk.PhotoImage(img_original))
        self.lbl = tk.Label(w, image=self.fotos[0], bd=0, highlightthickness=0)
        self.lbl.pack(fill="both", expand=True)
        self.lbl.bind("<Button-1>", self.cerrar)
        self.lbl.bind("<Button-3>", self.cerrar)

        # Barra de botones (también se puede usar con el teclado)
        f = app.fuente_ui
        self.barra = tk.Frame(w, bg="#151515", padx=6, pady=5, highlightthickness=1,
                              highlightbackground="#555")
        estilo = dict(bg="#2a2a2a", fg="white", activebackground="#444", activeforeground="white",
                      relief="flat", padx=10, pady=4, font=f, cursor="arrow", bd=0,
                      highlightthickness=2, highlightcolor="#ffd400", highlightbackground="#151515")
        self.b_original = tk.Button(self.barra, text="Ver original (Espacio)", command=self.alternar, **estilo)
        botones = [
            tk.Button(self.barra, text="✕ Cerrar (Esc)", command=self.cerrar, **estilo),
            self.b_original,
            tk.Button(self.barra, text="Copiar texto (C)", command=self.copiar, **estilo),
            tk.Button(self.barra, text="Leer en voz alta (L)", command=self.leer, **estilo),
            tk.Button(self.barra, text="Ver como texto (P)", command=self.como_texto, **estilo),
            tk.Button(self.barra, text="Ocultar barra (H)", command=self.ocultar_barra, **estilo),
        ]
        for b in botones:
            b.pack(side="left", padx=3)
        self.barra.place(relx=0.5, rely=1.0, anchor="s", y=-14)
        self.barra_visible = True

        w.bind("<Escape>", self.cerrar)
        w.bind("<space>", self.alternar)
        for letra, fn in (("c", self.copiar), ("l", self.leer), ("p", self.como_texto),
                          ("h", self.ocultar_barra)):
            w.bind(f"<KeyPress-{letra}>", fn)
            w.bind(f"<KeyPress-{letra.upper()}>", fn)
        w.focus_force()
        w.after(120, self._enfocar)
        seg = int(app.cfg.get("cerrar_auto_seg") or 0)
        if seg > 0:
            w.after(seg * 1000, self.cerrar)

    def _enfocar(self):
        if self.existe():
            self.win.lift()
            self.win.focus_force()

    def existe(self):
        try:
            return self.win.winfo_exists()
        except tk.TclError:
            return False

    def alternar(self, _=None):
        self.viendo_original = not self.viendo_original
        self.lbl.configure(image=self.fotos[1 if self.viendo_original else 0])
        self.b_original.configure(text="Ver traducción (Espacio)" if self.viendo_original
                                  else "Ver original (Espacio)")
        return "break"

    def copiar(self, _=None):
        self.app.copiar_texto(texto_de(self.bloques))

    def leer(self, _=None):
        self.app.leer(texto_de(self.bloques))

    def como_texto(self, _=None):
        self.cerrar()
        self.app.panel.mostrar(self.bloques)

    def ocultar_barra(self, _=None):
        if self.barra_visible:
            self.barra.place_forget()
        else:
            self.barra.place(relx=0.5, rely=1.0, anchor="s", y=-14)
        self.barra_visible = not self.barra_visible

    def cerrar(self, _=None):
        if self.existe():
            self.win.destroy()
        self.app.al_cerrar_superposicion(self)


def texto_de(bloques):
    return "\n".join(b["traduccion"] for b in bloques)


# ============================================================================
# PANEL DE TEXTO (lectura cómoda, copiar, voz, corregir)
# ============================================================================
class Panel:
    def __init__(self, app):
        self.app = app
        self.win = None
        self.bloques = []

    def abierto(self):
        try:
            return self.win is not None and self.win.winfo_exists()
        except tk.TclError:
            return False

    def visible(self):
        return self.abierto() and self.win.state() != "withdrawn"

    def mostrar(self, bloques):
        self.bloques = bloques
        if not self.abierto():
            self._crear()
        self.refrescar()
        self.win.deiconify()
        self.win.lift()
        self.win.focus_force()
        self.txt.focus_set()

    def _crear(self):
        w = self.win = tk.Toplevel(self.app.root)
        w.title("Traducción — Traductor de Pantalla")
        w.minsize(320, 200)
        w.protocol("WM_DELETE_WINDOW", self.cerrar)
        w.attributes("-topmost", bool(self.app.cfg["siempre_visible"]))
        self.app.poner_icono(w)
        sw = w.winfo_screenwidth()
        w.geometry(f"640x460+{max(0, sw - 680)}+80")

        barra = ttk.Frame(w, padding=(8, 6))
        barra.pack(fill="x")
        for texto, fn, ancho in (("Copiar todo", self.copiar, None), ("Leer en voz alta", self.leer, None),
                                 ("Detener voz", self.app.voz.detener, None),
                                 ("A−", lambda: self.zoom(-2), 3), ("A+", lambda: self.zoom(2), 3)):
            ttk.Button(barra, text=texto, command=fn, width=ancho).pack(side="left", padx=2)
        ttk.Button(barra, text="Cerrar (Esc)", command=self.cerrar).pack(side="right", padx=2)
        ttk.Label(w, text="Doble clic sobre una frase para corregirla en tu glosario.",
                  foreground="#666", padding=(10, 0)).pack(anchor="w")

        marco = ttk.Frame(w)
        marco.pack(fill="both", expand=True, padx=8, pady=8)
        self.txt = tk.Text(marco, wrap="word", padx=14, pady=12, relief="flat", bd=0,
                           insertwidth=0, spacing3=4, undo=False)
        sb = ttk.Scrollbar(marco, command=self.txt.yview)
        self.txt.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.txt.pack(side="left", fill="both", expand=True)
        self.txt.bind("<Key>", self._solo_lectura)
        self.txt.bind("<Control-a>", self._todo)
        self.txt.bind("<Control-c>", self._copiar_sel)
        for t in ("<Control-plus>", "<Control-equal>", "<Control-KP_Add>"):
            w.bind(t, lambda e: self.zoom(2))
        for t in ("<Control-minus>", "<Control-KP_Subtract>"):
            w.bind(t, lambda e: self.zoom(-2))
        w.bind("<Escape>", lambda e: self.cerrar())

    def _solo_lectura(self, e):
        navegacion = {"Left", "Right", "Up", "Down", "Home", "End", "Prior", "Next", "Tab",
                      "Escape", "Shift_L", "Shift_R", "Control_L", "Control_R", "ISO_Left_Tab"}
        if e.keysym in navegacion or (e.state & 0x4):
            return None
        return "break"

    def _todo(self, _=None):
        self.txt.tag_add("sel", "1.0", "end")
        return "break"

    def _copiar_sel(self, _=None):
        try:
            texto = self.txt.get("sel.first", "sel.last")
        except tk.TclError:
            texto = texto_de(self.bloques)
        self.app.copiar_texto(texto)
        return "break"

    def zoom(self, delta):
        self.app.cfg["tam_panel"] = max(9, min(40, self.app.cfg["tam_panel"] + delta))
        self.app.guardar_cfg()
        self.refrescar()

    def refrescar(self):
        if not self.abierto():
            return
        cfg = self.app.cfg
        _, fondo, texto = ESQUEMAS.get(cfg["esquema"], ESQUEMAS["blanco_negro"])
        familia = FUENTES.get(cfg["fuente"], FUENTES["Arial"])[2]
        peso = "bold" if cfg["negrita"] else "normal"
        tam = cfg["tam_panel"]
        t = self.txt
        t.configure(bg=_hex(fondo), fg=_hex(texto), selectbackground="#3a6ff7",
                    selectforeground="white", font=(familia, tam, peso))
        t.tag_configure("trad", font=(familia, tam, peso), foreground=_hex(texto))
        t.tag_configure("orig", font=(familia, max(8, int(tam * 0.8)), "italic"),
                        foreground=_hex(_mezcla(texto, fondo, 0.45)))
        t.delete("1.0", "end")
        if not self.bloques:
            t.insert("end", "Aún no hay traducciones.", ("orig",))
        for i, b in enumerate(self.bloques):
            etiqueta = f"b{i}"
            t.insert("end", b["traduccion"] + "\n", ("trad", etiqueta))
            if cfg["mostrar_original_panel"]:
                t.insert("end", b["original"] + "\n", ("orig", etiqueta))
            t.insert("end", "\n")
            t.tag_bind(etiqueta, "<Double-Button-1>",
                       lambda e, b=b: self.app.abrir_glosario((b["idioma"], b["original"], b["traduccion"])))
        self.win.attributes("-topmost", bool(cfg["siempre_visible"]))

    def copiar(self):
        self.app.copiar_texto(texto_de(self.bloques))

    def leer(self):
        self.app.leer(texto_de(self.bloques))

    def ocultar(self):
        if self.abierto():
            self.win.withdraw()

    def cerrar(self):
        if self.abierto():
            self.win.destroy()
        self.win = None


# ============================================================================
# CONFIGURACIÓN
# ============================================================================
class VentanaConfig:
    def __init__(self, app):
        self.app = app
        self.cfg = copy.deepcopy(app.cfg)
        w = self.win = tk.Toplevel(app.root)
        w.title("Configuración — Traductor de Pantalla")
        w.transient(app.root)
        w.attributes("-topmost", True)
        app.poner_icono(w)
        w.protocol("WM_DELETE_WINDOW", self.cancelar)
        w.bind("<Escape>", lambda e: self.cancelar())
        self.v = {}

        nb = ttk.Notebook(w)
        nb.pack(fill="both", expand=True, padx=10, pady=(10, 0))
        nb.enable_traversal()  # Ctrl+Tab para cambiar de pestaña
        nb.add(self._pestana_general(nb), text="General", underline=0)
        nb.add(self._pestana_apariencia(nb), text="Apariencia", underline=0)
        nb.add(self._pestana_atajos(nb), text="Atajos de teclado", underline=1)
        nb.add(self._pestana_voz(nb), text="Voz", underline=0)

        pie = ttk.Frame(w, padding=10)
        pie.pack(fill="x")
        ttk.Button(pie, text="Restablecer valores", command=self.restablecer).pack(side="left")
        ttk.Button(pie, text="Guardar", command=self.guardar).pack(side="right")
        ttk.Button(pie, text="Cancelar", command=self.cancelar).pack(side="right", padx=6)
        centrar(w, app.root, 640, 600)
        self._vista_previa()

    # ---------- utilidades ----------
    def _var(self, clave, tipo=tk.StringVar):
        var = tipo(value=self.cfg[clave])
        var.trace_add("write", lambda *_: self._vista_previa())
        self.v[clave] = var
        return var

    def _fila(self, padre, texto, fila):
        ttk.Label(padre, text=texto).grid(row=fila, column=0, sticky="w", pady=5, padx=(0, 12))

    # ---------- pestañas ----------
    def _pestana_general(self, nb):
        f = ttk.Frame(nb, padding=14)
        self._fila(f, "Idioma de origen:", 0)
        cb = ttk.Combobox(f, state="readonly", width=28,
                          values=["Detectar automáticamente", "Inglés", "Japonés"])
        self._idiomas = {"Detectar automáticamente": "auto", "Inglés": "en", "Japonés": "ja"}
        cb.set({v: k for k, v in self._idiomas.items()}[self.cfg["idioma_origen"]])
        cb.grid(row=0, column=1, sticky="w")
        self.cb_idioma = cb

        self._fila(f, "Mostrar la traducción:", 1)
        cb2 = ttk.Combobox(f, state="readonly", width=28,
                           values=["Sobre las palabras", "En un panel de texto"])
        cb2.set("Sobre las palabras" if self.cfg["modo_vista"] == "superpuesto" else "En un panel de texto")
        cb2.grid(row=1, column=1, sticky="w")
        self.cb_vista = cb2

        self._fila(f, "Cerrar la traducción sola tras (segundos):", 2)
        ttk.Spinbox(f, from_=0, to=120, width=6, textvariable=self._var("cerrar_auto_seg", tk.IntVar)
                    ).grid(row=2, column=1, sticky="w")
        ttk.Label(f, text="0 = no se cierra sola", foreground="#666").grid(row=3, column=1, sticky="w")

        for i, (clave, texto) in enumerate((
                ("omitir_espanol", "No traducir lo que ya está en español"),
                ("siempre_visible", "Ventanas siempre visibles (encima de las demás)"),
                ("mostrar_original_panel", "En el panel, mostrar también el texto original")), start=4):
            ttk.Checkbutton(f, text=texto, variable=self._var(clave, tk.BooleanVar)
                            ).grid(row=i, column=0, columnspan=2, sticky="w", pady=4)
        self._fila(f, "Tamaño de la interfaz:", 8)
        ttk.Combobox(f, state="readonly", width=14, values=list(TAM_INTERFAZ),
                     textvariable=self._var("tam_interfaz")).grid(row=8, column=1, sticky="w")
        ttk.Separator(f).grid(row=9, column=0, columnspan=2, sticky="we", pady=10)
        ttk.Checkbutton(f, text="Guardar el historial de traducciones al cerrar el programa",
                        variable=self._var("guardar_historial", tk.BooleanVar)
                        ).grid(row=10, column=0, columnspan=2, sticky="w", pady=4)
        self._fila(f, "Máximo de frases en el historial:", 11)
        ttk.Spinbox(f, from_=50, to=3000, increment=50, width=7,
                    textvariable=self._var("historial_max", tk.IntVar)).grid(row=11, column=1, sticky="w")
        return f

    def _pestana_apariencia(self, nb):
        f = ttk.Frame(nb, padding=14)
        self._fila(f, "Colores:", 0)
        self._esquemas = {v[0]: k for k, v in ESQUEMAS.items()}
        self.v_esquema = tk.StringVar(value=ESQUEMAS[self.cfg["esquema"]][0])
        self.v_esquema.trace_add("write", lambda *_: self._vista_previa())
        ttk.Combobox(f, state="readonly", width=34, values=list(self._esquemas),
                     textvariable=self.v_esquema).grid(row=0, column=1, sticky="w")

        self._fila(f, "Fuente:", 1)
        ttk.Combobox(f, state="readonly", width=34, values=list(FUENTES),
                     textvariable=self._var("fuente")).grid(row=1, column=1, sticky="w")
        ttk.Checkbutton(f, text="Letra en negrita", variable=self._var("negrita", tk.BooleanVar)
                        ).grid(row=2, column=1, sticky="w", pady=4)

        for fila, (clave, texto, a, b, suf) in enumerate((
                ("opacidad", "Opacidad del fondo:", 30, 100, "%"),
                ("escala_texto", "Tamaño del texto traducido:", 60, 250, "%"),
                ("letra_minima", "Tamaño mínimo de letra:", 8, 40, " px")), start=3):
            self._fila(f, texto, fila)
            var = self._var(clave, tk.IntVar)
            caja = ttk.Frame(f)
            caja.grid(row=fila, column=1, sticky="we")
            lbl = ttk.Label(caja, width=6)
            esc = ttk.Scale(caja, from_=a, to=b, length=230,
                            command=lambda val, var=var: var.set(int(float(val))))
            esc.set(var.get())
            esc.pack(side="left")
            lbl.pack(side="left", padx=6)
            var.trace_add("write", lambda *_, l=lbl, v=var, s=suf: l.configure(text=f"{v.get()}{s}"))
            lbl.configure(text=f"{var.get()}{suf}")

        ttk.Label(f, text="Vista previa:").grid(row=6, column=0, sticky="nw", pady=(14, 0))
        self.lbl_previa = ttk.Label(f)
        self.lbl_previa.grid(row=6, column=1, sticky="w", pady=(14, 0))
        return f

    def _pestana_atajos(self, nb):
        f = ttk.Frame(nb, padding=14)
        self.v_atajos_activos = tk.BooleanVar(value=self.cfg["atajos_activos"])
        ttk.Checkbutton(f, text="Activar atajos de teclado globales (funcionan en cualquier programa)",
                        variable=self.v_atajos_activos).grid(row=0, column=0, columnspan=5, sticky="w", pady=(0, 10))
        self.v_at = {}
        for fila, (accion, (nombre, _)) in enumerate(ACCIONES.items(), start=1):
            at = self.cfg["atajos"][accion]
            ttk.Label(f, text=nombre + ":").grid(row=fila, column=0, sticky="w", pady=6, padx=(0, 10))
            vs = {k: tk.BooleanVar(value=at[k]) for k in ("ctrl", "alt", "shift")}
            vs["tecla"] = tk.StringVar(value=at["tecla"])
            for col, (k, t) in enumerate((("ctrl", "Ctrl"), ("alt", "Alt"), ("shift", "Mayús")), start=1):
                ttk.Checkbutton(f, text=t, variable=vs[k]).grid(row=fila, column=col, padx=2)
            ttk.Combobox(f, state="readonly", width=5, values=TECLAS_ATAJO,
                         textvariable=vs["tecla"]).grid(row=fila, column=4, padx=4)
            self.v_at[accion] = vs
        ttk.Label(f, foreground="#666", wraplength=560, justify="left",
                  text="Consejo: usa siempre Ctrl o Alt (o una tecla F) para no chocar con la escritura "
                       "normal. Dentro de la traducción también funcionan: Esc cerrar · Espacio ver "
                       "original · C copiar · L leer · P ver como texto · H ocultar barra."
                  ).grid(row=10, column=0, columnspan=5, sticky="w", pady=(14, 0))
        return f

    def _pestana_voz(self, nb):
        f = ttk.Frame(nb, padding=14)
        ttk.Checkbutton(f, text="Leer la traducción en voz alta automáticamente",
                        variable=self._var("voz_auto", tk.BooleanVar)).grid(row=0, column=0, columnspan=3, sticky="w")
        self._fila(f, "Velocidad de la voz:", 1)
        var = self._var("voz_velocidad", tk.IntVar)
        lbl = ttk.Label(f, width=4)
        esc = ttk.Scale(f, from_=-5, to=5, length=220, command=lambda val: var.set(round(float(val))))
        esc.set(var.get())
        esc.grid(row=1, column=1, sticky="w")
        lbl.grid(row=1, column=2, sticky="w", padx=6)
        var.trace_add("write", lambda *_: lbl.configure(text=str(var.get())))
        lbl.configure(text=str(var.get()))
        ttk.Button(f, text="Probar voz",
                   command=lambda: self.app.voz.decir("Hola. Esta es la voz del traductor de pantalla.",
                                                      self.v["voz_velocidad"].get())
                   ).grid(row=2, column=1, sticky="w", pady=10)
        aviso = ("La voz usa las voces en español instaladas en tu sistema."
                 if self.app.voz.disponible() else
                 "No encontré un sintetizador de voz en este sistema.")
        ttk.Label(f, text=aviso, foreground="#666", wraplength=520).grid(row=3, column=0, columnspan=3, sticky="w")
        return f

    # ---------- lógica ----------
    def _leer_formulario(self):
        c = copy.deepcopy(self.cfg)
        for clave, var in self.v.items():
            try:
                c[clave] = var.get()
            except tk.TclError:
                pass
        c["idioma_origen"] = self._idiomas[self.cb_idioma.get()]
        c["modo_vista"] = "superpuesto" if self.cb_vista.get() == "Sobre las palabras" else "panel"
        c["esquema"] = self._esquemas.get(self.v_esquema.get(), "blanco_negro")
        c["atajos_activos"] = self.v_atajos_activos.get()
        c["atajos"] = {a: {k: v.get() for k, v in vs.items()} for a, vs in self.v_at.items()}
        return c

    def _vista_previa(self):
        if not hasattr(self, "lbl_previa"):
            return
        try:
            c = self._leer_formulario()
        except Exception:
            return
        img = Image.new("RGB", (430, 150), (46, 64, 96))
        d = ImageDraw.Draw(img)
        fuente = nucleo.cargar_fuente("Arial", 22)
        d.text((14, 22), "Welcome to the village!", font=fuente, fill=(235, 235, 235))
        d.text((14, 92), "設定", font=fuente, fill=(255, 220, 120))
        bloques = [{"caja": (12, 20, 270, 50), "traduccion": "¡Bienvenido a la aldea!"},
                   {"caja": (12, 90, 60, 118), "traduccion": "Configuración"}]
        self._foto = ImageTk.PhotoImage(nucleo.componer(img, bloques, c))
        self.lbl_previa.configure(image=self._foto)

    def restablecer(self):
        if messagebox.askyesno("Restablecer", "¿Volver a la configuración original?", parent=self.win):
            self.win.destroy()
            self.app.config_win = None
            self.app.aplicar_config(copy.deepcopy(nucleo.DEFAULTS))
            self.app.abrir_config()

    def guardar(self):
        c = self._leer_formulario()
        vistos = {}
        for accion, at in c["atajos"].items():
            nombre = ACCIONES[accion][0]
            if not (at["ctrl"] or at["alt"] or at["tecla"].startswith("F") and len(at["tecla"]) > 1):
                messagebox.showwarning("Atajo no válido",
                                       f"«{nombre}» necesita Ctrl o Alt (o usar una tecla F1–F12).",
                                       parent=self.win)
                return
            legible = atajo_legible(at)
            if legible in vistos:
                messagebox.showwarning("Atajo repetido",
                                       f"{legible} está asignado a «{vistos[legible]}» y a «{nombre}».",
                                       parent=self.win)
                return
            vistos[legible] = nombre
        self.win.destroy()
        self.app.config_win = None
        self.app.aplicar_config(c)

    def cancelar(self):
        self.win.destroy()
        self.app.config_win = None


# ============================================================================
# EDITOR DEL GLOSARIO
# ============================================================================
class EditorGlosario:
    NOMBRES = {"en": "Inglés", "ja": "Japonés"}

    def __init__(self, app, relleno=None):
        self.app = app
        self.datos = nucleo.leer_glosario()
        self.cambios = False
        w = self.win = tk.Toplevel(app.root)
        w.title("Mi glosario — Traductor de Pantalla")
        w.attributes("-topmost", True)
        app.poner_icono(w)
        w.protocol("WM_DELETE_WINDOW", self.cerrar)
        w.bind("<Escape>", lambda e: self.cerrar())

        arriba = ttk.Frame(w, padding=(10, 10, 10, 4))
        arriba.pack(fill="x")
        ttk.Label(arriba, text="Idioma:").pack(side="left")
        self.cb = ttk.Combobox(arriba, state="readonly", width=10, values=list(self.NOMBRES.values()))
        self.cb.set("Inglés")
        self.cb.pack(side="left", padx=(4, 16))
        self.cb.bind("<<ComboboxSelected>>", lambda e: self.llenar())
        ttk.Label(arriba, text="Buscar:").pack(side="left")
        self.v_buscar = tk.StringVar()
        self.v_buscar.trace_add("write", lambda *_: self.llenar())
        ttk.Entry(arriba, textvariable=self.v_buscar, width=24).pack(side="left", padx=4)

        ttk.Label(w, padding=(10, 0), foreground="#666", wraplength=600,
                  text="Lo que pongas aquí se usa ANTES que el traductor automático. "
                       "Ideal para nombres, menús de juegos y frases que se traducen mal.").pack(anchor="w")

        medio = ttk.Frame(w, padding=10)
        medio.pack(fill="both", expand=True)
        self.tabla = ttk.Treeview(medio, columns=("o", "t"), show="headings", height=12, selectmode="browse")
        self.tabla.heading("o", text="Original")
        self.tabla.heading("t", text="Traducción")
        self.tabla.column("o", width=260)
        self.tabla.column("t", width=300)
        sb = ttk.Scrollbar(medio, command=self.tabla.yview)
        self.tabla.configure(yscrollcommand=sb.set)
        self.tabla.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tabla.bind("<<TreeviewSelect>>", self._seleccion)
        self.tabla.bind("<Delete>", lambda e: self.eliminar())

        form = ttk.Frame(w, padding=(10, 0))
        form.pack(fill="x")
        ttk.Label(form, text="Original:").grid(row=0, column=0, sticky="w")
        ttk.Label(form, text="Traducción:").grid(row=1, column=0, sticky="w")
        self.v_o, self.v_t = tk.StringVar(), tk.StringVar()
        self.e_o = ttk.Entry(form, textvariable=self.v_o, width=60)
        self.e_o.grid(row=0, column=1, sticky="we", pady=3)
        e_t = ttk.Entry(form, textvariable=self.v_t, width=60)
        e_t.grid(row=1, column=1, sticky="we", pady=3)
        e_t.bind("<Return>", lambda e: self.agregar())
        form.columnconfigure(1, weight=1)

        bot = ttk.Frame(w, padding=10)
        bot.pack(fill="x")
        ttk.Button(bot, text="Agregar / actualizar (Enter)", command=self.agregar).pack(side="left")
        ttk.Button(bot, text="Eliminar seleccionado (Supr)", command=self.eliminar).pack(side="left", padx=6)
        ttk.Button(bot, text="Guardar y cerrar", command=self.guardar).pack(side="right")
        ttk.Button(bot, text="Cancelar", command=self.cerrar).pack(side="right", padx=6)
        centrar(w, app.root, 820, 560)
        w.minsize(700, 420)
        self.llenar()
        if relleno:
            self.rellenar(*relleno)

    def idioma(self):
        return "ja" if self.cb.get() == "Japonés" else "en"

    def rellenar(self, idioma, original, traduccion):
        self.cb.set(self.NOMBRES.get(idioma, "Inglés"))
        self.llenar()
        self.v_o.set(original)
        self.v_t.set(traduccion)
        self.win.lift()
        self.e_o.focus_set()

    def llenar(self):
        self.tabla.delete(*self.tabla.get_children())
        filtro = self.v_buscar.get().lower()
        for o, t in sorted(self.datos[self.idioma()].items(), key=lambda kv: kv[0].lower()):
            if filtro in o.lower() or filtro in t.lower():
                self.tabla.insert("", "end", values=(o, t))

    def _seleccion(self, _=None):
        sel = self.tabla.selection()
        if sel:
            o, t = self.tabla.item(sel[0], "values")
            self.v_o.set(o)
            self.v_t.set(t)

    def agregar(self):
        o, t = self.v_o.get().strip(), self.v_t.get().strip()
        if not o or not t:
            messagebox.showinfo("Glosario", "Escribe el texto original y su traducción.", parent=self.win)
            return
        self.datos[self.idioma()][o] = t
        self.cambios = True
        self.v_o.set("")
        self.v_t.set("")
        self.llenar()
        self.e_o.focus_set()

    def eliminar(self):
        sel = self.tabla.selection()
        if not sel:
            return
        o = self.tabla.item(sel[0], "values")[0]
        self.datos[self.idioma()].pop(o, None)
        self.cambios = True
        self.llenar()

    def guardar(self):
        try:
            nucleo.guardar_glosario(self.datos)
        except Exception as e:
            messagebox.showerror("Glosario", f"No se pudo guardar: {e}", parent=self.win)
            return
        self.app.motor.cargar_glosario()
        self.app.estado("Glosario guardado.")
        self.cambios = False
        self.cerrar()

    def cerrar(self):
        if self.cambios and not messagebox.askyesno(
                "Glosario", "Tienes cambios sin guardar. ¿Salir sin guardar?", parent=self.win):
            return
        self.win.destroy()
        self.app.glosario_win = None


# ============================================================================
# HISTORIAL
# ============================================================================
class VentanaHistorial:
    IDIOMAS = {"Todos": None, "Inglés": "en", "Japonés": "ja"}
    NOMBRE_IDIOMA = {"en": "Inglés", "ja": "Japonés"}

    def __init__(self, app):
        self.app = app
        self.hist = app.historial
        self.indices = []            # fila de la tabla -> índice en el historial
        w = self.win = tk.Toplevel(app.root)
        w.title("Historial — Traductor de Pantalla")
        w.attributes("-topmost", bool(app.cfg["siempre_visible"]))
        app.poner_icono(w)
        w.protocol("WM_DELETE_WINDOW", self.cerrar)
        w.bind("<Escape>", lambda e: self.cerrar())

        arriba = ttk.Frame(w, padding=(10, 10, 10, 4))
        arriba.pack(fill="x")
        ttk.Label(arriba, text="Buscar:").pack(side="left")
        self.v_buscar = tk.StringVar()
        self.v_buscar.trace_add("write", lambda *_: self.llenar())
        e = ttk.Entry(arriba, textvariable=self.v_buscar, width=30)
        e.pack(side="left", padx=(4, 16))
        ttk.Label(arriba, text="Idioma:").pack(side="left")
        self.cb = ttk.Combobox(arriba, state="readonly", width=9, values=list(self.IDIOMAS))
        self.cb.set("Todos")
        self.cb.pack(side="left", padx=4)
        self.cb.bind("<<ComboboxSelected>>", lambda e: self.llenar())
        self.lbl_total = ttk.Label(arriba, foreground="#666")
        self.lbl_total.pack(side="right")

        medio = ttk.Panedwindow(w, orient="vertical")
        medio.pack(fill="both", expand=True, padx=10, pady=6)
        marco = ttk.Frame(medio)
        self.tabla = ttk.Treeview(marco, columns=("h", "i", "t", "o"), show="headings",
                                  selectmode="extended", height=12)
        for col, txt, ancho, est in (("h", "Hora", 120, False), ("i", "Idioma", 70, False),
                                     ("t", "Traducción", 330, True), ("o", "Original", 260, True)):
            self.tabla.heading(col, text=txt)
            self.tabla.column(col, width=ancho, stretch=est)
        sb = ttk.Scrollbar(marco, command=self.tabla.yview)
        self.tabla.configure(yscrollcommand=sb.set)
        self.tabla.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        medio.add(marco, weight=3)

        _, fondo, color = ESQUEMAS.get(app.cfg["esquema"], ESQUEMAS["blanco_negro"])
        familia = FUENTES.get(app.cfg["fuente"], FUENTES["Arial"])[2]
        tam = app.cfg["tam_panel"]
        self.detalle = tk.Text(medio, wrap="word", height=6, padx=12, pady=8, relief="flat",
                               bg=_hex(fondo), fg=_hex(color), insertwidth=0,
                               font=(familia, tam, "bold" if app.cfg["negrita"] else "normal"))
        self.detalle.tag_configure("orig", font=(familia, max(8, int(tam * 0.8)), "italic"),
                                   foreground=_hex(_mezcla(color, fondo, 0.45)))
        self.detalle.bind("<Key>", lambda e: None if (e.state & 0x4 or e.keysym in
                          ("Left", "Right", "Up", "Down", "Tab", "Home", "End")) else "break")
        medio.add(self.detalle, weight=1)

        bot = ttk.Frame(w, padding=(10, 4, 10, 2))
        bot.pack(fill="x")
        for texto, fn in (("Copiar traducción", lambda: self.copiar("traduccion")),
                          ("Copiar original", lambda: self.copiar("original")),
                          ("Leer en voz alta", self.leer),
                          ("Corregir en glosario", self.corregir)):
            ttk.Button(bot, text=texto, command=fn).pack(side="left", padx=2)
        bot2 = ttk.Frame(w, padding=(10, 2, 10, 10))
        bot2.pack(fill="x")
        ttk.Button(bot2, text="Exportar a texto…", command=self.exportar).pack(side="left", padx=2)
        ttk.Button(bot2, text="Borrar selección (Supr)", command=self.borrar).pack(side="left", padx=2)
        ttk.Button(bot2, text="Vaciar todo", command=self.vaciar).pack(side="left", padx=2)
        ttk.Button(bot2, text="Cerrar (Esc)", command=self.cerrar).pack(side="right", padx=2)

        self.tabla.bind("<<TreeviewSelect>>", lambda e: self.mostrar_detalle())
        self.tabla.bind("<Double-Button-1>", lambda e: self.copiar("traduccion"))
        self.tabla.bind("<Return>", lambda e: self.copiar("traduccion"))
        self.tabla.bind("<Control-c>", lambda e: self.copiar("traduccion"))
        self.tabla.bind("<Control-a>", lambda e: (self.tabla.selection_set(self.tabla.get_children()), "break")[1])
        self.tabla.bind("<Delete>", lambda e: self.borrar())
        centrar(w, app.root, 960, 640)
        w.minsize(760, 460)
        self.llenar()
        e.focus_set()

    def existe(self):
        try:
            return self.win.winfo_exists()
        except tk.TclError:
            return False

    def llenar(self):
        if not self.existe():
            return
        self.tabla.delete(*self.tabla.get_children())
        self.indices = []
        filtro = self.v_buscar.get().lower().strip()
        idioma = self.IDIOMAS.get(self.cb.get())
        for i, e in enumerate(self.hist.entradas):
            if idioma and e["idioma"] != idioma:
                continue
            if filtro and filtro not in e["original"].lower() and filtro not in e["traduccion"].lower():
                continue
            hora = e["fecha"][5:16].replace("-", "/", 1)       # MM/DD HH:MM
            self.tabla.insert("", "end", iid=str(i), values=(
                hora, self.NOMBRE_IDIOMA.get(e["idioma"], e["idioma"]),
                e["traduccion"].replace("\n", " "), e["original"].replace("\n", " ")))
            self.indices.append(i)
        total = len(self.hist.entradas)
        self.lbl_total.configure(text=f"{len(self.indices)} de {total} frases")
        hijos = self.tabla.get_children()
        if hijos:
            self.tabla.selection_set(hijos[0])
            self.tabla.focus(hijos[0])
        self.mostrar_detalle()

    def seleccion(self):
        return [self.hist.entradas[int(i)] for i in self.tabla.selection()
                if int(i) < len(self.hist.entradas)]

    def mostrar_detalle(self):
        sel = self.seleccion()
        t = self.detalle
        t.delete("1.0", "end")
        if not sel:
            t.insert("end", "Aún no hay traducciones en el historial." if not self.hist.entradas
                     else "Selecciona una frase para verla completa.", ("orig",))
            return
        e = sel[0]
        t.insert("end", e["traduccion"] + "\n")
        t.insert("end", e["original"] + f"\n({e['fecha']} · {self.NOMBRE_IDIOMA.get(e['idioma'], '')} · "
                 f"{nucleo.Historial.FUENTES.get(e.get('fuente'), '')})", ("orig",))

    def copiar(self, campo):
        sel = self.seleccion()
        if sel:
            self.app.copiar_texto("\n".join(e[campo] for e in sel))
        return "break"

    def leer(self):
        sel = self.seleccion()
        if sel:
            self.app.leer("\n".join(e["traduccion"] for e in sel))

    def corregir(self):
        sel = self.seleccion()
        if sel:
            e = sel[0]
            self.app.abrir_glosario((e["idioma"], e["original"], e["traduccion"]))

    def borrar(self):
        sel = [int(i) for i in self.tabla.selection()]
        if sel:
            self.hist.borrar(sel)

    def vaciar(self):
        if self.hist.entradas and messagebox.askyesno(
                "Historial", "¿Borrar todo el historial de traducciones?", parent=self.win):
            self.hist.vaciar()

    def exportar(self):
        sel = self.seleccion()
        entradas = sel if len(sel) > 1 else [self.hist.entradas[i] for i in self.indices]
        if not entradas:
            return
        ruta = filedialog.asksaveasfilename(parent=self.win, title="Exportar historial",
                                            defaultextension=".txt", initialfile="traducciones.txt",
                                            filetypes=[("Texto", "*.txt")])
        if not ruta:
            return
        with open(ruta, "w", encoding="utf-8-sig") as f:
            for e in entradas:
                f.write(f"[{e['fecha']}] ({self.NOMBRE_IDIOMA.get(e['idioma'], '')})\n"
                        f"{e['original']}\n→ {e['traduccion']}\n\n")
        self.app.estado(f"Exportadas {len(entradas)} frases a {ruta}")

    def cerrar(self):
        if self.existe():
            self.win.destroy()
        self.app.historial_win = None


# ============================================================================
# AYUDA
# ============================================================================
def ventana_ayuda(app):
    w = tk.Toplevel(app.root)
    w.title("Ayuda — Traductor de Pantalla")
    w.attributes("-topmost", True)
    app.poner_icono(w)
    at = {a: atajo_legible(app.cfg["atajos"][a]) for a in ACCIONES}
    texto = f"""ATAJOS GLOBALES (funcionan en cualquier programa)
  {at['pantalla']:<14} Traducir la pantalla completa
  {at['zona']:<14} Elegir una zona con el ratón y traducirla
  {at['repetir']:<14} Volver a traducir la última zona (ideal para subtítulos y juegos)
  {at['portapapeles']:<14} Traducir lo que copiaste con Ctrl+C (sin leer la pantalla)
  {at['detener']:<14} Detener: cancela, cierra la traducción y calla la voz

DOS IDIOMAS A LA VEZ
  Con "Detectar solo", cada frase se clasifica por separado: si en la
  pantalla hay inglés y japonés mezclados, se traducen los dos de una vez.
  Elegir "Inglés" o "Japonés" solo sirve para ignorar el otro idioma.

HISTORIAL
  Guarda todo lo traducido (lo más reciente arriba). Puedes buscar,
  filtrar por idioma, copiar, leer en voz alta, corregir en el glosario,
  borrar y exportar a un archivo de texto.
  Enter o doble clic ... copiar la traducción seleccionada
  Ctrl+C .............. copiar las traducciones seleccionadas
  Supr ................ borrar las seleccionadas

DENTRO DE LA TRADUCCIÓN
  Esc o clic       Cerrar
  Espacio          Alternar entre traducción y texto original
  C                Copiar todo el texto traducido
  L                Leer en voz alta
  P                Ver como texto (panel)
  H                Ocultar / mostrar la barra de botones

EN LA VENTANA PRINCIPAL
  Tab / Mayús+Tab  Moverse entre botones
  Enter o Espacio  Pulsar el botón seleccionado

PANEL DE TEXTO
  Ctrl + / Ctrl −  Agrandar / achicar la letra
  Ctrl+C           Copiar (lo seleccionado o todo)
  Doble clic       Corregir esa frase en tu glosario

CONSEJOS
  · "Traducir una zona" es más rápido y preciso que la pantalla completa.
  · Si una frase sale mal, corrígela en el glosario: la próxima vez saldrá bien.
  · En Configuración puedes cambiar colores (alto contraste), tamaño de letra,
    fuente, atajos, voz y que la traducción se cierre sola.
  · Si algo falla, revisa el archivo registro.txt en la carpeta del programa.
"""
    t = tk.Text(w, wrap="word", padx=16, pady=14, relief="flat", font=app.fuente_mono, width=78, height=30)
    t.insert("1.0", texto)
    t.configure(state="disabled")
    t.pack(fill="both", expand=True)
    ttk.Button(w, text="Cerrar", command=w.destroy).pack(pady=8)
    w.bind("<Escape>", lambda e: w.destroy())
    w.focus_force()
