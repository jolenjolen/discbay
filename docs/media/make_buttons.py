"""Generates the README's buttons as SVG pills, in light and dark versions.

    python3 docs/media/make_buttons.py

GitHub READMEs can't style links, so each button is a small image inside a
link. Edit BUTTONS to add one, then re-run.
"""

from pathlib import Path

HERE = Path(__file__).parent / "buttons"

# name: (label, primary?)
BUTTONS = {
    "get-started": ("Get started", True),
    "read-the-story": ("Read the story", False),
    "open-studio": ("Open DiscBay Studio", True),
    "guide": ("Read the guide", False),
    "frontend": ("Write a frontend", False),
    "issue": ("Report a problem", False),
}

THEMES = {
    "light": {"ink": "#1d1d1f", "on_ink": "#ffffff", "line": "#c7c7cc"},
    "dark": {"ink": "#f5f5f7", "on_ink": "#000000", "line": "#48484a"},
}

FONT = "-apple-system, BlinkMacSystemFont, 'SF Pro Text', Inter, 'Segoe UI', Cantarell, 'Noto Sans', sans-serif"
SIZE, HEIGHT, PAD = 15, 44, 26


def width(label: str) -> int:
    # Rough advance widths for a semibold UI font; generous so no font clips.
    narrow, wide = set("iljtfr .,'"), set("mwMW")
    units = sum(0.32 if c in narrow else 0.9 if c in wide else 0.6 for c in label)
    return int(units * SIZE + PAD * 2)


def svg(label: str, primary: bool, theme: dict) -> str:
    w = width(label)
    if primary:
        shape = f'<rect x="0.5" y="0.5" width="{w - 1}" height="{HEIGHT - 1}" rx="{HEIGHT / 2 - 0.5}" fill="{theme["ink"]}"/>'
        color = theme["on_ink"]
    else:
        shape = (f'<rect x="0.75" y="0.75" width="{w - 1.5}" height="{HEIGHT - 1.5}" rx="{HEIGHT / 2 - 0.75}" '
                 f'fill="none" stroke="{theme["line"]}" stroke-width="1.5"/>')
        color = theme["ink"]
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{HEIGHT}" viewBox="0 0 {w} {HEIGHT}" '
            f'role="img" aria-label="{label}">{shape}'
            f'<text x="{w / 2}" y="{HEIGHT / 2 + SIZE * 0.36:.1f}" text-anchor="middle" font-family="{FONT}" '
            f'font-size="{SIZE}" font-weight="600" fill="{color}">{label}</text></svg>\n')


HERE.mkdir(exist_ok=True)
for name, (label, primary) in BUTTONS.items():
    for theme_name, theme in THEMES.items():
        (HERE / f"{name}-{theme_name}.svg").write_text(svg(label, primary, theme), encoding="utf-8")
print(f"wrote {len(BUTTONS) * len(THEMES)} buttons to {HERE}")
