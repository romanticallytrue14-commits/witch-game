import math
from PIL import Image, ImageDraw

def gradient(size, top, bottom):
    g = Image.new('RGB', (1, size))
    px = g.load()
    for y in range(size):
        t = y / (size - 1)
        px[0, y] = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
    return g.resize((size, size))

def draw_icon(size):
    img = gradient(size, (38, 23, 78), (16, 10, 30)).convert('RGBA')
    d = ImageDraw.Draw(img)
    s = float(size)
    # inner panel outline
    m = s * 0.09
    d.rounded_rectangle([m, m, s - m, s - m], radius=s * 0.14,
                        outline=(86, 66, 148, 110), width=max(1, int(s * 0.007)))
    # main pink wave (glow layers)
    mid, amp = s * 0.40, s * 0.135
    pts = [(x, mid + math.sin((x / s) * math.pi * 2 * 2.0 + 0.4) * amp)
           for x in range(0, int(s) + 1, 2)]
    for w, a in ((s * 0.115, 22), (s * 0.062, 62), (s * 0.030, 255)):
        d.line(pts, fill=(255, 93, 177, int(a)), width=max(1, int(w)), joint='curve')
    # smaller cyan wave
    mid2, amp2 = s * 0.40, s * 0.07
    pts2 = [(x, mid2 + math.sin((x / s) * math.pi * 2 * 2.0 + 2.3) * amp2)
            for x in range(0, int(s) + 1, 2)]
    for w, a in ((s * 0.05, 40), (s * 0.016, 230)):
        d.line(pts2, fill=(89, 227, 255, int(a)), width=max(1, int(w)), joint='curve')
    # three knobs
    yk = s * 0.72
    xs = [s * 0.30, s * 0.50, s * 0.70]
    cols = [(255, 93, 177), (89, 227, 255), (255, 209, 102)]
    r = s * 0.072
    for i, x in enumerate(xs):
        c = cols[i]
        d.ellipse([x - r, yk - r, x + r, yk + r], fill=(24, 16, 46, 255),
                  outline=c + (255,), width=max(2, int(s * 0.011)))
        ang = -2.4 + i * 0.55
        lx = x + math.cos(ang) * r * 0.72
        ly = yk + math.sin(ang) * r * 0.72
        d.line([x, yk, lx, ly], fill=c + (255,), width=max(2, int(s * 0.013)))
    # status LED
    ex, ey, er = s * 0.845, s * 0.155, s * 0.022
    d.ellipse([ex - er * 2.4, ey - er * 2.4, ex + er * 2.4, ey + er * 2.4], fill=(124, 255, 178, 60))
    d.ellipse([ex - er, ey - er, ex + er, ey + er], fill=(124, 255, 178, 255))
    return img

def rounded(img, radius):
    mask = Image.new('L', img.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, img.size[0] - 1, img.size[1] - 1],
                                           radius=radius, fill=255)
    out = img.copy()
    out.putalpha(mask)
    return out

base = draw_icon(512)

out_dir = 'icons'
import os
os.makedirs(out_dir, exist_ok=True)

for name, size in (('icon-512.png', 512), ('icon-192.png', 192)):
    im = base if size == 512 else base.resize((size, size), Image.LANCZOS)
    rounded(im, int(size * 0.18)).save(f'{out_dir}/{name}')
for name, size in (('icon-512-maskable.png', 512), ('icon-192-maskable.png', 192)):
    im = base if size == 512 else base.resize((size, size), Image.LANCZOS)
    im.save(f'{out_dir}/{name}')

apple = base.resize((180, 180), Image.LANCZOS)
apple.convert('RGB').save('apple-touch-icon.png')
print('icons done')
