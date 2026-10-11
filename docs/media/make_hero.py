"""Generates the README hero: the island swooshing in above a spinning disc.

    python3 docs/media/make_hero.py      writes hero-light.svg and hero-dark.svg

Plain SVG with SMIL animation, so it plays in GitHub's image viewer without
any scripts. Re-run after changing colours or copy.
"""

from pathlib import Path

HERE = Path(__file__).parent

THEMES = {
    "light": {"bg": "#f5f5f7", "glow": "#ff8a3d", "hub": "#ffffff"},
    "dark": {"bg": "#000000", "glow": "#ff6a1f", "hub": "#1c1c1e"},
}

W, H = 1200, 560
CX, CY, R = 600, 330, 190          # big disc
HOLE = R * 0.15


def svg(theme: dict) -> str:
    bg, glow = theme["bg"], theme["glow"]
    # Everything runs on one 8 s loop: island in at 0 s, countdown from 0.8 s
    # to 5.8 s, island out at the end. Each line of the countdown is its own
    # <text>, shown for one second (hidden text would still take up space).
    loop = 'dur="8s" begin="0.4s" repeatCount="indefinite"'
    font = "-apple-system, 'SF Pro Text', Inter, 'Segoe UI', Cantarell, sans-serif"
    countdown = ""
    for n in range(5, 0, -1):
        start = 0 if n == 5 else (0.8 + (5 - n)) / 8   # "5" shows as soon as the island arrives
        end = start + 1 / 8
        countdown += (
            f'<text x="72" y="48" font-family="{font}" font-size="12.5" fill="#fff" fill-opacity="0.62" '
            f'visibility="{"visible" if n == 5 else "hidden"}">Inserted · starting in {n}'
            f'<animate attributeName="visibility" values="hidden;visible;hidden" '
            f'keyTimes="0;{start:.4f};{end:.4f}" calcMode="discrete" {loop}/></text>')
    ring_start, ring_end = 0.8 / 8, 5.8 / 8
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img"
     aria-label="A disc spins while a notification slides in: Night Drive, inserted, starting in 5">
  <defs>
    <radialGradient id="glow" cx="50%" cy="62%" r="55%">
      <stop offset="0" stop-color="{glow}" stop-opacity="0.28"/>
      <stop offset="1" stop-color="{glow}" stop-opacity="0"/>
    </radialGradient>
    <linearGradient id="cover" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#1b1640"/>
      <stop offset="0.48" stop-color="#7a2a6b"/>
      <stop offset="0.72" stop-color="#ff6a3d"/>
      <stop offset="1" stop-color="#ffc46b"/>
    </linearGradient>
    <linearGradient id="sun" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#ffe29a"/>
      <stop offset="1" stop-color="#ff7a59"/>
    </linearGradient>
    <mask id="ring">
      <circle cx="0" cy="0" r="{R}" fill="#fff"/>
      <circle cx="0" cy="0" r="{HOLE}" fill="#000"/>
    </mask>
    <mask id="miniRing">
      <circle cx="0" cy="0" r="20" fill="#fff"/>
      <circle cx="0" cy="0" r="3.6" fill="#000"/>
    </mask>
    <filter id="shadow" x="-20%" y="-40%" width="140%" height="200%">
      <feDropShadow dx="0" dy="14" stdDeviation="16" flood-color="#000" flood-opacity="0.28"/>
    </filter>
    <g id="art">
      <rect x="{-R}" y="{-R}" width="{2 * R}" height="{2 * R}" fill="url(#cover)"/>
      <circle cx="0" cy="{R * 0.1:.1f}" r="{R * 0.42:.1f}" fill="url(#sun)"/>
      <g fill="#1b1640" opacity="0.85">
        <rect x="{-R}" y="{R * 0.22:.1f}" width="{2 * R}" height="{R}"/>
      </g>
      <g stroke="#ff6a3d" stroke-width="3" opacity="0.7">
        <line x1="{-R}" y1="{R * 0.32:.1f}" x2="{R}" y2="{R * 0.32:.1f}"/>
        <line x1="{-R}" y1="{R * 0.46:.1f}" x2="{R}" y2="{R * 0.46:.1f}"/>
        <line x1="{-R}" y1="{R * 0.64:.1f}" x2="{R}" y2="{R * 0.64:.1f}"/>
        <line x1="0" y1="{R * 0.22:.1f}" x2="{-R * 1.6:.1f}" y2="{R}"/>
        <line x1="0" y1="{R * 0.22:.1f}" x2="{R * 1.6:.1f}" y2="{R}"/>
        <line x1="0" y1="{R * 0.22:.1f}" x2="{-R * 0.55:.1f}" y2="{R}"/>
        <line x1="0" y1="{R * 0.22:.1f}" x2="{R * 0.55:.1f}" y2="{R}"/>
      </g>
    </g>
  </defs>

  <rect width="{W}" height="{H}" fill="{bg}"/>
  <ellipse cx="{CX}" cy="{CY + 30}" rx="460" ry="300" fill="url(#glow)"/>

  <!-- the disc -->
  <g transform="translate({CX} {CY})">
    <g filter="url(#shadow)">
      <g mask="url(#ring)">
        <g>
          <use href="#art"/>
          <!-- rainbow sheen sweeping round, the tell of a spinning disc -->
          <g opacity="0.32" style="mix-blend-mode:screen">
            <path d="M0 0 L{R} -40 A{R} {R} 0 0 1 {R} 40 Z" fill="#9be7ff"/>
            <path d="M0 0 L-{R} 40 A{R} {R} 0 0 1 -{R} -40 Z" fill="#ffb3f0"/>
          </g>
          <circle r="{R * 0.33:.1f}" fill="#fff" opacity="0.26"/>
          <circle r="{R * 0.24:.1f}" fill="none" stroke="#000" stroke-opacity="0.18" stroke-width="1.5"/>
          <animateTransform attributeName="transform" type="rotate" from="0" to="360" dur="7s" repeatCount="indefinite"/>
        </g>
      </g>
      <circle r="{R - 0.5}" fill="none" stroke="#fff" stroke-opacity="0.35" stroke-width="1"/>
    </g>
  </g>

  <!-- the island, swooshing down from the top -->
  <g transform="translate(0 -150)">
    <animateTransform attributeName="transform" type="translate"
      values="0 -150; 0 14; 0 0; 0 0; 0 -150" keyTimes="0; 0.06; 0.09; 0.93; 1"
      calcMode="spline" keySplines="0.2 0.9 0.3 1; 0.4 0 0.6 1; 0 0 1 1; 0.6 0 0.9 0.4"
      {loop}/>
    <g transform="translate(395 34)" filter="url(#shadow)">
      <rect width="410" height="66" rx="33" fill="#0c0c10" fill-opacity="0.96"/>
      <rect width="410" height="66" rx="33" fill="none" stroke="#fff" stroke-opacity="0.08"/>
      <g transform="translate(37 33)">
        <g mask="url(#miniRing)">
          <g transform="scale({20 / R:.4f})"><use href="#art"/></g>
          <animateTransform attributeName="transform" type="rotate" from="0" to="360" dur="2.4s" repeatCount="indefinite"/>
        </g>
        <circle r="23" fill="none" stroke="#fff" stroke-opacity="0.14" stroke-width="2.4"/>
        <circle r="23" fill="none" stroke="#fff" stroke-width="2.4" stroke-linecap="round"
                transform="rotate(-90)" pathLength="100" stroke-dasharray="100" stroke-dashoffset="0">
          <animate attributeName="stroke-dashoffset" values="0;0;100;100" keyTimes="0;{ring_start:.4f};{ring_end:.4f};1" {loop}/>
        </circle>
      </g>
      <text x="72" y="29" font-family="{font}" font-size="16" font-weight="700" fill="#fff">Night Drive</text>
      {countdown}
      <g font-family="-apple-system, 'SF Pro Text', Inter, 'Segoe UI', Cantarell, sans-serif" font-size="13" font-weight="600">
        <rect x="242" y="17" width="74" height="32" rx="16" fill="#fff" fill-opacity="0.13"/>
        <text x="279" y="38" text-anchor="middle" fill="#fff">Cancel</text>
        <rect x="324" y="17" width="72" height="32" rx="16" fill="#fff" fill-opacity="0.94"/>
        <text x="360" y="38" text-anchor="middle" fill="#0c0c10">Start</text>
      </g>
    </g>
  </g>
</svg>
'''


for name, theme in THEMES.items():
    (HERE / f"hero-{name}.svg").write_text(svg(theme), encoding="utf-8")
    print("wrote", HERE / f"hero-{name}.svg")
