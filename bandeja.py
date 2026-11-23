# -*- coding: utf-8 -*-
"""Icono junto al reloj de Windows con un menú de todas las acciones."""
import threading

import pystray


class Bandeja:
    def __init__(self, app):
        self.app = app
        cola = app.cola

        def atajo(accion):
            return lambda icono, item: cola.put(("atajo", accion))

        def abrir(que):
            return lambda icono, item: cola.put(("abrir", que))

        M = pystray.MenuItem
        menu = pystray.Menu(
            M("Mostrar el traductor", lambda i, it: cola.put(("mostrar", None)), default=True),
            pystray.Menu.SEPARATOR,
            M("Anclar a un juego o programa", atajo("anclar")),
            M("Elegir zona en vivo", atajo("zona")),
            M("Subtítulos de pantalla", atajo("subtitulos")),
            M("Subtítulos de voz", atajo("voz")),
            M("Volver a la última zona", atajo("repetir")),
            M("Detener", atajo("detener")),
            pystray.Menu.SEPARATOR,
            M("Historial", abrir("historial")),
            M("Ajustes", abrir("config")),
            M("Salir", abrir("salir")),
        )
        self.icono = pystray.Icon("TraductorPantalla", app._icono_pil, "Traductor de Pantalla", menu)
        self.hilo = threading.Thread(target=self.icono.run, daemon=True)
        self.hilo.start()

    def cerrar(self):
        try:
            self.icono.stop()
        except Exception:
            pass
