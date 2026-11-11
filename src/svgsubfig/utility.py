import base64
import json
import re
import subprocess
from collections.abc import Iterable
from pathlib import Path
from typing import Final, NamedTuple, Self, Union

from lxml import etree
from PIL import Image

from .font import text_extent
from .markup import Run, parse_scripts

# =============================================================================

# auxiliary type for numeric values
numeric = Union[float, int]

# constants for unit conversion (mm -> px, pt -> px)
MM2PX: Final[float] = 3.77952755906
PT2PX: Final[float] = 4 / 3

# absolute CSS/SVG length units in user units [px], see
# https://www.w3.org/TR/css-values-3/#absolute-lengths
UNIT2PX: Final[dict[str, float]] = {
    "": 1.0,
    "px": 1.0,
    "pt": 96 / 72,
    "pc": 96 / 6,
    "in": 96.0,
    "mm": 96 / 25.4,
    "cm": 96 / 2.54,
    "q": 96 / 101.6,
}

# a number with an optional unit suffix, e.g. "180.14417mm"
RE_LENGTH: Final[re.Pattern] = re.compile(
    r"\s*([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)\s*([a-zA-Z%]*)\s*$"
)


def length_to_px(value: str | None) -> float | None:
    """Converts an SVG length to user units [px].

    Returns ``None`` if the length is absent or expressed in a unit that only
    resolves against a rendering context (``%``, ``em``, ``ex``, ``ch``, ``rem``,
    ``vw``, ``vh``), in which case the caller has to fall back to the viewBox.
    """

    if value is None:
        return None

    match = RE_LENGTH.match(value)

    if match is None:
        return None

    factor = UNIT2PX.get(match.group(2).lower())

    if factor is None:
        return None

    return float(match.group(1)) * factor


def normalize_svg(root: etree._Element) -> tuple[float, float]:
    """Neutralizes the viewport of an SVG so it can be nested and scaled freely.

    A nested ``<svg>`` is laid out in the viewport its ``width``/``height``
    establish, while a ``viewBox`` rescales the content into that viewport.
    MATLAB up to R2024b wrote plain user units and no viewBox, so the two
    agreed. Since R2025a it writes ``width``/``height`` in [mm] next to a
    viewBox in [pt], which renders the content 4/3 too large.

    Restates ``width``/``height`` in the units of the content and returns that
    size, leaving the group transform around the subfigure as the only scaling
    that is applied to it.
    """

    vb = root.get("viewBox")
    w: float | None
    h: float | None

    if vb is not None:
        w, h = (float(v) for v in vb.replace(",", " ").split()[2:4])
    else:
        w = length_to_px(root.get("width"))
        h = length_to_px(root.get("height"))

    if not w or not h:
        raise ValueError(
            "Cannot determine the size of an SVG subfigure: it needs a viewBox "
            "or absolute width and height attributes."
        )

    root.set("width", str(w))
    root.set("height", str(h))

    return w, h


def as_rows(items: list) -> list[list]:
    """Reads a flat JSON array as a single row and a nested one as rows.

    Items of a nested array that are no arrays themselves form a row of their own,
    e.g. ``["a.png", ["b.svg", "c.svg"]]`` puts ``a.png`` above the other two.
    """

    if not any(isinstance(item, list) for item in items):
        return [items]

    return [item if isinstance(item, list) else [item] for item in items]


class Picture(NamedTuple):
    """An image read into an element that is yet to be placed in the figure."""

    element: etree._Element

    # natural size in the units of the image
    width: float
    height: float

    # physical size of such a unit in [px], to scale images alike
    unit: float


class SVGSubFigure:

    NS_SVG = "http://www.w3.org/2000/svg"
    NS_XLINK = "http://www.w3.org/1999/xlink"

    NSMAP = {
        None: NS_SVG,
        "xlink": NS_XLINK,
    }

    MARGIN_SIDES = ("top", "bottom", "left", "right")

    def __init__(self):
        self._rows = []
        self._description = []
        self._font_size = 12
        self._font_family = "Arial, Helvetica, sans-serif"
        self._gap_between = 20
        self._gap_label = 10
        self._gap_margin = None
        self._gap_row = None
        self._index_offset = 0
        self._labels = True
        self._margins = {}
        self._margin_sizes = {}
        self._width = 180
        self._height = 90

    @classmethod
    def from_json(cls, pth: Path) -> Self:
        """Creates a ``SVGSubFigure`` instance from JSON configuration file."""

        ins = cls()

        with pth.open(encoding="utf-8", mode="r") as f:
            data = json.load(f)
            ins._gap_between = data["gap-between"] * MM2PX
            ins._width = data["width"] * MM2PX

            # the labels' typeface and gap may be left out, e.g. without labels
            if "font-size" in data:
                ins._font_size = data["font-size"] * PT2PX

            if "font-family" in data:
                ins._font_family = data["font-family"]

            if "gap-label" in data:
                ins._gap_label = data["gap-label"] * MM2PX

            if "labels" in data:
                ins._labels = bool(data["labels"])

            if "gap-margin" in data:
                ins._gap_margin = data["gap-margin"] * MM2PX

            if "gap-row" in data:
                ins._gap_row = data["gap-row"] * MM2PX

            if "index-offset" in data:
                ins._index_offset = data["index-offset"]

            if "images" in data:
                for row in as_rows(data["images"]):
                    ins.add_row(pth.parent / pth_img for pth_img in row)

            if "description" in data:
                description = data["description"]

                # nested descriptions follow the rows of the images
                if any(isinstance(d, list) for d in description):
                    description = [
                        d
                        for row, row_description in zip(ins.rows, as_rows(description))
                        for d in (row_description + [""] * len(row))[: len(row)]
                    ]

                ins._description = description

            if "margin" in data:
                for side, margin in data["margin"].items():

                    # an image of a given size in [mm] comes as an object
                    if isinstance(margin, dict):
                        ins.set_margin(
                            side,
                            pth.parent / margin["image"],
                            *(
                                margin[key] * MM2PX if key in margin else None
                                for key in ("width", "height")
                            ),
                        )
                    else:
                        ins.set_margin(side, pth.parent / margin)

        return ins

    @property
    def children(self) -> list[Path]:
        """Collection of ``Path`` instances, each referencing a subfigure, row by row"""
        return [child for row in self._rows for child in row]

    @children.setter
    def children(self, f: list[Path]):
        self._rows = [f]

    @property
    def rows(self) -> list[list[Path]]:
        """Rows of subfigures, from top to bottom"""
        return self._rows

    @rows.setter
    def rows(self, r: list[list[Path]]):
        self._rows = r

    @property
    def description(self) -> list[str]:
        """Descriptions to add after the labels"""
        return self._description

    @description.setter
    def description(self, f: list[str]):
        self._description = f

    @property
    def font_family(self) -> str:
        """Label font family string"""
        return self._font_family

    @font_family.setter
    def font_family(self, f: str):
        self._font_family = f

    @property
    def font_size(self) -> numeric:
        """Label font size in [px]"""
        return self._font_size

    @font_size.setter
    def font_size(self, f: numeric):
        self._font_size = f

    @property
    def gap_between(self) -> numeric:
        """Spacing between subfigures in [px]"""
        return self._gap_between

    @gap_between.setter
    def gap_between(self, g: numeric):
        self._gap_between = g

    @property
    def gap_label(self) -> numeric:
        "Spacing between each subfigure and its label [px]"
        return self._gap_label

    @gap_label.setter
    def gap_label(self, g: numeric):
        self._gap_label = g

    @property
    def gap_margin(self) -> numeric:
        """Spacing between the subfigures and the images in the margins in [px],
        unless set the same as ``gap_between``"""
        return self._gap_between if self._gap_margin is None else self._gap_margin

    @gap_margin.setter
    def gap_margin(self, g: numeric):
        self._gap_margin = g

    @property
    def gap_row(self) -> numeric:
        """Spacing between the labels of a row and the next row in [px], or between
        the rows of subfigures if they are not labeled, unless set the same as
        ``gap_between``"""
        return self._gap_between if self._gap_row is None else self._gap_row

    @gap_row.setter
    def gap_row(self, g: numeric):
        self._gap_row = g

    @property
    def height(self) -> numeric:
        """Height of the figure in [px]"""
        return self._height

    @height.setter
    def height(self, h: numeric):
        self._height = h

    @property
    def index_offset(self) -> int:
        """Index of the first subfigure image"""
        return self._index_offset

    @index_offset.setter
    def index_offset(self, h: int):
        self._index_offset = h

    @property
    def labels(self) -> bool:
        """Whether a label is set below each subfigure, e.g. ``(a)``, or the
        subfigures are arranged in their rows alone"""
        return self._labels

    @labels.setter
    def labels(self, f: bool):
        self._labels = f

    @property
    def margins(self) -> dict[str, Path]:
        """Images in the margins around the subfigures, by side"""
        return self._margins

    @property
    def margin_sizes(self) -> dict[str, tuple[numeric | None, numeric | None]]:
        """Given width and height in [px] of the images in the margins, by side, one
        of which is ``None``, while images without one are scaled like the
        subfigures"""
        return self._margin_sizes

    @property
    def width(self) -> numeric:
        """Width of the figure in [px]"""
        return self._width

    @width.setter
    def width(self, w: numeric):
        self._width = w

    @property
    def size(self) -> tuple[numeric, numeric]:
        return (self.width, self.height)

    def add_child(self, child: Path) -> Self:
        """Adds a subimage to the last row of the figure."""
        return self.add_children((child,))

    def add_children(self, children: Iterable[Path]) -> Self:
        """Adds subimages to the last row of the figure."""

        if not self._rows:
            self._rows.append([])

        self._rows[-1].extend(children)
        return self

    def add_row(self, children: Iterable[Path] = ()) -> Self:
        """Starts a new row of subimages below the previous ones."""
        self._rows.append(list(children))
        return self

    def set_margin(
        self,
        side: str,
        child: Path,
        width: numeric | None = None,
        height: numeric | None = None,
    ) -> Self:
        """Puts an image into the margin at a side of the subfigures, e.g. a colorbar
        that applies to all of them.

        The side is one of ``top``, ``bottom``, ``left`` and ``right``, each taking a
        single image, which spans the subfigures along that side.

        The image is rendered at the given ``width`` or ``height`` in [px], keeping
        its aspect ratio, or else scaled from its physical size like the subfigures.
        """

        if side not in self.MARGIN_SIDES:
            raise ValueError(
                f"Unknown margin '{side}', known margins are: "
                + ", ".join(self.MARGIN_SIDES)
            )

        if width is not None and height is not None:
            raise ValueError(
                f"The image in the {side} margin takes either a width or a height, "
                "as it keeps its aspect ratio."
            )

        self._margins[side] = child

        if width is None and height is None:
            self._margin_sizes.pop(side, None)
        else:
            self._margin_sizes[side] = (width, height)

        return self

    def save(self, path: Path) -> Self:
        """Save the total figure to the specified path.

        Each row spans the width of the subfigures, which are scaled to a common
        height within it. The labels of a row share a baseline, and their ink is kept
        ``gap_label`` below the subfigures and ``gap_row`` above the next row.

        Images in the margins are rendered at their given width or height, or else
        scaled from their physical size like the subfigures, or by their mean scale if
        the subfigures are scaled differently. They are kept ``gap_margin`` apart from
        the subfigures and centered along their side: at the top and bottom on the
        width of the rows, to the left and right on the height from the top of the
        first row to the bottom of the last row's subfigures. The figure spans
        ``width`` and ends right below the labels of the last row, unless the margins
        extend further.

        Without ``labels``, the rows are kept ``gap_row`` apart from each other and the
        figure ends right below the last row's subfigures instead.
        """

        svg = etree.Element(f"{{{self.NS_SVG}}}svg", nsmap=self.NSMAP)

        # subfigures and labels of each row, along with the extent of the labels' ink
        # above and below their common baseline, which is empty without labels
        rows = []
        no = 0

        for row in filter(None, self._rows):
            subfigs = [
                self._load_image(pth, f"subfig-{no + i}", f"img-{no + i}-")
                for i, pth in enumerate(row)
            ]
            labels = [self._label(no + i) for i in range(len(row))] if self.labels else []
            extents = [self._label_extent(runs) for runs in labels]
            ascent = max((top for top, _ in extents), default=0.0)
            descent = max((bottom for _, bottom in extents), default=0.0)
            rows.append((subfigs, labels, ascent, descent))
            no += len(row)

        # unlabeled subfigures leave no gap for labels below them
        gap_label = self.gap_label if self.labels else 0.0

        margins = {
            side: self._load_image(pth, f"margin-{side}", f"img-{side}-")
            for side, pth in self._margins.items()
        }

        # subfigures of a row share their height and fill the width between the gaps,
        # so rows of width w stack subfigures to the height slope * w + offset, from
        # the top of the first to the bottom of the last row's subfigures
        aspects = [sum(p.width / p.height for p in subfigs) for subfigs, *_ in rows]
        slope = sum(1 / aspect for aspect in aspects)
        offset = sum(
            -self.gap_between * (len(subfigs) - 1) / aspect
            for (subfigs, *_), aspect in zip(rows, aspects)
        ) + sum(
            gap_label + ascent + descent + self.gap_row
            for _, _, ascent, descent in rows[:-1]
        )

        # a subfigure is scaled from its physical size by the height of its row over
        # its physical height, so on average by scale_slope * w + scale_offset
        factors = [
            (1 / (aspect * p.height * p.unit), self.gap_between * (len(subfigs) - 1))
            for (subfigs, *_), aspect in zip(rows, aspects)
            for p in subfigs
        ]
        scale_slope = sum(f for f, _ in factors) / (len(factors) or 1)
        scale_offset = -sum(f * g for f, g in factors) / (len(factors) or 1)

        # rendered size of the images in the margins that are given one, keeping
        # their aspect ratio
        size = {}

        for side, p in margins.items():
            if side in self._margin_sizes:
                w, h = self._margin_sizes[side]
                size[side] = (
                    (w, w * p.height / p.width)
                    if w is not None
                    else (h * p.width / p.height, h)
                )

        # images to the left and right take their width off the rows, either a given
        # one or scaled alike
        sides = [side for side in ("left", "right") if side in margins]
        w_given = sum(size[side][0] for side in sides if side in size)
        w_sides = sum(
            margins[side].width * margins[side].unit for side in sides if side not in size
        )
        w_rows = (
            self.width - self.gap_margin * len(sides) - w_given - w_sides * scale_offset
        ) / (1 + w_sides * scale_slope)
        h_rows = slope * w_rows + offset
        scale = scale_slope * w_rows + scale_offset

        if any(w_rows <= self.gap_between * (len(subfigs) - 1) for subfigs, *_ in rows):
            raise ValueError(
                "The figure is too narrow to fit the subfigures between the gaps."
            )

        # rendered size of the other images in the margins
        for side, p in margins.items():
            if side not in size:
                size[side] = (scale * p.width * p.unit, scale * p.height * p.unit)

        for side in ("top", "bottom"):
            if side in size and size[side][0] > w_rows:
                raise ValueError(
                    f"The image in the {side} margin is wider than the subfigures."
                )

        x_rows = size["left"][0] + self.gap_margin if "left" in margins else 0.0
        y_rows = 0.0

        if "top" in margins:
            w, h = size["top"]
            self._place_image(svg, margins["top"], x_rows + (w_rows - w) / 2, 0.0, w, h)
            y_rows = h + self.gap_margin

        # images to the left and right that are taller than the subfigures overhang
        # them evenly
        h_sides = max(
            (h for side, (_, h) in size.items() if side in ("left", "right")), default=0.0
        )
        overhang = max(h_sides - h_rows, 0.0) / 2
        y_rows += overhang

        y = y_rows
        height = y
        no = 0

        for (subfigs, labels, ascent, descent), aspect in zip(rows, aspects):

            h = (w_rows - self.gap_between * (len(subfigs) - 1)) / aspect
            baseline = y + h + gap_label + ascent
            x = x_rows

            for i, picture in enumerate(subfigs):
                w = h * picture.width / picture.height
                self._place_image(svg, picture, x, y, w, h)

                if labels:
                    self._add_label(svg, no, x + 0.5 * w, baseline, labels[i])

                x += w + self.gap_between
                no += 1

            height = baseline + descent
            y = height + self.gap_row

        for side, x in (("left", 0.0), ("right", x_rows + w_rows + self.gap_margin)):
            if side in margins:
                w, h = size[side]
                self._place_image(svg, margins[side], x, y_rows + (h_rows - h) / 2, w, h)

        height = max(height, y_rows + h_rows + overhang)

        if "bottom" in margins:
            w, h = size["bottom"]
            y = height + self.gap_margin
            self._place_image(svg, margins["bottom"], x_rows + (w_rows - w) / 2, y, w, h)
            height = y + h

        svg.set("viewBox", f"0 0 {self.width} {height}")

        tree = etree.ElementTree(svg)
        tree.write(path, xml_declaration=True, encoding="utf-8")

        return self

    def _label(self, no: int) -> list[Run]:
        """Label of a subfigure as runs of text, see ``parse_scripts``.

        The label is followed by the description of the subfigure if there is one,
        which may contain sub- and superscripts in TeX notation.
        """

        text = f"({chr(97 + self.index_offset + no)})"

        if no < len(self.description) and self.description[no]:
            # the leading blank puts the description's first run on the baseline
            first, *runs = parse_scripts(f" {self.description[no]}")
            return [(text + first[0], 0.0, 1.0), *runs]

        return [(text, 0.0, 1.0)]

    def _label_extent(self, runs: list[Run]) -> tuple[float, float]:
        """Extent of the ink of a label above and below its baseline in [px]."""

        top = bottom = 0.0

        for chars, rise, size in runs:
            top_run, bottom_run = text_extent(chars, self.font_family)
            top = max(top, rise + top_run * size)
            bottom = max(bottom, bottom_run * size - rise)

        return top * self.font_size, bottom * self.font_size

    def _set_label_text(self, label: etree._Element, runs: list[Run]):
        """Writes the runs of a label into its text element.

        Text on the baseline that leads the label goes into the element itself, any
        further runs into spans. As ``dy`` shifts the baseline of the text that
        follows as well, each span shifts it relative to the one before.
        """

        rise = 0.0

        for chars, rise_run, size in runs:

            if len(label) == 0 and (rise_run, size) == (0.0, 1.0):
                label.text = chars
                continue

            span = etree.SubElement(label, f"{{{self.NS_SVG}}}tspan")
            span.text = chars

            if size != 1.0:
                span.set("font-size", str(size * self.font_size))

            if rise_run != rise:
                span.set("dy", str((rise - rise_run) * self.font_size))
                rise = rise_run

    def _place_image(
        self,
        svg: etree._Element,
        picture: Picture,
        x: float,
        y: float,
        w: float,
        h: float,
    ):
        """Appends an image read by ``_load_image`` to the figure, scaled to a width
        and height of the same aspect ratio, with its top left corner at (x, y)."""

        img = picture.element

        if img.tag == f"{{{self.NS_SVG}}}image":
            img.set("width", str(w))
            img.set("height", str(h))
            img.set("x", str(x))
            img.set("y", str(y))
        else:
            img.set(
                "transform",
                f"translate({str(x)}, {str(y)}) scale({w / picture.width})",
            )

        svg.append(img)

    def _add_label(
        self, svg: etree._Element, no: int, x: float, y: float, runs: list[Run]
    ):
        """Appends the label of a subfigure, centered at x on the baseline y."""

        label = etree.SubElement(svg, f"{{{self.NS_SVG}}}text", id=f"label-{no}")
        label.set("x", str(x))
        label.set("y", str(y))
        label.set("text-anchor", "middle")
        label.set("font-size", str(self.font_size))
        label.set("font-family", self.font_family)

        # blanks leading a span would be dropped otherwise
        label.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
        self._set_label_text(label, runs)

    def _load_image(self, pth_subfig: Path, id_image: str, id_prefix: str) -> Picture:
        """Reads an image into an element that is yet to be placed.

        The element is given the id ``id_image``, while the ids within an SVG image
        are prefixed with ``id_prefix`` to keep them unique within the figure.
        """

        if pth_subfig.suffix == ".svg":
            img_svg = etree.parse(pth_subfig)
            for node in img_svg.xpath("//*[@id]"):
                id_old = node.get("id")
                id_new = id_prefix + id_old
                node.set("id", id_new)
                for node in img_svg.xpath(f"//*[@*[contains(.,'#{id_old}')]]"):
                    for key, value in node.items():
                        if id_old in value:
                            node.set(key, value.replace(id_old, id_new))
            root_subfig = img_svg.getroot()

            # the physical size is the one of the viewport, if given absolutely
            w_px = length_to_px(root_subfig.get("width"))
            w, h = normalize_svg(root_subfig)

            img = etree.Element(f"{{{self.NS_SVG}}}g", id=id_image)
            img.append(root_subfig)

            return Picture(img, w, h, w_px / w if w_px else 1.0)

        # check format
        if pth_subfig.suffix == ".png":
            format = "png"
        elif pth_subfig.suffix in [".jpg", ".jpeg"]:
            format = "jpeg"
        else:
            raise Exception("Unknown picture format. Known formats are: PNG and JPG.")

        with Image.open(pth_subfig) as raster:
            w, h = raster.size
            dpi = raster.info.get("dpi")

        with pth_subfig.open(mode="rb") as f:
            img_encoded = base64.b64encode(f.read())

        img = etree.Element(f"{{{self.NS_SVG}}}image", id=id_image)
        img.set(
            f"{{{self.NS_XLINK}}}href",
            f"data:image/{format};base64,{img_encoded.decode('utf-8')}",
        )

        # pixels measure 1/96 in, unless the image states its resolution
        return Picture(img, w, h, 96 / float(dpi[0]) if dpi and dpi[0] else 1.0)


def convert_svg(pth: Path, formats: str = "pdf,png,svg"):
    subprocess.call(
        f'inkscape --export-type="{formats}" --export-overwrite -d 600 -D -T ' + str(pth)
    )
