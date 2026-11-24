# -*- coding: utf-8 -*-
"""Tema visual del traductor: negro obsidiana, acentos rojo vino, texto blanco hueso y plata."""
import sys
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

OBSIDIANA = "#0e0c10"      # fondo de las ventanas
SUPERFICIE = "#18141b"     # campos, tablas, botones
SUPERFICIE_2 = "#231c27"   # al pasar el ratón
BORDE = "#30283a"
HUESO = "#ede4d3"          # texto
TENUE = "#a59c90"          # texto secundario
APAGADO = "#5e5562"        # desactivado
PLATA = "#c8cad2"
VINO = "#7a1530"           # acento
VINO_CLARO = "#a3233f"
VINO_OSCURO = "#520e1e"
VINO_TEXTO = "#e0566f"     # rojo vino legible sobre negro
ERROR = "#ff7a85"


def aplicar(root, tam=10):
    """Configura los estilos ttk y los colores de los widgets clásicos (tk)."""
    st = ttk.Style(root)
    try:
        st.theme_use("clam")
    except tk.TclError:
        pass
    fam = tkfont.nametofont("TkDefaultFont").actual("family")
    try:
        root.configure(bg=OBSIDIANA)
    except tk.TclError:
        pass
    for clave, valor in {
            "*Toplevel.background": OBSIDIANA, "*Canvas.background": OBSIDIANA,
            "*Text.background": SUPERFICIE, "*Text.foreground": HUESO, "*Text.insertBackground": HUESO,
            "*Text.selectBackground": VINO, "*Text.selectForeground": HUESO,
            "*Listbox.background": SUPERFICIE, "*Listbox.foreground": HUESO,
            "*Listbox.selectBackground": VINO, "*Listbox.selectForeground": HUESO,
            "*TCombobox*Listbox.background": SUPERFICIE, "*TCombobox*Listbox.foreground": HUESO,
            "*TCombobox*Listbox.selectBackground": VINO, "*TCombobox*Listbox.selectForeground": HUESO,
            "*Menu.background": SUPERFICIE, "*Menu.foreground": HUESO, "*Menu.activeBackground": VINO,
            "*Menu.activeForeground": HUESO, "*Menu.relief": "flat", "*Menu.borderWidth": 1}.items():
        root.option_add(clave, valor)
    pad = 4 + (tam - 10) // 2
    st.configure(".", background=OBSIDIANA, foreground=HUESO, fieldbackground=SUPERFICIE,
                 bordercolor=BORDE, lightcolor=BORDE, darkcolor=BORDE, troughcolor=SUPERFICIE,
                 selectbackground=VINO, selectforeground=HUESO, insertcolor=HUESO,
                 focuscolor=VINO_CLARO, arrowcolor=PLATA, relief="flat")
    st.map(".", foreground=[("disabled", APAGADO)])
    st.configure("TFrame", background=OBSIDIANA)
    st.configure("Tarjeta.TFrame", background=SUPERFICIE)
    st.configure("Linea.TFrame", background=VINO)
    st.configure("Linea2.TFrame", background=BORDE)
    st.configure("TLabel", background=OBSIDIANA, foreground=HUESO)
    st.configure("Tarjeta.TLabel", background=SUPERFICIE)
    st.configure("Tenue.TLabel", foreground=TENUE)
    st.configure("Titulo.TLabel", font=(fam, tam + 7, "bold"), foreground=HUESO)
    st.configure("Seccion.TLabel", font=(fam, max(8, tam - 1), "bold"), foreground=VINO_TEXTO)
    st.configure("Error.TLabel", foreground=ERROR)

    st.configure("TButton", background=SUPERFICIE, foreground=HUESO, bordercolor=BORDE,
                 lightcolor=SUPERFICIE, darkcolor=SUPERFICIE, padding=(12, pad), focusthickness=1)
    st.map("TButton",
           background=[("disabled", OBSIDIANA), ("pressed", VINO_OSCURO), ("active", SUPERFICIE_2)],
           bordercolor=[("disabled", SUPERFICIE), ("focus", VINO_CLARO), ("active", VINO)],
           lightcolor=[("pressed", VINO_OSCURO), ("active", SUPERFICIE_2)],
           darkcolor=[("pressed", VINO_OSCURO), ("active", SUPERFICIE_2)],
           foreground=[("disabled", APAGADO)])
    st.configure("Grande.TButton", padding=(14, pad + 3), anchor="w")
    st.configure("Doble.TButton", padding=(12, pad), anchor="w", justify="left")
    st.configure("Acento.TButton", background=VINO, bordercolor=VINO, lightcolor=VINO, darkcolor=VINO)
    st.map("Acento.TButton",
           background=[("disabled", "#2a1419"), ("pressed", VINO_OSCURO), ("active", VINO_CLARO)],
           lightcolor=[("pressed", VINO_OSCURO), ("active", VINO_CLARO)],
           darkcolor=[("pressed", VINO_OSCURO), ("active", VINO_CLARO)],
           bordercolor=[("focus", HUESO), ("active", VINO_CLARO)])
    st.configure("Grande.Acento.TButton", padding=(14, pad + 5), anchor="w", font=(fam, tam, "bold"))

    for w in ("TCheckbutton", "TRadiobutton"):
        st.configure(w, background=OBSIDIANA, foreground=HUESO, indicatorbackground=SUPERFICIE,
                     indicatorforeground=HUESO, upperbordercolor=BORDE, lowerbordercolor=BORDE,
                     focuscolor=OBSIDIANA)
        st.map(w, background=[("active", OBSIDIANA)],
               indicatorbackground=[("selected", VINO), ("pressed", VINO_OSCURO), ("active", SUPERFICIE_2)],
               upperbordercolor=[("selected", VINO_CLARO), ("focus", VINO_CLARO)],
               lowerbordercolor=[("selected", VINO_CLARO), ("focus", VINO_CLARO)],
               foreground=[("disabled", APAGADO), ("focus", HUESO)])
    try:
        _crear_indicadores(st, root, tam)
    except Exception:
        pass
    st.configure("Toolbutton", background=SUPERFICIE, foreground=TENUE, padding=(10, pad), anchor="center")
    st.map("Toolbutton", background=[("selected", VINO), ("active", SUPERFICIE_2)],
           foreground=[("selected", HUESO), ("active", HUESO)])

    st.configure("TCombobox", fieldbackground=SUPERFICIE, background=SUPERFICIE, foreground=HUESO,
                 arrowcolor=PLATA, bordercolor=BORDE, lightcolor=SUPERFICIE, darkcolor=SUPERFICIE,
                 padding=(6, 3))
    st.map("TCombobox", fieldbackground=[("readonly", SUPERFICIE), ("disabled", OBSIDIANA)],
           foreground=[("readonly", HUESO), ("disabled", APAGADO)],
           selectbackground=[("readonly", SUPERFICIE)], selectforeground=[("readonly", HUESO)],
           bordercolor=[("focus", VINO_CLARO)], background=[("active", SUPERFICIE_2)])
    for w in ("TEntry", "TSpinbox"):
        st.configure(w, fieldbackground=SUPERFICIE, foreground=HUESO, insertcolor=HUESO,
                     bordercolor=BORDE, lightcolor=SUPERFICIE, darkcolor=SUPERFICIE, background=SUPERFICIE,
                     arrowcolor=PLATA, padding=(6, 3))
        st.map(w, bordercolor=[("focus", VINO_CLARO)])

    st.configure("TNotebook", background=OBSIDIANA, bordercolor=BORDE, tabmargins=(2, 6, 2, 0))
    st.configure("TNotebook.Tab", background=OBSIDIANA, foreground=TENUE, padding=(14, 6),
                 bordercolor=BORDE, lightcolor=OBSIDIANA, darkcolor=OBSIDIANA)
    st.map("TNotebook.Tab", background=[("selected", SUPERFICIE), ("active", SUPERFICIE_2)],
           foreground=[("selected", HUESO), ("active", HUESO)],
           lightcolor=[("selected", VINO_CLARO)], bordercolor=[("selected", BORDE)])

    st.configure("Treeview", background=SUPERFICIE, fieldbackground=SUPERFICIE, foreground=HUESO,
                 bordercolor=BORDE, lightcolor=SUPERFICIE, darkcolor=SUPERFICIE, rowheight=int(tam * 2.4))
    st.map("Treeview", background=[("selected", VINO)], foreground=[("selected", HUESO)])
    st.configure("Treeview.Heading", background=OBSIDIANA, foreground=PLATA, bordercolor=BORDE,
                 lightcolor=OBSIDIANA, darkcolor=OBSIDIANA, relief="flat", font=(fam, tam, "bold"))
    st.map("Treeview.Heading", background=[("active", SUPERFICIE_2)])

    st.configure("TProgressbar", background=VINO_CLARO, troughcolor=SUPERFICIE, bordercolor=OBSIDIANA,
                 lightcolor=VINO_CLARO, darkcolor=VINO, thickness=4)
    # barra que solo se ve mientras se trabaja (quieta = del color del fondo, sin saltos de tamaño)
    st.configure("Fina.Horizontal.TProgressbar", background=VINO_CLARO, troughcolor=SUPERFICIE,
                 bordercolor=OBSIDIANA, lightcolor=VINO_CLARO, darkcolor=VINO, thickness=3)
    st.configure("Quieta.Horizontal.TProgressbar", background=OBSIDIANA, troughcolor=OBSIDIANA,
                 bordercolor=OBSIDIANA, lightcolor=OBSIDIANA, darkcolor=OBSIDIANA, thickness=3)
    # enlaces del pie (botones planos)
    st.configure("Enlace.TButton", background=OBSIDIANA, foreground=TENUE, bordercolor=OBSIDIANA,
                 lightcolor=OBSIDIANA, darkcolor=OBSIDIANA, padding=(6, 3), focusthickness=1)
    st.map("Enlace.TButton", foreground=[("disabled", APAGADO), ("active", HUESO)],
           background=[("pressed", VINO_OSCURO), ("active", SUPERFICIE)],
           bordercolor=[("focus", VINO_CLARO), ("active", SUPERFICIE)],
           lightcolor=[("active", SUPERFICIE)], darkcolor=[("active", SUPERFICIE)])
    st.configure("Lema.TLabel", foreground=PLATA)
    st.configure("TScale", background=VINO_CLARO, troughcolor=SUPERFICIE, bordercolor=BORDE,
                 lightcolor=VINO_CLARO, darkcolor=VINO)
    st.map("TScale", background=[("active", VINO_TEXTO)])
    st.configure("TLabelframe", background=OBSIDIANA, bordercolor=BORDE, lightcolor=BORDE, darkcolor=BORDE)
    st.configure("TLabelframe.Label", background=OBSIDIANA, foreground=PLATA, font=(fam, tam, "bold"))
    st.configure("TSeparator", background=BORDE)
    st.configure("TPanedwindow", background=OBSIDIANA)
    for w in ("Vertical.TScrollbar", "Horizontal.TScrollbar"):
        st.configure(w, background=SUPERFICIE_2, troughcolor=OBSIDIANA, bordercolor=OBSIDIANA,
                     lightcolor=SUPERFICIE_2, darkcolor=SUPERFICIE_2, arrowcolor=PLATA)
        st.map(w, background=[("active", VINO)])


def _rgb(hexa):
    return tuple(int(hexa[i:i + 2], 16) for i in (1, 3, 5))


def _indicadores(root, tam):
    """Casillas (✓) y botones de opción dibujados con los colores del tema."""
    from PIL import Image, ImageDraw, ImageTk
    n = max(14, int(tam * 1.55))
    k = 4                                           # dibujar grande y reducir = bordes suaves

    def hacer(forma, fondo, borde, marca=None):
        im = Image.new("RGBA", (n * k, n * k), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        caja = (k, k, n * k - k - 1, n * k - k - 1)
        if forma == "cuadro":
            d.rounded_rectangle(caja, radius=3 * k, fill=_rgb(fondo), outline=_rgb(borde), width=k + 1)
            if marca:
                d.line([(n * k * 0.26, n * k * 0.52), (n * k * 0.44, n * k * 0.70), (n * k * 0.76, n * k * 0.32)],
                       fill=_rgb(marca), width=int(k * 2.2), joint="curve")
        else:
            d.ellipse(caja, fill=_rgb(fondo), outline=_rgb(borde), width=k + 1)
            if marca:
                r = n * k * 0.22
                c = n * k / 2 - 0.5
                d.ellipse((c - r, c - r, c + r, c + r), fill=_rgb(marca))
        return ImageTk.PhotoImage(im.resize((n, n), Image.LANCZOS), master=root)

    imgs = {
        "c": hacer("cuadro", SUPERFICIE, BORDE), "c_h": hacer("cuadro", SUPERFICIE_2, VINO_CLARO),
        "c_s": hacer("cuadro", VINO, VINO_CLARO, HUESO), "c_d": hacer("cuadro", OBSIDIANA, SUPERFICIE_2),
        "c_sd": hacer("cuadro", SUPERFICIE_2, SUPERFICIE_2, APAGADO),
        "r": hacer("circulo", SUPERFICIE, BORDE), "r_h": hacer("circulo", SUPERFICIE_2, VINO_CLARO),
        "r_s": hacer("circulo", SUPERFICIE, VINO_CLARO, VINO_TEXTO), "r_d": hacer("circulo", OBSIDIANA, SUPERFICIE_2),
        "r_sd": hacer("circulo", OBSIDIANA, SUPERFICIE_2, APAGADO)}
    root._tema_imgs = imgs                           # que no se borren
    return imgs


def _crear_indicadores(st, root, tam):
    if getattr(root, "_tema_indicadores", None) == tam:
        return
    imgs = _indicadores(root, tam)
    sufijo = str(tam)
    for tipo, pre in (("Checkbutton", "c"), ("Radiobutton", "r")):
        nombre = f"Tema{sufijo}.{tipo}.indicator"
        st.element_create(nombre, "image", imgs[pre],
                          ("disabled", "selected", imgs[pre + "_sd"]), ("disabled", imgs[pre + "_d"]),
                          ("selected", imgs[pre + "_s"]), ("active", imgs[pre + "_h"]),
                          sticky="", padding=(0, 0, 7, 0))
        st.layout("T" + tipo, [(f"{tipo}.padding", {"sticky": "nswe", "children": [
            (nombre, {"side": "left", "sticky": ""}),
            (f"{tipo}.focus", {"side": "left", "sticky": "w",
                               "children": [(f"{tipo}.label", {"sticky": "nswe"})]})]})])
    root._tema_indicadores = tam


def _color(hexa):
    r, g, b = int(hexa[1:3], 16), int(hexa[3:5], 16), int(hexa[5:7], 16)
    return r | (g << 8) | (b << 16)


def barra_oscura(win):
    """Barra de título oscura (Windows 10/11) y, en Windows 11, con los colores del tema."""
    if not sys.platform.startswith("win"):
        return
    try:
        import ctypes
        win.update_idletasks()
        hwnd = ctypes.c_void_p(int(win.wm_frame(), 16))
        dwm = ctypes.windll.dwmapi
        si = ctypes.c_int(1)
        for atributo in (20, 19):                           # modo oscuro (nuevo / antiguo)
            if dwm.DwmSetWindowAttribute(hwnd, atributo, ctypes.byref(si), 4) == 0:
                break
        for atributo, color in ((35, OBSIDIANA), (36, HUESO), (34, VINO)):   # título, texto, borde
            c = ctypes.c_uint(_color(color))
            dwm.DwmSetWindowAttribute(hwnd, atributo, ctypes.byref(c), 4)
        # repintar el marco ya mismo
        ctypes.windll.user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x0020 | 0x0002 | 0x0001 | 0x0004 | 0x0010)
    except Exception:
        pass


# ============================================================================
# Botón dibujado: esquinas suaves, icono, título y atajo en una etiqueta
# ============================================================================
_COLORES_BOTON = {
    # variante: {estado: (arriba, abajo, borde, grosor_borde, texto, icono)}
    "principal": {
        "normal": ((142, 28, 58), (104, 16, 41), (255, 255, 255, 34), 1, HUESO, HUESO),
        "hover": ((168, 38, 68), (122, 21, 48), (255, 255, 255, 60), 1, HUESO, HUESO),
        "pressed": ((82, 14, 30), (70, 10, 26), (255, 255, 255, 30), 1, HUESO, HUESO),
        "disabled": ((44, 22, 28), (40, 20, 26), (255, 255, 255, 10), 1, APAGADO, APAGADO),
    },
    "tarjeta": {
        "normal": ((27, 22, 30), (22, 18, 25), (48, 40, 58, 255), 1, HUESO, PLATA),
        "hover": ((38, 30, 42), (31, 25, 35), (122, 21, 48, 255), 1, HUESO, VINO_TEXTO),
        "pressed": ((82, 14, 30), (70, 10, 26), (163, 35, 63, 255), 1, HUESO, HUESO),
        "disabled": ((19, 16, 21), (17, 14, 19), (32, 27, 37, 255), 1, APAGADO, APAGADO),
    },
    "peligro": {
        "normal": ((48, 16, 26), (38, 12, 20), (163, 35, 63, 255), 1, HUESO, VINO_TEXTO),
        "hover": ((72, 18, 34), (58, 14, 28), (224, 86, 111, 255), 1, HUESO, VINO_TEXTO),
        "pressed": ((82, 14, 30), (70, 10, 26), (163, 35, 63, 255), 1, HUESO, HUESO),
        "disabled": ((19, 16, 21), (17, 14, 19), (32, 27, 37, 255), 1, APAGADO, APAGADO),
    },
}


class Boton(tk.Canvas):
    """Botón de la ventana principal. Funciona con ratón y teclado (Tab, Enter, Espacio) y
    entiende state(["disabled"]) / state(["!disabled"]) como los botones ttk."""

    def __init__(self, master, comando, titulo="", atajo="", icono=None, variante="tarjeta",
                 fuente=None, fuente_atajo=None):
        super().__init__(master, highlightthickness=0, bd=0, bg=OBSIDIANA, takefocus=1, cursor="hand2")
        self.comando = comando
        self.titulo, self.atajo, self.icono = titulo, atajo, icono
        self.variante = variante
        self.f = fuente or tkfont.nametofont("TkDefaultFont")
        self.f_at = fuente_atajo or self.f
        self._estados = set()
        self._fotos = []
        self._ultimo = None
        ls = self.f.metrics("linespace")
        if variante == "principal":
            alto = int(ls * 2.9)
        else:
            alto = int(ls * 2 + self.f_at.metrics("linespace") + 18)
        self.configure(height=alto, width=10)
        for ev, fn in (("<Configure>", lambda e: self._dibujar()), ("<Enter>", lambda e: self._poner("hover", True)),
                       ("<Leave>", lambda e: (self._poner("hover", False), self._poner("pressed", False))),
                       ("<ButtonPress-1>", lambda e: self._poner("pressed", True)),
                       ("<ButtonRelease-1>", self._soltar), ("<FocusIn>", lambda e: self._poner("focus", True)),
                       ("<FocusOut>", lambda e: self._poner("focus", False)),
                       ("<Return>", lambda e: self.invoke()), ("<space>", lambda e: self.invoke())):
            self.bind(ev, fn)

    # ---- como un botón ttk ----
    def state(self, lista=None):
        if lista is None:
            return tuple(self._estados)
        for s in lista:
            if s.startswith("!"):
                self._estados.discard(s[1:])
            else:
                self._estados.add(s)
        self.configure(cursor="" if "disabled" in self._estados else "hand2",
                       takefocus=0 if "disabled" in self._estados else 1)
        self._dibujar()

    def instate(self, lista):
        return all((s[1:] not in self._estados) if s.startswith("!") else (s in self._estados) for s in lista)

    def invoke(self):
        if "disabled" not in self._estados and self.comando:
            self.comando()

    def poner_texto(self, titulo, atajo=""):
        if (titulo, atajo) != (self.titulo, self.atajo):
            self.titulo, self.atajo = titulo, atajo
            self._dibujar()

    def reajustar(self):
        """Recalcula el alto con la letra actual (al cambiar el tamaño de la interfaz)."""
        ls = self.f.metrics("linespace")
        if self.variante == "principal":
            alto = int(ls * 2.9)
        else:
            alto = int(ls * 2 + self.f_at.metrics("linespace") + 18)
        self._ultimo = None
        self.configure(height=alto)
        self._dibujar()

    def poner_variante(self, variante):
        if variante != self.variante:
            self.variante = variante
            self._dibujar()

    # ---- interno ----
    def _poner(self, estado, si):
        antes = estado in self._estados
        if si and not antes:
            self._estados.add(estado)
        elif not si and antes:
            self._estados.discard(estado)
        else:
            return
        self._dibujar()

    def _soltar(self, e):
        dentro = 0 <= e.x < self.winfo_width() and 0 <= e.y < self.winfo_height()
        self._poner("pressed", False)
        if dentro:
            self.invoke()

    def _clave_estado(self):
        if "disabled" in self._estados:
            return "disabled"
        if "pressed" in self._estados:
            return "pressed"
        return "hover" if "hover" in self._estados else "normal"

    def _dibujar(self):
        import marca
        from PIL import ImageTk
        w, h = self.winfo_width(), self.winfo_height()
        if w < 20 or h < 10:
            return
        est = self._clave_estado()
        foco = "focus" in self._estados and est != "disabled"
        firma = (w, h, est, foco, self.titulo, self.atajo, self.variante, self.icono)
        if firma == self._ultimo:
            return
        self._ultimo = firma
        arriba, abajo, borde, grosor, c_txt, c_ico = _COLORES_BOTON[self.variante][est]
        radio = max(6, int(h * (0.2 if self.variante == "principal" else 0.16)))
        if foco:
            borde, grosor = _rgb(HUESO if self.variante == "principal" else VINO_CLARO) + (255,), 2
        fondo = marca.rectangulo(w, h, radio, arriba, borde, grosor, degradado=abajo)
        self.delete("all")
        self._fotos = [ImageTk.PhotoImage(fondo, master=self)]
        self.create_image(0, 0, anchor="nw", image=self._fotos[0])
        ls = self.f.metrics("linespace")
        pad = max(10, int(ls * 0.75))
        x = pad
        if self.icono:
            lado = int(ls * (1.35 if self.variante == "principal" else 1.25))
            ico = ImageTk.PhotoImage(marca.icono_ui(self.icono, lado, _rgb(c_ico) if isinstance(c_ico, str)
                                                    else c_ico), master=self)
            self._fotos.append(ico)
            if self.variante == "principal":
                self.create_image(x, h // 2, anchor="w", image=ico)
            else:
                self.create_image(x, pad + ls // 2 + 1, anchor="w", image=ico)
            x += lado + int(ls * 0.6)
        color_txt = c_txt if isinstance(c_txt, str) else "#%02x%02x%02x" % c_txt
        chip_w = 0
        if self.atajo:
            chip_w = int(self.f_at.measure(self.atajo) + ls * 0.9)
            chip_h = int(self.f_at.metrics("linespace") + 6)
            if self.variante == "principal":
                cx, cy = w - pad - chip_w, (h - chip_h) // 2
                rel, bor, col = (0, 0, 0, 60), (255, 255, 255, 50), HUESO
            else:
                cx, cy = x, pad + ls + 5
                rel, bor, col = (255, 255, 255, 10), (255, 255, 255, 26), TENUE
            if est == "disabled":
                col = APAGADO
            chip = ImageTk.PhotoImage(marca.rectangulo(chip_w, chip_h, max(4, chip_h // 3), rel, bor, 1),
                                      master=self)
            self._fotos.append(chip)
            self.create_image(cx, cy, anchor="nw", image=chip)
            self.create_text(cx + chip_w // 2, cy + chip_h // 2, text=self.atajo, font=self.f_at, fill=col)
        if self.variante == "principal":
            self.create_text(x, h // 2, text=self.titulo, anchor="w", font=self.f, fill=color_txt,
                             width=max(40, w - x - pad - chip_w - pad))
        else:
            self.create_text(x, pad + ls // 2 + 1, text=self.titulo, anchor="w", font=self.f, fill=color_txt,
                             width=max(40, w - x - pad))


class Punto(tk.Canvas):
    """Indicador de estado: un punto de color que puede latir (traducción en vivo)."""
    COLORES = {"listo": PLATA, "vivo": VINO_TEXTO, "ocupado": VINO_TEXTO, "error": ERROR,
               "aviso": "#e0a85a", "cargando": APAGADO}

    def __init__(self, master, lado=10):
        super().__init__(master, width=lado, height=lado, highlightthickness=0, bd=0, bg=OBSIDIANA)
        self.lado = lado
        self.tipo = "cargando"
        self._fase = 0
        self._latido = None
        self._dibujar(self.COLORES["cargando"])

    def _dibujar(self, color):
        self._color = color
        try:
            self.delete("all")
            m = 1
            self.create_oval(m, m, self.lado - m, self.lado - m, fill=color, outline="")
        except tk.TclError:
            pass

    def redibujar(self):
        self._dibujar(getattr(self, "_color", PLATA))

    def poner(self, tipo):
        if tipo == self.tipo:
            return
        self.tipo = tipo
        if self._latido is not None:
            self.after_cancel(self._latido)
            self._latido = None
        self._dibujar(self.COLORES.get(tipo, PLATA))
        if tipo == "vivo":
            self._latir()

    def _latir(self):
        try:
            if not self.winfo_exists():
                return
            self._fase = 1 - self._fase
            self._dibujar(VINO_TEXTO if self._fase else VINO)
            self._latido = self.after(700, self._latir)
        except tk.TclError:
            self._latido = None


class Filete(tk.Canvas):
    """Línea fina de color vino que se desvanece hacia los lados."""

    def __init__(self, master, alto=2):
        super().__init__(master, height=alto, highlightthickness=0, bd=0, bg=OBSIDIANA)
        self._foto = None
        self._ancho = 0
        self.bind("<Configure>", lambda e: self._dibujar())

    def _dibujar(self):
        from PIL import Image, ImageTk
        import numpy as np
        w, h = self.winfo_width(), self.winfo_height()
        if w < 4 or w == self._ancho:
            return
        self._ancho = w
        t = np.linspace(0, 1, w)
        a = np.clip(np.minimum(t, 1 - t) * 3.2, 0, 1)
        fondo, vino = np.array(_rgb(OBSIDIANA)), np.array(_rgb(VINO_CLARO))
        fila = (fondo[None] * (1 - a[:, None]) + vino[None] * a[:, None]).astype(np.uint8)
        img = Image.fromarray(np.repeat(fila[None], h, 0), "RGB")
        self._foto = ImageTk.PhotoImage(img, master=self)
        self.delete("all")
        self.create_image(0, 0, anchor="nw", image=self._foto)
