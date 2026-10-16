from pathlib import Path
from PIL import Image, ImageDraw

root = Path(__file__).resolve().parents[1]
(root / 'assets').mkdir(exist_ok=True)
im = Image.new('RGBA', (256, 256), '#172640')
d = ImageDraw.Draw(im)
d.rounded_rectangle((48, 38, 208, 218), radius=22, fill='#4568ed')
d.line((82, 90, 174, 90), fill='white', width=12)
d.line((82, 127, 155, 127), fill='white', width=12)
d.line((82, 164, 136, 164), fill='#70e2c3', width=12)
im.save(root / 'assets' / 'app.ico', sizes=[(16,16),(32,32),(48,48),(64,64),(128,128),(256,256)])
