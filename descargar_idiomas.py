# -*- coding: utf-8 -*-
"""
Descarga los "diccionarios" (modelos de traducción y de lectura de texto)
para trabajar sin internet:
  - Argos Translate: inglés->español, japonés->inglés (y japonés->español directo si existe)
  - EasyOCR: lectura de texto en japonés e inglés
Solo hace falta ejecutarlo una vez (los .bat/.sh de inicio lo hacen solos).
"""
PARES = [("en", "es"), ("ja", "es"), ("ja", "en")]


def descargar_traductores(avisar=print):
    from argostranslate import package
    avisar("Consultando el índice de idiomas…")
    package.update_package_index()
    disponibles = package.get_available_packages()
    instalados = {(p.from_code, p.to_code) for p in package.get_installed_packages()}
    for origen, destino in PARES:
        if (origen, destino) in instalados:
            avisar(f"  ✓ {origen}->{destino} ya instalado")
            continue
        if (origen, destino) == ("ja", "en") and ("ja", "es") in instalados:
            continue  # ya hay japonés->español directo
        paquete = next((p for p in disponibles
                        if p.from_code == origen and p.to_code == destino), None)
        if paquete is None:
            if (origen, destino) != ("ja", "es"):
                avisar(f"  ✗ No se encontró {origen}->{destino}")
            continue
        avisar(f"  Descargando {origen}->{destino}…")
        package.install_from_path(paquete.download())
        instalados.add((origen, destino))
        avisar(f"  ✓ {origen}->{destino} instalado")


def descargar_ocr(avisar=print):
    avisar("Descargando modelos de lectura (OCR) japonés + inglés…")
    import easyocr
    easyocr.Reader(["ja", "en"], gpu=False, verbose=False)
    easyocr.Reader(["en"], gpu=False, verbose=False)
    avisar("  ✓ OCR listo")


def probar(avisar=print):
    """Traduce dos frases de prueba para confirmar que todo quedó bien instalado."""
    from argostranslate import translate
    from nucleo import usar_separador_simple
    langs = {l.code: l for l in translate.get_installed_languages()}
    for origen, frase in (("en", "Welcome to the village, traveler."), ("ja", "ありがとうございます。")):
        try:
            tr = langs[origen].get_translation(langs["es"])
            usar_separador_simple(tr)
            avisar(f"  Prueba {origen}->es:  {frase}  =>  {tr.translate(frase)}")
        except Exception as e:
            avisar(f"  ✗ Prueba {origen}->es falló: {e}")


if __name__ == "__main__":
    descargar_traductores()
    descargar_ocr()
    probar()
    print("\nTodo listo. Ya puedes usar el traductor sin internet.")
