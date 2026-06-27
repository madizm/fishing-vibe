"""生成小程序地图 marker 图标。

设计目标：
- 使用「水滴定位针」造型，尖端能准确指向经纬度；
- 白色描边 + 轻阴影，兼容矢量地图和卫星图底色；
- 同时输出 1x / 2x PNG，map 组件使用 @2x 资源缩放显示更清晰。
"""
import math
import os
import struct
import zlib


BANDS = [
    ('优', '#0f9f6e'),
    ('良', '#2f7ed8'),
    ('中', '#f59e0b'),
    ('低', '#ef4444'),
    ('na', '#64748b'),
]


# 逻辑尺寸（1x）：32 × 40；实际显示时在 map marker 中缩放到 28 × 36。
BASE_W = 32
BASE_H = 40
SUPERSAMPLE = 4


def create_png(width, height, pixels):
    """pixels: list of (r,g,b,a) tuples, row by row, top to bottom."""

    def make_chunk(chunk_type, data):
        chunk = chunk_type + data
        return (
            struct.pack('>I', len(data))
            + chunk
            + struct.pack('>I', zlib.crc32(chunk) & 0xFFFFFFFF)
        )

    raw_rows = []
    for y in range(height):
        row = bytearray([0])  # PNG filter: None
        for x in range(width):
            row.extend(pixels[y * width + x])
        raw_rows.append(bytes(row))

    sig = b'\x89PNG\r\n\x1a\n'
    ihdr = make_chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0))
    idat = make_chunk(b'IDAT', zlib.compress(b''.join(raw_rows), level=9))
    iend = make_chunk(b'IEND', b'')
    return sig + ihdr + idat + iend


def hex_rgb(color):
    color = color.lstrip('#')
    return tuple(int(color[i:i + 2], 16) for i in (0, 2, 4))


def over(dst, src):
    """Alpha-composite src over dst. Channels are 0–255 straight alpha."""
    sr, sg, sb, sa = src
    if sa <= 0:
        return dst
    dr, dg, db, da = dst
    if da <= 0:
        return src

    sa_f = sa / 255
    da_f = da / 255
    out_a = sa_f + da_f * (1 - sa_f)
    if out_a <= 0:
        return (0, 0, 0, 0)

    r = int(round((sr * sa_f + dr * da_f * (1 - sa_f)) / out_a))
    g = int(round((sg * sa_f + dg * da_f * (1 - sa_f)) / out_a))
    b = int(round((sb * sa_f + db * da_f * (1 - sa_f)) / out_a))
    a = int(round(out_a * 255))
    return (r, g, b, a)


def point_in_triangle(px, py, ax, ay, bx, by, cx, cy):
    def sign(x1, y1, x2, y2, x3, y3):
        return (x1 - x3) * (y2 - y3) - (x2 - x3) * (y1 - y3)

    d1 = sign(px, py, ax, ay, bx, by)
    d2 = sign(px, py, bx, by, cx, cy)
    d3 = sign(px, py, cx, cy, ax, ay)
    has_neg = d1 < 0 or d2 < 0 or d3 < 0
    has_pos = d1 > 0 or d2 > 0 or d3 > 0
    return not (has_neg and has_pos)


def inside_pin(px, py, scale=1.0, ox=0.0, oy=0.0):
    """Vector hit-test for the marker body in logical 1x coordinates."""
    cx = BASE_W / 2 + ox
    cy = 15.0 + oy
    r = 11.2 * scale
    tip_y = 37.0 * scale + (1 - scale) * cy + oy
    neck_y = cy + 7.4 * scale
    half_neck = 7.0 * scale

    in_head = (px - cx) ** 2 + (py - cy) ** 2 <= r ** 2
    in_tail = point_in_triangle(
        px,
        py,
        cx - half_neck,
        neck_y,
        cx + half_neck,
        neck_y,
        cx,
        tip_y,
    )
    # Cut a tiny amount from the lower sides to keep the pin neck clean.
    return in_head or in_tail


def draw_marker(width, height, color):
    ss = SUPERSAMPLE
    hi_w = width * ss
    hi_h = height * ss
    sx = width / BASE_W
    sy = height / BASE_H
    fill = hex_rgb(color)

    hi = [(0, 0, 0, 0)] * (hi_w * hi_h)

    def paint_if(predicate, rgba):
        for y in range(hi_h):
            py = (y + 0.5) / ss / sy
            for x in range(hi_w):
                px = (x + 0.5) / ss / sx
                if predicate(px, py):
                    idx = y * hi_w + x
                    hi[idx] = over(hi[idx], rgba)

    # 柔和投影：增强卫星图/深色底图上的可见性。
    paint_if(lambda x, y: inside_pin(x, y, 1.03, 1.0, 1.8), (0, 0, 0, 42))

    # 外描边、内色块。
    paint_if(lambda x, y: inside_pin(x, y, 1.16), (255, 255, 255, 255))
    paint_if(lambda x, y: inside_pin(x, y, 1.00), (*fill, 255))

    # 顶部高光，避免纯色块在地图上显得扁平。
    paint_if(
        lambda x, y: ((x - 11.8) ** 2 / 7.0 ** 2 + (y - 10.2) ** 2 / 4.6 ** 2) <= 1,
        (255, 255, 255, 34),
    )

    # 中心定位孔：让 marker 在密集点位中更有辨识度。
    paint_if(lambda x, y: (x - 16.0) ** 2 + (y - 15.0) ** 2 <= 4.4 ** 2, (255, 255, 255, 235))
    paint_if(lambda x, y: (x - 16.0) ** 2 + (y - 15.0) ** 2 <= 2.0 ** 2, (*fill, 255))

    # Downsample supersampled buffer into final RGBA pixels.
    pixels = []
    for y in range(height):
        for x in range(width):
            rs = gs = bs = alphas = 0
            for yy in range(ss):
                for xx in range(ss):
                    r, g, b, a = hi[(y * ss + yy) * hi_w + (x * ss + xx)]
                    rs += r * a
                    gs += g * a
                    bs += b * a
                    alphas += a
            samples = ss * ss
            a = int(round(alphas / samples))
            if alphas:
                r = int(round(rs / alphas))
                g = int(round(gs / alphas))
                b = int(round(bs / alphas))
            else:
                r = g = b = 0
            pixels.append((r, g, b, a))
    return create_png(width, height, pixels)


def main():
    out_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        'miniprogram',
        'images',
    )
    os.makedirs(out_dir, exist_ok=True)

    for label, color in BANDS:
        for scale, suffix in [(1, ''), (2, '@2x')]:
            width = BASE_W * scale
            height = BASE_H * scale
            filename = f'marker_{label}{suffix}.png'
            png = draw_marker(width, height, color)
            with open(os.path.join(out_dir, filename), 'wb') as f:
                f.write(png)
            print(f'✓ {filename} {width}x{height} {len(png)} bytes')


if __name__ == '__main__':
    main()
