"""Brand palette derivation and WCAG contrast checks (pure functions, no deps).

The 50 to 950 ramp is derived in OKLCH: ``brand-600`` is the primary colour
itself, lighter steps move its lightness towards white and darker steps
towards near-black, keeping the hue. Chroma is reduced for the very light
and very dark steps and then clipped into the sRGB gamut, so every step is
a valid ``#rrggbb``.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

HEX_PATTERN = re.compile(r"^#[0-9a-fA-F]{6}$")

RAMP_STEPS: tuple[int, ...] = (50, 100, 200, 300, 400, 500, 600, 700, 800, 900, 950)
PRIMARY_STEP = 600
# Fraction of the way from the primary's lightness towards the light / dark end.
_LIGHT_FRACTION = {50: 0.95, 100: 0.87, 200: 0.72, 300: 0.55, 400: 0.38, 500: 0.19}
_DARK_FRACTION = {700: 0.2, 800: 0.42, 900: 0.62, 950: 0.8}
_LIGHT_END = 0.985
_DARK_END = 0.18
_LIGHT_CHROMA_LOSS = 0.8
_DARK_CHROMA_LOSS = 0.35
_GAMUT_STEPS = 24

WHITE = "#ffffff"
# Matches `dark.bg` in the frontend Tailwind theme.
DARK_BG = "#090d11"
MIN_TEXT_CONTRAST = 4.5

# sRGB transfer function constants (IEC 61966-2-1).
_SRGB_THRESHOLD = 0.04045
_SRGB_LINEAR_THRESHOLD = 0.0031308
_SRGB_SLOPE = 12.92
_SRGB_GAMMA = 2.4
_SRGB_OFFSET = 0.055
# WCAG 2.x relative luminance coefficients and flare term.
_LUMA = (0.2126, 0.7152, 0.0722)
_FLARE = 0.05
_CHANNEL_MAX = 255


class PaletteError(ValueError):
    """A palette that fails the WCAG check; ``str()`` explains why."""


@dataclass(frozen=True, slots=True)
class Oklch:
    lightness: float
    chroma: float
    hue: float


def normalize_hex(value: str) -> str:
    """``#RRGGBB`` (any case) to lower-case ``#rrggbb``; ValueError otherwise."""
    if not HEX_PATTERN.match(value):
        raise ValueError(f"{value!r} is not a #RRGGBB colour")
    return value.lower()


def hex_to_rgb(value: str) -> tuple[float, float, float]:
    """``#rrggbb`` to sRGB channels in 0..1."""
    value = normalize_hex(value)
    return (
        int(value[1:3], 16) / _CHANNEL_MAX,
        int(value[3:5], 16) / _CHANNEL_MAX,
        int(value[5:7], 16) / _CHANNEL_MAX,
    )


def rgb_to_hex(rgb: tuple[float, float, float]) -> str:
    channels = (round(min(max(c, 0.0), 1.0) * _CHANNEL_MAX) for c in rgb)
    return "#" + "".join(f"{c:02x}" for c in channels)


def _to_linear(channel: float) -> float:
    if channel <= _SRGB_THRESHOLD:
        return channel / _SRGB_SLOPE
    return ((channel + _SRGB_OFFSET) / (1 + _SRGB_OFFSET)) ** _SRGB_GAMMA


def _from_linear(channel: float) -> float:
    if channel <= _SRGB_LINEAR_THRESHOLD:
        return channel * _SRGB_SLOPE
    return (1 + _SRGB_OFFSET) * channel ** (1 / _SRGB_GAMMA) - _SRGB_OFFSET


def rgb_to_oklch(rgb: tuple[float, float, float]) -> Oklch:
    r, g, b = (_to_linear(c) for c in rgb)
    lms = (
        0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b,
        0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b,
        0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b,
    )
    l_, m_, s_ = (math.copysign(abs(v) ** (1 / 3), v) for v in lms)
    lightness = 0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_
    a = 1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_
    b2 = 0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_
    return Oklch(lightness, math.hypot(a, b2), math.atan2(b2, a))


def _oklch_to_linear(color: Oklch) -> tuple[float, float, float]:
    a = color.chroma * math.cos(color.hue)
    b = color.chroma * math.sin(color.hue)
    l_ = color.lightness + 0.3963377774 * a + 0.2158037573 * b
    m_ = color.lightness - 0.1055613458 * a - 0.0638541728 * b
    s_ = color.lightness - 0.0894841775 * a - 1.2914855480 * b
    l3, m3, s3 = l_**3, m_**3, s_**3
    return (
        4.0767416621 * l3 - 3.3077115913 * m3 + 0.2309699292 * s3,
        -1.2684380046 * l3 + 2.6097574011 * m3 - 0.3413193965 * s3,
        -0.0041960863 * l3 - 0.7034186147 * m3 + 1.7076147010 * s3,
    )


def _in_gamut(linear: tuple[float, float, float]) -> bool:
    eps = 1e-6
    return all(-eps <= c <= 1 + eps for c in linear)


def oklch_to_hex(color: Oklch) -> str:
    """Convert to sRGB, reducing chroma (binary search) until it is in gamut."""
    linear = _oklch_to_linear(color)
    if not _in_gamut(linear):
        low, high = 0.0, color.chroma
        for _ in range(_GAMUT_STEPS):
            mid = (low + high) / 2
            if _in_gamut(_oklch_to_linear(Oklch(color.lightness, mid, color.hue))):
                low = mid
            else:
                high = mid
        linear = _oklch_to_linear(Oklch(color.lightness, low, color.hue))
    r, g, b = (_from_linear(min(max(c, 0.0), 1.0)) for c in linear)
    return rgb_to_hex((r, g, b))


def derive_ramp(primary_hex: str) -> dict[str, str]:
    """The ``"50"`` to ``"950"`` ramp; ``"600"`` is ``primary_hex`` itself."""
    primary = normalize_hex(primary_hex)
    base = rgb_to_oklch(hex_to_rgb(primary))
    ramp: dict[str, str] = {}
    for step in RAMP_STEPS:
        if step == PRIMARY_STEP:
            ramp[str(step)] = primary
            continue
        if step in _LIGHT_FRACTION:
            fraction = _LIGHT_FRACTION[step]
            lightness = base.lightness + (_LIGHT_END - base.lightness) * fraction
            chroma = base.chroma * (1 - _LIGHT_CHROMA_LOSS * fraction)
        else:
            fraction = _DARK_FRACTION[step]
            lightness = base.lightness - (base.lightness - _DARK_END) * fraction
            chroma = base.chroma * (1 - _DARK_CHROMA_LOSS * fraction)
        ramp[str(step)] = oklch_to_hex(Oklch(lightness, chroma, base.hue))
    return ramp


def relative_luminance(value: str) -> float:
    r, g, b = (_to_linear(c) for c in hex_to_rgb(value))
    return _LUMA[0] * r + _LUMA[1] * g + _LUMA[2] * b


def contrast_ratio(foreground: str, background: str) -> float:
    """WCAG 2.x contrast ratio (1.0 to 21.0)."""
    lighter, darker = sorted(
        (relative_luminance(foreground), relative_luminance(background)),
        reverse=True,
    )
    return (lighter + _FLARE) / (darker + _FLARE)


def check_palette(ramp: dict[str, str]) -> None:
    """Raise PaletteError unless brand text is readable in both themes.

    ``brand-600`` is the light-theme text/link colour (on white) and
    ``brand-400`` the dark-theme one (on ``dark-bg``); both need 4.5:1.
    """
    problems = []
    light = contrast_ratio(ramp["600"], WHITE)
    if light < MIN_TEXT_CONTRAST:
        problems.append(
            f"brand-600 {ramp['600']} on white is {light:.2f}:1 "
            f"(needs {MIN_TEXT_CONTRAST}:1); choose a darker primary colour"
        )
    dark = contrast_ratio(ramp["400"], DARK_BG)
    if dark < MIN_TEXT_CONTRAST:
        problems.append(
            f"brand-400 {ramp['400']} on dark background {DARK_BG} is "
            f"{dark:.2f}:1 (needs {MIN_TEXT_CONTRAST}:1); choose a lighter "
            "or more saturated primary colour"
        )
    if problems:
        raise PaletteError("; ".join(problems))


def palette_for(primary_hex: str) -> dict[str, str]:
    """Derive and validate the ramp in one step (PaletteError on failure)."""
    ramp = derive_ramp(primary_hex)
    check_palette(ramp)
    return ramp
