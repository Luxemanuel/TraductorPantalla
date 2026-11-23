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
