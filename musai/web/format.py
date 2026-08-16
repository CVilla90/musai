"""Presentation helpers for the cockpit (grade colors, etc.).

Grade color scale (UACH passing mark = 7.0):
  • below 7.0  → red → orange  (failing)
  • 7.0        → yellow        (just passing)  — a deliberate jump marks the pass line
  • 7.0 → 10   → yellow → green (gradually greener toward a perfect 10)

Soft/creamy palette: muted saturation, light tints — never neon.
"""

from __future__ import annotations

import colorsys

PASS_MARK = 7.0


def _hue(g: float) -> float:
    g = max(0.0, min(10.0, g))
    if g < PASS_MARK:
        return (g / PASS_MARK) * 38.0          # 0 (red) → 38 (orange)
    return 52.0 + ((g - PASS_MARK) / 3.0) * 76.0  # 52 (yellow) → 128 (green)


def grade_colors(g: float | None) -> dict:
    """Return soft CSS colors {text, bg, border} for a 0–10 grade (gray if None)."""
    if g is None:
        return {"text": "#9a948a", "bg": "#f3efe7", "border": "#e6e0d4"}
    h = _hue(g)
    return {
        "text": f"hsl({h:.0f} 58% 38%)",
        "bg": f"hsl({h:.0f} 68% 93%)",
        "border": f"hsl({h:.0f} 48% 82%)",
    }


def grade_pill_style(g: float | None) -> str:
    """Inline CSS for a grade 'pill' (background + text + border) — used in templates."""
    c = grade_colors(g)
    return f"color:{c['text']};background:{c['bg']};border:1px solid {c['border']};"


def _hsl_hex(h: float, s: float, l: float) -> str:
    """HSL (h in degrees, s/l in 0–1) → 'RRGGBB' hex (for openpyxl)."""
    r, g, b = colorsys.hls_to_rgb(h / 360.0, l, s)
    return f"{int(r * 255):02X}{int(g * 255):02X}{int(b * 255):02X}"


def grade_hex(g: float | None) -> dict:
    """Hex {fill, font} for a 0–10 grade — for the XLSX evidence export."""
    if g is None:
        return {"fill": "F3EFE7", "font": "9A948A"}
    h = _hue(g)
    return {"fill": _hsl_hex(h, 0.55, 0.90), "font": _hsl_hex(h, 0.55, 0.30)}
