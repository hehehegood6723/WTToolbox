#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
WTToolbox brand asset generator (pure Pillow, fully procedural, no downloads).

Outputs
-------
  <ASSETS>/icon.ico        multi-resolution ICO: 16,24,32,48,64,128,256
  <ASSETS>/icon_256.png    256x256 PNG preview of the icon
  <ASSETS>/banner.png      1200x620 RGB hero banner
  <BUILD>/preview_icon16.png   (16px frame, 8x NEAREST)
  <BUILD>/preview_icon32.png   (32px frame, 8x NEAREST)
"""

import math
import os
import sys

from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageOps

# ---------------------------------------------------------------- paths / const

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(ROOT, "src", "wttoolbox", "assets")
BUILD = os.environ.get("WTTOOLBOX_ASSET_PREVIEW_DIR", os.path.join(ROOT, "build", "asset-preview"))
os.makedirs(BUILD, exist_ok=True)
ICO = os.path.join(ASSETS, "icon.ico")
ICON_PNG = os.path.join(ASSETS, "icon_256.png")
BANNER = os.path.join(ASSETS, "banner.png")
PREV16 = os.path.join(BUILD, "preview_icon16.png")
PREV32 = os.path.join(BUILD, "preview_icon32.png")
PREV16_16X = os.path.join(BUILD, "preview_icon16_16x.png")   # 256px, per the design brief

ICON_SIZES = [16, 24, 32, 48, 64, 128, 256]
SS_ICON = 8          # supersample factor for the icon
SS_BANNER = 2        # supersample factor for the banner

ORANGE = (255, 158, 27)
DEEP_ORANGE = (249, 115, 22)
BOLT_TOP = (255, 196, 107)      # #FFC46B
INK = (16, 20, 28)              # #10141C
PANEL = (27, 34, 48)            # #1B2230
LIGHT = (244, 246, 250)         # #F4F6FA
SLATE = (138, 147, 166)         # #8A93A6

BANNER_W, BANNER_H = 1200, 620

FONT_BOLD_CANDIDATES = [
    r"C:\Windows\Fonts\segoeuib.ttf",
    r"C:\Windows\Fonts\seguisb.ttf",
    r"C:\Windows\Fonts\arialbd.ttf",
]
FONT_CJK_CANDIDATES = [
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\segoeuib.ttf",
]

# The mark: ONE polygon, normalised to a 0..1 box (x right, y down).
# Two upper arms sweeping outward like a delta wing, meeting at a centre notch,
# then an asymmetric downward bolt with a kink on the right flank.
# Vertices are deliberately few and chunky so the shape survives 16px
# rasterisation (verified with an 8x NEAREST preview of the saved ICO frame).
MARK_POLY = [
    (0.00, 0.02),   # left wing tip
    (0.50, 0.28),   # centre notch between the wings
    (1.00, 0.02),   # right wing tip
    (0.68, 0.46),   # right waist (concave)
    (0.80, 0.62),   # bolt kink, kicks outward right
    (0.45, 1.00),   # bolt tip
    (0.33, 0.50),   # left waist (concave)
]

log = []


def note(msg):
    print(msg)
    log.append(msg)


# ---------------------------------------------------------------- primitives


def blit(dst, src, xy):
    """alpha_composite `src` onto `dst` at xy, clipping at the canvas edges."""
    x, y = int(round(xy[0])), int(round(xy[1]))
    dw, dh = dst.size
    sx0, sy0 = max(0, -x), max(0, -y)
    dx0, dy0 = max(0, x), max(0, y)
    w = min(src.size[0] - sx0, dw - dx0)
    h = min(src.size[1] - sy0, dh - dy0)
    if w <= 0 or h <= 0:
        return
    dst.alpha_composite(src.crop((sx0, sy0, sx0 + w, sy0 + h)), (dx0, dy0))


def vgrad(w, h, top, bottom):
    """Vertical RGB gradient, `top` at y=0 -> `bottom` at y=h-1."""
    ramp = Image.new("L", (1, h))
    if h == 1:
        ramp.putdata([0])
    else:
        ramp.putdata([int(round(255 * y / (h - 1))) for y in range(h)])
    return ImageOps.colorize(ramp.resize((w, h), Image.NEAREST), black=top, white=bottom)


def diag_gradient(w, h, c_tl, c_br):
    """Linear gradient along the top-left -> bottom-right diagonal."""
    total = w + h
    ramp = Image.new("L", (total, 1))
    ramp.putdata([int(round(255 * i / (total - 1))) for i in range(total)])
    field = Image.new("L", (w, h))
    for y in range(h):
        field.paste(ramp.crop((y, 0, y + w, 1)), (0, y))
    return ImageOps.colorize(field, black=c_tl, white=c_br)


def pick_font(candidates):
    for p in candidates:
        if os.path.isfile(p):
            return p
    return None


def fit_font(path, text, target_ink_h, index=0):
    """Return (font, size) whose ink bbox height for `text` ~= target_ink_h."""
    probe = 120
    try:
        f = ImageFont.truetype(path, probe, index=index)
    except Exception:
        return None, None
    bb = f.getbbox(text)
    h = bb[3] - bb[1]
    if h <= 0:
        return f, probe
    size = max(1, int(round(probe * target_ink_h / float(h))))
    for _ in range(3):
        f = ImageFont.truetype(path, size, index=index)
        bb = f.getbbox(text)
        h = bb[3] - bb[1]
        if h <= 0 or abs(h - target_ink_h) <= 0.5:
            break
        size = max(1, int(round(size * target_ink_h / float(h))))
    return f, size


def mark_points(poly, target_h, center):
    """Scale `poly` so its bbox height == target_h, with bbox centre at `center`."""
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    bh = max(ys) - min(ys)
    s = target_h / bh
    ox = center[0] - (min(xs) + max(xs)) / 2.0 * s
    oy = center[1] - (min(ys) + max(ys)) / 2.0 * s
    return [(ox + px * s, oy + py * s) for px, py in poly]


# ---------------------------------------------------------------- icon


def render_icon(size, ss=SS_ICON, poly=None):
    """Render one icon raster at size*ss then LANCZOS down to `size`."""
    if poly is None:
        poly = MARK_POLY
    S = size * ss
    radius = int(round(0.22 * S))

    # --- tile: gradient + squircle mask
    tile = vgrad(S, S, PANEL, INK).convert("RGBA")
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, S - 1, S - 1], radius=radius, fill=255)
    tile.putalpha(mask)

    # --- inner top highlight: 1px (final) rim, fading out down the tile
    hl = Image.new("L", (S, S), 0)
    off = ss / 2.0
    ImageDraw.Draw(hl).rounded_rectangle(
        [off, off, S - 1 - off, S - 1 - off], radius=radius, outline=255, width=ss
    )
    ramp = Image.new("L", (1, S))
    fade_end = S * 0.46

    def _fade(y):
        t = 1.0 - min(1.0, y / fade_end)
        t = t * t * (3 - 2 * t)  # smoothstep
        return int(round(255 * t))

    ramp.putdata([_fade(y) for y in range(S)])
    a = ImageChops.multiply(hl, ramp.resize((S, S), Image.NEAREST))
    a = a.point(lambda v: v * 28 // 255)
    hi = Image.new("RGBA", (S, S), (255, 255, 255, 0))
    hi.putalpha(a)
    tile = Image.alpha_composite(tile, hi)

    # --- mark inside the 62% centred safe area
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    bw, bh = max(xs) - min(xs), max(ys) - min(ys)
    safe = 0.62 * S
    pts = mark_points(poly, safe * bh / max(bw, bh), (S / 2.0, S / 2.0))

    mmask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mmask).polygon(pts, fill=255)

    px_ys = [p[1] for p in pts]
    y0, y1 = int(math.floor(min(px_ys))), int(math.ceil(max(px_ys)))
    y0 = max(0, y0)
    y1 = min(S, max(y1, y0 + 1))
    mgrad = vgrad(S, S, BOLT_TOP, DEEP_ORANGE).convert("RGBA")
    mgrad.putalpha(mmask)

    icon = Image.alpha_composite(tile, mgrad)
    return icon.resize((size, size), Image.LANCZOS)


# ---------------------------------------------------------------- banner


def radial_glow(canvas_size, center, radius, color, max_alpha):
    w, h = canvas_size
    R = int(math.ceil(radius))
    small = 220
    prof = Image.new("L", (small, small), 0)
    pp = prof.load()
    c = (small - 1) / 2.0
    for y in range(small):
        for x in range(small):
            d = math.hypot(x - c, y - c) / c
            v = max(0.0, 1.0 - d) ** 1.25   # soft falloff, reaches 0 at d=1
            pp[x, y] = int(round(255 * v))
    prof = prof.resize((2 * R, 2 * R), Image.BICUBIC)
    # round (not truncate) so the faint outer tail survives 8-bit quantisation
    prof = prof.point(lambda v: int(round(v * max_alpha / 255.0)))

    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    glow = Image.new("RGBA", (2 * R, 2 * R), color + (0,))
    glow.putalpha(prof)
    blit(layer, glow, (center[0] - R, center[1] - R))
    return layer


def draw_tracked(draw, xy, text, font, fill, tracking):
    """Draw `text` glyph by glyph with `tracking` extra px between advances."""
    x, y = xy
    for ch in text:
        draw.text((x, y), ch, font=font, fill=fill, anchor="ls")
        x += draw.textlength(ch, font=font) + tracking
    return x - tracking


def build_banner():
    S = SS_BANNER
    W, H = BANNER_W * S, BANNER_H * S

    base = diag_gradient(W, H, PANEL, (12, 16, 23)).convert("RGBA")

    # --- technical grid: 1px every 40 (final), brighter every 200
    grid = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grid)
    step = 40 * S
    major = 200 * S
    for x in range(0, W + 1, step):
        a = 18 if x % major == 0 else 10
        gd.line([(x, 0), (x, H)], fill=(255, 255, 255, a), width=S)
    for y in range(0, H + 1, step):
        a = 18 if y % major == 0 else 10
        gd.line([(0, y), (W, y)], fill=(255, 255, 255, a), width=S)
    base = Image.alpha_composite(base, grid)

    # --- concentric contour arcs sweeping in from the right
    arcs = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ad = ImageDraw.Draw(arcs)
    acx, acy = 1290 * S, 300 * S
    specs = [(170, 2), (300, 3), (450, 2), (620, 3), (810, 2)]
    for r, lw in specs:
        rr = r * S
        ad.arc(
            [acx - rr, acy - rr, acx + rr, acy + rr],
            start=100, end=260,
            fill=(255, 158, 27, 26),
            width=lw * S,
        )
    # fade the sweep out just before it reaches the wordmark column
    fade = Image.new("L", (W, 1))
    x0, x1 = 575 * S, 690 * S

    def _hfade(x):
        t = (x - x0) / float(x1 - x0)
        t = max(0.0, min(1.0, t))
        return int(round(255 * t * t * (3 - 2 * t)))

    fade.putdata([_hfade(x) for x in range(W)])
    arcs.putalpha(ImageChops.multiply(arcs.getchannel("A"), fade.resize((W, H), Image.NEAREST)))
    base = Image.alpha_composite(base, arcs)

    # --- soft radial orange glow
    glow = radial_glow((W, H), (0.72 * W, 0.46 * H), 340 * S, ORANGE, 55)
    base = Image.alpha_composite(base, glow)

    # --- watermark mark (~300px tall) at 78% x, 48% y
    wm_mask = Image.new("L", (W, H), 0)
    wm_pts = mark_points(MARK_POLY, 300 * S, (0.78 * W, 0.48 * H))
    ImageDraw.Draw(wm_mask).polygon(wm_pts, fill=255)
    wm = Image.new("RGBA", (W, H), (255, 158, 27, 0))
    wm.putalpha(wm_mask.point(lambda v: v * 40 // 255))
    base = Image.alpha_composite(base, wm)

    # --- wordmark + subtitle, left aligned at 64px, block vertically centred
    bold_path = pick_font(FONT_BOLD_CANDIDATES)
    cjk_path = pick_font(FONT_CJK_CANDIDATES)
    note("wordmark font : %s" % bold_path)
    note("subtitle font : %s" % cjk_path)
    if bold_path is None:
        raise RuntimeError("no usable bold font found")

    word = "WTTOOLBOX"
    sub = "\u6218\u96f7\u5de5\u5177\u7bb1 \u00b7 WAR THUNDER COMPANION"
    tracking = 6 * S
    gap = 14 * S

    f_word, sz_word = fit_font(bold_path, word, 52 * S)
    f_sub, sz_sub = (None, None)
    if cjk_path is not None:
        f_sub, sz_sub = fit_font(cjk_path, "WAR THUNDER COMPANION", 19 * S)
    note("wordmark size : %s px (ink height target %.0f)" % (sz_word, 52 * S))
    note("subtitle size : %s px (latin ink target %.0f)" % (sz_sub, 19 * S))

    d = ImageDraw.Draw(base)
    bb_w = f_word.getbbox(word, anchor="ls")
    ink_w = bb_w[3] - bb_w[1]
    if f_sub is not None:
        bb_s = f_sub.getbbox(sub, anchor="ls")
        ink_s = bb_s[3] - bb_s[1]
    else:
        bb_s = (0, 0, 0, 0)
        ink_s = 0

    block_h = ink_w + gap + ink_s
    top = (H - block_h) / 2.0
    base_word = top - bb_w[1]
    base_sub = top + ink_w + gap - bb_s[1]

    left = 64 * S
    end_x = draw_tracked(d, (left, base_word), word, f_word, LIGHT, tracking)
    note("wordmark width: %.1f px (final), right edge %.1f px" % ((end_x - left) / S, end_x / S))
    if f_sub is not None:
        d.text((left, base_sub), sub, font=f_sub, fill=SLATE, anchor="ls")
        note("subtitle width: %.1f px (final)" % (f_sub.getlength(sub) / S))

    # --- bottom 3px accent bar, orange -> transparent, left to right
    bar_h = 3 * S
    bar_a = Image.new("L", (W, 1))
    bar_a.putdata([max(0, min(255, int(round(255 * (1 - x / float(W - 1)))))) for x in range(W)])
    bar_a = bar_a.resize((W, bar_h), Image.NEAREST)
    bar = Image.new("RGBA", (W, bar_h), ORANGE + (0,))
    bar.putalpha(bar_a)
    blit(base, bar, (0, H - bar_h))

    return base.resize((BANNER_W, BANNER_H), Image.LANCZOS).convert("RGB")


# ---------------------------------------------------------------- main


def main():
    os.makedirs(ASSETS, exist_ok=True)
    os.makedirs(BUILD, exist_ok=True)
    note("assets dir     : %s" % ASSETS)

    # ---- icon rasters
    frames = {}
    for s in ICON_SIZES:
        frames[s] = render_icon(s)
        note("rendered icon frame %-3d  ss=%d  -> %s" % (s, SS_ICON, frames[s].size))

    frames[256].save(ICON_PNG, format="PNG")
    note("wrote %s" % ICON_PNG)

    base = frames[256]
    base.save(
        ICO,
        format="ICO",
        sizes=[(s, s) for s in ICON_SIZES],
        append_images=[frames[s] for s in ICON_SIZES if s != 256],
        bitmap_format="png",
    )
    note("wrote %s" % ICO)

    # ---- banner
    banner = build_banner()
    banner.save(BANNER, format="PNG")
    note("wrote %s" % BANNER)

    # ---- previews from the *saved* ico frames
    with Image.open(ICO) as ico:
        for s, out, k in ((16, PREV16, 8), (32, PREV32, 8), (16, PREV16_16X, 16)):
            ico.size = (s, s)
            ico.load()
            fr = ico.convert("RGBA")
            fr.resize((s * k, s * k), Image.NEAREST).save(out, format="PNG")
            note("wrote preview %s (%dx%d from %dx%d, NEAREST x%d)"
                 % (out, s * k, s * k, s, s, k))


if __name__ == "__main__":
    main()
