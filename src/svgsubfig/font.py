import logging
import os
import sys
import warnings
from functools import cache
from pathlib import Path
from typing import Final

from fontTools.pens.boundsPen import BoundsPen
from fontTools.ttLib import TTCollection, TTFont

# =============================================================================

# installed fonts that stand in for the generic CSS font families, by preference
GENERIC_FAMILIES: Final[dict[str, tuple[str, ...]]] = {
    "sans-serif": ("Arial", "Helvetica", "Liberation Sans", "DejaVu Sans"),
    "serif": ("Times New Roman", "Times", "Liberation Serif", "DejaVu Serif"),
    "monospace": ("Courier New", "Courier", "Liberation Mono", "DejaVu Sans Mono"),
}

# extent of a label above and below its baseline [em] if its font is not installed,
# covering the parentheses and descenders of common typefaces
FALLBACK_EXTENT: Final[tuple[float, float]] = (0.77, 0.22)


def font_dirs() -> list[Path]:
    """Directories the fonts of the operating system and its users reside in."""

    if sys.platform == "win32":
        return [
            Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts",
            Path.home() / "AppData/Local/Microsoft/Windows/Fonts",
        ]

    if sys.platform == "darwin":
        return [
            Path("/System/Library/Fonts"),
            Path("/Library/Fonts"),
            Path.home() / "Library/Fonts",
        ]

    return [
        Path("/usr/share/fonts"),
        Path("/usr/local/share/fonts"),
        Path.home() / ".local/share/fonts",
        Path.home() / ".fonts",
    ]


@cache
def font_index() -> dict[str, list[tuple[tuple[bool, int, int], Path, int]]]:
    """Maps the lower-cased family names of all installed fonts to their faces.

    Each face is given by its file and its number within a font collection,
    preceded by a key that sorts the upright face of normal width and weight first.
    """

    index: dict[str, list[tuple[tuple[bool, int, int], Path, int]]] = {}

    # malformed name tables of some system fonts are logged, but harmless
    logger = logging.getLogger("fontTools")
    level = logger.level
    logger.setLevel(logging.CRITICAL)

    try:
        for pth in (p for d in font_dirs() if d.is_dir() for p in d.rglob("*")):

            suffix = pth.suffix.lower()

            if suffix not in (".ttf", ".otf", ".ttc", ".otc"):
                continue

            try:
                if suffix in (".ttc", ".otc"):
                    with TTCollection(pth, lazy=True) as collection:
                        faces = [_face(f) for f in collection.fonts]
                else:
                    with TTFont(pth, lazy=True) as f:
                        faces = [_face(f)]
            except Exception:
                # skip files that are no fonts after all
                continue

            for no, (families, key) in enumerate(faces):
                for family in families:
                    index.setdefault(family, []).append((key, pth, no))
    finally:
        logger.setLevel(level)

    return index


def _face(font: TTFont) -> tuple[set[str], tuple[bool, int, int]]:
    """Family names of a face and its key to prefer the regular face of a family."""

    name = font["name"]
    families = {name.getDebugName(i) for i in (1, 16, 21)}

    if "OS/2" in font:
        os2 = font["OS/2"]
        key = (
            bool(os2.fsSelection & 1),
            abs(os2.usWidthClass - 5),
            abs(os2.usWeightClass - 400),
        )
    else:
        key = (bool(font["head"].macStyle & 2), 0, 0)

    return {f.lower() for f in families if f}, key


@cache
def find_font(family: str) -> tuple[Path, int] | None:
    """Locates the regular face of the first installed family of a CSS font-family.

    Returns the font file and the number of the face within it, or ``None`` if
    none of the families is installed.
    """

    index = font_index()

    for name in family.split(","):
        name = name.strip().strip("'\"")

        for candidate in GENERIC_FAMILIES.get(name.lower(), (name,)):
            faces = index.get(candidate.lower())

            if faces:
                _, pth, no = min(faces)
                return pth, no

    return None


@cache
def load_font(pth: Path, no: int) -> TTFont:
    """Opens a face of a font file, keeping it open for later measurements."""
    return TTFont(pth, fontNumber=no)


def text_extent(text: str, family: str) -> tuple[float, float]:
    """Extent of the ink of a text above and below its baseline in [em].

    Takes the glyph outlines of the font a renderer resolves for the CSS
    font-family, so that the label spacing does not depend on the font's nominal
    ascent and descent, which leave a varying amount of room around the glyphs.
    """

    face = find_font(family)

    if face is None:
        warnings.warn(
            f"None of the fonts '{family}' is installed, labels are placed based "
            "on approximate font metrics."
        )
        return FALLBACK_EXTENT

    font = load_font(*face)
    cmap = font.getBestCmap()
    glyphs = font.getGlyphSet()
    upm = font["head"].unitsPerEm

    y_min = y_max = 0.0

    for char in text:
        pen = BoundsPen(glyphs)
        glyphs[cmap.get(ord(char), ".notdef")].draw(pen)

        # blanks have no ink
        if pen.bounds is not None:
            y_min = min(y_min, pen.bounds[1])
            y_max = max(y_max, pen.bounds[3])

    return y_max / upm, -y_min / upm
