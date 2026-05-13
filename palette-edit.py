#!/usr/bin/env python3
"""Interactive palette editor for the rose-pine fork.

Lists every color in a variant of `lua/rose-pine/palette.lua`. For a chosen
color you can describe what you want ("more orange", "less saturated") and the
script asks the local `claude` CLI for a handful of hex alternatives, previews
them as truecolor swatches, and writes the chosen value back to the palette.

When working on dawn, the change also propagates to the matching ANSI slot in
the kitty light theme (`~/.../kitty/light-theme.auto.conf`).
"""
from __future__ import annotations
import argparse, colorsys, json, os, random, re, subprocess, sys, tempfile, termios, tty
from pathlib import Path

ROOT = Path(__file__).parent
PALETTE_FILE = ROOT / "lua" / "rose-pine" / "palette.lua"
PREVIEW_LUA = ROOT / "palette-edit-preview.lua"
KITTY_FILE = Path.home() / "dotfiles" / "kitty" / ".config" / "kitty" / "light-theme.auto.conf"

# A ~15 line Python snippet that exercises most syntax categories.
PREVIEW_SNIPPET = '''"""Cached fetcher with retry."""

from typing import Optional


class Fetcher:
    BASE_URL = "https://api.example.com"
    cache: dict[str, list] = {}

    def get(self, path: str) -> Optional[dict]:
        if path in self.cache:
            return self.cache[path]
        try:
            return fetch(f"{self.BASE_URL}/{path}").json()
        except TimeoutError:
            return None
'''

# Roles that act as backgrounds — the picker treats these specially: the
# preview panel's bg is swapped to the candidate rather than substituting a
# foreground colour, and the candidate rows use `text` as fg so they stay
# readable when the candidate itself is light.
BG_ROLES = {"base", "surface", "overlay", "_nc", "float",
            "highlight_low", "highlight_med", "highlight_high"}


# Dawn role -> kitty hex lines that should track that role
KITTY_MAP_DAWN = {
    "text":    ["foreground", "selection_foreground", "active_tab_foreground", "cursor", "color7", "color15"],
    "base":    ["background", "inactive_tab_background", "cursor_text_color"],
    "overlay": ["active_tab_background", "color0"],
    "muted":   ["inactive_tab_foreground", "color8"],
    "love":    ["color1", "color9"],
    "moss":    ["color2", "color10"],
    "gold":    ["color3", "color11"],
    "pine":    ["color4", "color12"],
    "iris":    ["color5", "color13", "url_color"],
    "foam":    ["color6", "color14"],
}

HEX_RE = re.compile(r"#[0-9a-fA-F]{6}")


# ── color math ─────────────────────────────────────────────────────────
def hex_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))


def _lin(c):
    c = c / 255
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def lum(h):
    r, g, b = hex_rgb(h)
    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def ratio(a, b):
    la, lb = lum(a), lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def hsv(h):
    r, g, b = [c / 255 for c in hex_rgb(h)]
    H, S, V = colorsys.rgb_to_hsv(r, g, b)
    return H * 360, S, V


# ── raw input ──────────────────────────────────────────────────────────
def getch():
    """Read a single keypress, returning CSI sequences like '\\x1b[A' intact.

    Reads raw bytes from fd 0 to bypass Python's TextIOWrapper buffering —
    which otherwise can swallow the follow-up bytes of an escape sequence
    and leave us with a bare '\\x1b' that looks like the user just pressed
    Escape. After ESC we wait up to ~150 ms for a continuation; once we see
    '[' or 'O' we keep reading until a CSI/SS3 final byte arrives (handles
    plain arrows and the Kitty protocol variants like '\\x1b[1;5A').
    """
    import select
    fd = 0  # stdin
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        ch = os.read(fd, 1).decode("utf-8", errors="replace")
        if ch != "\x1b":
            return ch
        if not select.select([fd], [], [], 0.15)[0]:
            return ch  # bare ESC
        # Drain whatever's there in one read; most terminals send the whole
        # sequence at once. Then keep going only if we still need a final byte.
        more = os.read(fd, 32).decode("utf-8", errors="replace")
        buf = ch + more
        if not (buf.startswith("\x1b[") or buf.startswith("\x1bO")):
            return buf
        while True:
            last = buf[-1]
            if "A" <= last <= "Z" or last == "~":
                return buf
            if not select.select([fd], [], [], 0.05)[0]:
                return buf
            buf += os.read(fd, 32).decode("utf-8", errors="replace")
            if len(buf) > 64:
                return buf
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


# ── snippet preview (via headless nvim) ────────────────────────────────
def fetch_spans():
    """Render PREVIEW_SNIPPET through headless nvim → list of lines of spans.

    Each span is `{"text": str, "fg": str|None}`. Whitespace spans have
    `fg = None` so the caller can paint them with the editor background.
    """
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(PREVIEW_SNIPPET)
        path = f.name
    out_path = path + ".json"
    try:
        env = {**os.environ, "PALETTE_PREVIEW_OUT": out_path}
        subprocess.run(
            [
                "nvim", "--headless", path,
                "-c", "set background=light",
                "-c", "set filetype=python",
                "-c", "colorscheme rose-pine",
                "-c", f"luafile {PREVIEW_LUA}",
                "-c", "qa!",
            ],
            env=env, capture_output=True, timeout=30, check=False,
        )
        return json.loads(Path(out_path).read_text())
    finally:
        for p in (path, out_path):
            try: os.unlink(p)
            except FileNotFoundError: pass


def render_snippet(spans, edited_role_color, override_hex, bg):
    """Substitute `override_hex` for any span whose fg matches the edited role."""
    target = edited_role_color.lower() if edited_role_color else None
    lines = []
    for line in spans:
        out = []
        for span in line:
            fg = span.get("fg")
            text = span["text"]
            if fg and target and fg.lower() == target:
                fg = override_hex
            out.append(on(fg or bg, bg, text))
        lines.append("".join(out))
    return lines


# ── ANSI helpers ───────────────────────────────────────────────────────
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
LOW_CONTRAST_THRESHOLD = 1.75


def visible_len(s):
    return len(_ANSI_RE.sub("", s))


def sw(h, w=6):
    r, g, b = hex_rgb(h)
    return f"\x1b[48;2;{r};{g};{b}m{' ' * w}\x1b[0m"


def contrast_fg(c):
    """Pick black or white as the foreground that's most readable on `c`."""
    return "#000000" if lum(c) > 0.5 else "#ffffff"


def label(role_color, bg, text):
    """Render descriptive text in `role_color` on `bg`, unless their contrast
    is too low — in which case flip to plain text on a background of the
    color, so the description remains legible."""
    if ratio(bg, role_color) < LOW_CONTRAST_THRESHOLD:
        return on(contrast_fg(role_color), role_color, text)
    return on(role_color, bg, text)


def on(fg, bg, text):
    r, g, b = hex_rgb(fg)
    R, G, B = hex_rgb(bg)
    return f"\x1b[48;2;{R};{G};{B}m\x1b[38;2;{r};{g};{b}m{text}\x1b[0m"


# ── palette I/O ────────────────────────────────────────────────────────
def parse_variant(text, variant):
    m = re.search(rf"{variant}\s*=\s*\{{(.*?)\n\t*\}}", text, re.S)
    if not m:
        sys.exit(f"could not find `{variant} = {{ … }}` in palette.lua")
    block = m.group(1)
    return [(k, v) for k, v in re.findall(r"(\w+)\s*=\s*['\"](#[0-9a-fA-F]{6})['\"]", block)]


def update_palette(variant, key, new_hex):
    text = PALETTE_FILE.read_text()
    # Restrict the substitution to inside the variant block.
    m = re.search(rf"({variant}\s*=\s*\{{)(.*?)(\n\t*\}})", text, re.S)
    if not m:
        sys.exit(f"variant `{variant}` not found")
    block = m.group(2)
    new_block, n = re.subn(
        rf"(\b{re.escape(key)}\s*=\s*['\"])#[0-9a-fA-F]{{6}}(['\"])",
        rf"\g<1>{new_hex}\g<2>",
        block,
    )
    if n == 0:
        sys.exit(f"could not find `{key}` inside `{variant}`")
    PALETTE_FILE.write_text(text[:m.start(2)] + new_block + text[m.end(2):])


def update_kitty(role, new_hex):
    if not KITTY_FILE.exists():
        return False
    keys = KITTY_MAP_DAWN.get(role)
    if not keys:
        return False
    text = KITTY_FILE.read_text()
    changed = False
    for k in keys:
        new_text, n = re.subn(
            rf"(^{re.escape(k)}\s+)#[0-9a-fA-F]{{6}}",
            rf"\g<1>{new_hex}",
            text,
            flags=re.M,
        )
        if n:
            text = new_text
            changed = True
    if changed:
        KITTY_FILE.write_text(text)
    return changed


# ── alternative generation ─────────────────────────────────────────────
def _hls_to_hex(h, l, s):
    r, g, b = colorsys.hls_to_rgb(h % 1.0, max(0.0, min(1.0, l)), max(0.0, min(1.0, s)))
    return "#{:02x}{:02x}{:02x}".format(round(r * 255), round(g * 255), round(b * 255))


def generate_alternatives(current_hex, h_var=1.0, s_var=1.0, l_var=1.0, n=6):
    """`n` randomized variants of `current_hex`, nudged independently per axis.

    Each multiplier scales the random magnitude on its axis (1.0 = default,
    ±~30° hue, ±0.15 sat, ±0.08 lightness).
    """
    r, g, b = [c / 255 for c in hex_rgb(current_hex)]
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    out = []
    for _ in range(n):
        dh = random.uniform(-0.08, 0.08) * h_var      # ±~30° at h_var=1
        ds = random.uniform(-0.15, 0.15) * s_var
        dl = random.uniform(-0.08, 0.08) * l_var
        out.append(_hls_to_hex(h + dh, l + dl, s + ds))
    return out


# ── UI ─────────────────────────────────────────────────────────────────
def fmt_row(idx, name, hexv, bg):
    h, s, _ = hsv(hexv)
    body = f"  {idx:>2}. {name:8} {hexv}  hue {h:>3.0f}° sat {s:.2f}  ratio {ratio(bg, hexv):.2f}"
    pad = " " * max(0, 60 - len(body))
    return sw(hexv, 4) + label(hexv, bg, body + pad)


def list_colors(entries, bg):
    print()
    for i, (k, v) in enumerate(entries, 1):
        print(fmt_row(i, k, v, bg))
    print()


def fmt_candidate(idx, hexv, bg, original, selected, text_fg, is_bg):
    delta = ratio(bg, hexv)
    h, s, _ = hsv(hexv)
    cursor = "▸ " if selected else "  "
    body = f"{cursor}{idx}. {hexv}  hue {h:>3.0f}° sat {s:.2f}  bg-ratio {delta:.2f}  "
    if is_bg:
        # bg-class roles: use palette text colour, since the candidate IS a bg.
        rendered = on(text_fg, bg, " " + body)
    else:
        rendered = label(hexv, bg, " " + body)
    return sw(original, 6) + sw(hexv, 6) + rendered


def edit_loop(variant):
    while True:
        text = PALETTE_FILE.read_text()
        entries = parse_variant(text, variant)
        palette = dict(entries)
        bg = palette.get("base", "#ffffff")

        list_colors(entries, bg)
        sel = input(f"Pick a color number (q to quit) for `{variant}`: ").strip()
        if sel in ("q", "quit", ""):
            return
        try:
            idx = int(sel)
            role, current = entries[idx - 1]
        except (ValueError, IndexError):
            print("invalid selection")
            continue

        h_var = s_var = l_var = 1.0
        # Snapshots of the HLS axis values when the axis was locked, or None.
        # Stored in floating point so they don't drift due to RGB rounding.
        locked_h = locked_s = locked_l = None

        def _hls(hexv):
            r, g, b = [c / 255 for c in hex_rgb(hexv)]
            return colorsys.rgb_to_hls(r, g, b)  # (h, l, s)

        def regen(anchor, h=None, s=None, l=None):
            """Generate alternatives around `anchor`. Any axis given a value
            (h/s/l) is pinned: every candidate uses that exact value with
            zero variance on that axis. Pass the lock snapshots explicitly
            from the caller."""
            ah, al, as_ = _hls(anchor)
            base = _hls_to_hex(
                h if h is not None else ah,
                l if l is not None else al,
                s if s is not None else as_,
            )
            return generate_alternatives(
                base,
                h_var=0.0 if h is not None else h_var,
                s_var=0.0 if s is not None else s_var,
                l_var=0.0 if l is not None else l_var,
            )

        def fmt_axis(name, var, locked):
            return f"{name}:{'  LOCK ' if locked else f'{var:.2f}x'}"

        def locks():
            return {"h": locked_h, "s": locked_s, "l": locked_l}

        candidates = regen(current, **locks())
        selected = 0
        spans = fetch_spans()
        lines_drawn = 0
        new_hex = None

        while True:
            if lines_drawn:
                sys.stdout.write(f"\x1b[{lines_drawn}A\r\x1b[J")

            preview_color = candidates[selected]
            is_bg_role = role in BG_ROLES
            text_fg = palette.get("text", "#000000")
            # For bg roles, the snippet panel's bg swaps to the candidate.
            before_bg = bg
            after_bg = preview_color if is_bg_role else bg
            # For fg roles, substitute the current colour wherever it appears.
            # For bg roles, no fg substitution — only the bg swap.
            sub_target = None if is_bg_role else current
            sub_override = None if is_bg_role else preview_color
            block = []
            block.append("")
            block.append(
                f"  selected: {sw(current, 8)} {role} = {current}"
                f"    variance  {fmt_axis('h', h_var, locked_h is not None)}"
                f"  {fmt_axis('s', s_var, locked_s is not None)}"
                f"  {fmt_axis('l', l_var, locked_l is not None)}"
            )
            block.append("")
            for i, c in enumerate(candidates, 1):
                block.append(fmt_candidate(i, c, bg, current, i - 1 == selected,
                                           text_fg, is_bg_role))
            block.append("")
            col_w = 52
            before = render_snippet(spans, None, None, before_bg)
            after = render_snippet(spans, sub_target, sub_override, after_bg)
            block.append(f"  before{' ' * (col_w - 6)}     after")
            for bl, al in zip(before, after):
                bl_pad = bl + on(before_bg, before_bg, " " * max(0, col_w - visible_len(bl)))
                al_pad = al + on(after_bg, after_bg, " " * max(0, col_w - visible_len(al)))
                block.append("    " + bl_pad + on("#969491", bg, " │ ") + al_pad)
            block.append("")
            block.append("  [↑/↓] nav  [enter] pick  [r] regen  [H/h S/s L/l] axis  [+/-] all  [x/c/v] lock h/s/l  [b] back")
            sys.stdout.write("\n".join(block) + "\n")
            sys.stdout.flush()
            lines_drawn = len(block)

            key = getch()
            is_csi = key.startswith("\x1b[") or key.startswith("\x1bO")
            if (is_csi and key.endswith("A")) or key == "k":
                selected = (selected - 1) % len(candidates)
            elif (is_csi and key.endswith("B")) or key == "j":
                selected = (selected + 1) % len(candidates)
            elif key == "H":
                h_var *= 1.1; candidates = regen(candidates[selected], **locks())
            elif key == "h":
                h_var /= 1.1; candidates = regen(candidates[selected], **locks())
            elif key == "S":
                s_var *= 1.1; candidates = regen(candidates[selected], **locks())
            elif key == "s":
                s_var /= 1.1; candidates = regen(candidates[selected], **locks())
            elif key == "L":
                l_var *= 1.1; candidates = regen(candidates[selected], **locks())
            elif key == "l":
                l_var /= 1.1; candidates = regen(candidates[selected], **locks())
            elif key == "+":
                h_var *= 1.1; s_var *= 1.1; l_var *= 1.1
                candidates = regen(candidates[selected], **locks())
            elif key == "-":
                h_var /= 1.1; s_var /= 1.1; l_var /= 1.1
                candidates = regen(candidates[selected], **locks())
            elif key == "x":
                if locked_h is None:
                    locked_h, _, _ = _hls(candidates[selected])
                    h_var = 0
                else:
                    locked_h = None
                    h_var = 1.0
                candidates = regen(candidates[selected], **locks())
            elif key == "c":
                if locked_s is None:
                    _, _, locked_s = _hls(candidates[selected])
                    s_var = 0
                else:
                    locked_s = None
                    s_var = 1.0
                candidates = regen(candidates[selected], **locks())
            elif key == "v":
                if locked_l is None:
                    _, locked_l, _ = _hls(candidates[selected])
                    l_var = 0
                else:
                    locked_l = None
                    l_var = 1.0
                candidates = regen(candidates[selected], **locks())
            elif key == "r":
                candidates = regen(current, **locks())
            elif key in ("\n", "\r"):
                new_hex = candidates[selected]
                break
            elif key in ("b", "q", "\x1b", "\x03"):
                break
            elif key.isdigit() and 1 <= int(key) <= len(candidates):
                new_hex = candidates[int(key) - 1]
                break

        if new_hex is not None:
            update_palette(variant, role, new_hex)
            msg = f"  saved {role} = {new_hex} to palette.lua"
            if variant == "dawn" and update_kitty(role, new_hex):
                msg += " (and kitty light theme)"
            print(msg)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("variant", nargs="?", default="dawn",
                    help="palette variant to edit (default: dawn)")
    args = ap.parse_args()
    if not PALETTE_FILE.exists():
        sys.exit(f"palette.lua not found at {PALETTE_FILE}")
    edit_loop(args.variant)


if __name__ == "__main__":
    main()
