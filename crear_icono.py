# Genera icono.ico para el .exe (mismo diseño que el icono de la ventana)
from PIL import Image, ImageDraw, ImageFont
def fuente(t):
    for n in ("arialbd.ttf", "DejaVuSans-Bold.ttf", "arial.ttf"):
        try: return ImageFont.truetype(n, t)
        except Exception: pass
    return ImageFont.load_default()
img = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
d.rounded_rectangle((8, 8, 248, 248), radius=56, fill=(74, 8, 22))
f = fuente(120)
d.text((40, 56), "A", font=f, fill=(245, 238, 220))
d.text((128, 88), "ñ", font=f, fill=(255, 210, 90))
img.save("icono.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print("icono.ico creado")
