#!/usr/bin/env python3
"""Render a terminal preview + pair-distance audit of the rose-pine dawn palette.

Reads lua/rose-pine/palette.lua, extracts the `dawn` block, and prints an
ANSI-coloured summary to stdout. Pipe through `less -R` or redirect to a file
and `cat` it. Requires a truecolor (24-bit) terminal.
"""
import argparse, colorsys, math, re, sys
from pathlib import Path

PALETTE_FILE = Path(__file__).parent / "lua" / "rose-pine" / "palette.lua"
BG_KEY = "base"


def hex_rgb(hex):
    h = hex.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def lin(c):
    c = c / 255
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def lum(hex):
    r, g, b = hex_rgb(hex)
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def ratio(a, b):
    la, lb = lum(a), lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def hs(hex):
    r, g, b = [c / 255 for c in hex_rgb(hex)]
    H, S, _ = colorsys.rgb_to_hsv(r, g, b)
    return H * 360, S


def to_lab(hex):
    r, g, b = [lin(c) for c in hex_rgb(hex)]
    X = (0.4124564 * r + 0.3575761 * g + 0.1804375 * b) / 0.95047
    Y = (0.2126729 * r + 0.7151522 * g + 0.0721750 * b) / 1.00000
    Z = (0.0193339 * r + 0.1191920 * g + 0.9503041 * b) / 1.08883
    def f(t):
        return t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116
    fx, fy, fz = f(X), f(Y), f(Z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def deltaE(c1, c2):
    L1, a1, b1 = to_lab(c1)
    L2, a2, b2 = to_lab(c2)
    return math.sqrt((L1 - L2) ** 2 + (a1 - a2) ** 2 + (b1 - b2) ** 2)


def sw(hex, w=4):
    r, g, b = hex_rgb(hex)
    return f"\x1b[48;2;{r};{g};{b}m{' ' * w}\x1b[0m"


def on(fg, bg, text):
    r, g, b = hex_rgb(fg)
    R, G, B = hex_rgb(bg)
    return f"\x1b[48;2;{R};{G};{B}m\x1b[38;2;{r};{g};{b}m{text}\x1b[0m"


def parse_palette(path, variant):
    src = path.read_text()
    block_re = re.compile(rf"{variant}\s*=\s*\{{(.*?)\n\t*\}}", re.S)
    m = block_re.search(src)
    if not m:
        sys.exit(f"could not find `{variant} = {{ ... }}` in {path}")
    block = m.group(1)
    entry_re = re.compile(r"(\w+)\s*=\s*['\"](#[0-9a-fA-F]{6})['\"]")
    return dict(entry_re.findall(block))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("variant", nargs="?", default="dawn",
                    help="palette variant to render (default: dawn). e.g. main, moon, dawn")
    args = ap.parse_args()

    palette = parse_palette(PALETTE_FILE, args.variant)
    if BG_KEY not in palette:
        sys.exit(f"missing `{BG_KEY}` in {args.variant}")
    bg = palette[BG_KEY]

    # Foreground colors only: skip backgrounds/highlights/none.
    skip = {"_nc", "float", "base", "surface", "overlay",
            "highlight_low", "highlight_med", "highlight_high", "none"}
    colors = {k: v for k, v in palette.items() if k not in skip}

    def sort_key(item):
        _, hexv = item
        h, s = hs(hexv)
        return (1, lum(hexv), 0) if s < 0.20 else (0, h, 0)

    ordered = sorted(colors.items(), key=sort_key)

    print()
    print(f"  Rose-pine {args.variant} — palette summary (sorted by hue)")
    print("  " + "─" * 68)
    print()
    print(f"  {sw(bg, 68)}")
    for n, c in ordered:
        h, s = hs(c)
        row = f"  {n:8} {c}  hue {h:>3.0f}° sat {s:.2f}  The quick brown fox  ratio {ratio(bg, c):.2f}  "
        print("  " + on(c, bg, row))
    print(f"  {sw(bg, 68)}")
    print()
    print("  All foreground pairs (sorted by perceptual closeness — CIE76 ΔE)")
    print("  lower ΔE = more visually similar.  ΔE<10 ~ may confuse, <5 ~ very close")
    print("  " + "─" * 68)
    print()

    pairs = []
    names = list(colors.keys())
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            de = deltaE(colors[a], colors[b])
            lr = ratio(colors[a], colors[b])
            ha, _ = hs(colors[a])
            hb, _ = hs(colors[b])
            hue_d = min(abs(ha - hb), 360 - abs(ha - hb))
            pairs.append((de, lr, hue_d, a, b))
    pairs.sort()

    for de, lr, hue_d, a, b in pairs:
        marker = "VERY CLOSE" if de < 10 else ("close     " if de < 20 else "          ")
        swatches = sw(colors[a], 6) + sw(colors[b], 6)
        left = on(colors[a], bg, f" {colors[a]} {a:>7} ")
        right = on(colors[b], bg, f" {b:<7} {colors[b]} ")
        print(f"  {left}{swatches}{right}   ΔE {de:5.1f}  lum {lr:.2f}  Δhue {hue_d:>3.0f}°  {marker}")
    print()


if __name__ == "__main__":
    main()
