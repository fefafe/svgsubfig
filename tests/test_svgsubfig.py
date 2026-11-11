import hashlib
import json
import subprocess
import warnings
from pathlib import Path

import pytest
from lxml import etree
from PIL import Image

import svgsubfig.utility as util
from svgsubfig import SVGSubFigure
from svgsubfig.font import FALLBACK_EXTENT, find_font
from svgsubfig.markup import parse_scripts

pth_config = Path("./tests/plots.json")

# output of the tests lands in a directory of its own, ignored by git
pth_tmp = Path("./tests/tmp")
pth_tmp.mkdir(exist_ok=True)
pth_svg = pth_tmp / "plots.svg"

fig = SVGSubFigure.from_json(pth_config)
fig.save(pth_svg)

util.convert_svg(pth_svg)


def test_plots():

    hash_expected = "01ae7f1c4aec2c5cf1bb981c1b813d1dad85e8b4c98fbee595c21e38506ac8e8"

    with pth_svg.with_suffix(".png").open(mode="rb") as f:
        h = hashlib.file_digest(f, "sha256")

        assert h.hexdigest() == hash_expected


# -----------------------------------------------------------------------------
# subfigures exported by MATLAB >= R2025a carry their size in [mm] next to a
# viewBox in [pt]; taking the viewBox for the size while leaving the [mm]
# viewport in place rendered them 4/3 too large


def _subfigure_geometry(pth_svg: Path, prefix="subfig-") -> list[tuple[float, ...]]:
    """(x, y, rendered width, rendered height) of every subfigure of a composed figure,
    or of the other images whose ids start with the prefix."""

    root = etree.parse(pth_svg).getroot()
    geometry = []

    for node in root.xpath(f'//*[starts-with(@id,"{prefix}")]'):

        # raster subfigures are placed by x/width instead of a transform
        if node.get("transform") is None:
            geometry.append(
                tuple(float(node.get(key)) for key in ("x", "y", "width", "height"))
            )
            continue

        transform = node.get("transform")
        x, y = (
            float(v) for v in transform.split("translate(")[1].split(")")[0].split(",")
        )
        scale = float(transform.split("scale(")[1].rstrip(")"))
        nested = node[0]
        vb = nested.get("viewBox")

        assert vb is None or nested.get("width") == str(
            float(vb.split()[2])
        ), "nested viewport must be restated in the units of the viewBox"

        geometry.append(
            (
                x,
                y,
                float(nested.get("width")) * scale,
                float(nested.get("height")) * scale,
            )
        )

    return geometry


def test_length_to_px():

    assert util.length_to_px("908.04688") == 908.04688
    assert util.length_to_px("180.14417mm") == pytest.approx(680.85986, rel=1e-6)
    assert util.length_to_px("510.64614pt") == pytest.approx(680.86152, rel=1e-6)
    assert util.length_to_px("2in") == 192.0

    # relative units cannot be resolved without a rendering context
    assert util.length_to_px("100%") is None
    assert util.length_to_px("12em") is None
    assert util.length_to_px(None) is None


def test_normalize_svg_neutralizes_the_viewport():

    root = etree.parse(Path("./tests/matlab_r2025a.svg")).getroot()

    assert util.normalize_svg(root) == (510.64614, 323.44599)

    # the [mm] viewport would otherwise rescale the content by another 4/3
    assert root.get("width") == "510.64614"
    assert root.get("height") == "323.44599"


def test_matlab_r2025a_subfigures_fill_their_slot(tmp_path):

    fig = SVGSubFigure()
    fig.gap_between = 5 * util.MM2PX
    fig.width = 120 * util.MM2PX
    fig.add_children(
        (Path("./tests/matlab_r2025a.svg"), Path("./tests/matlab_r2025a.svg"))
    )

    pth_svg = tmp_path / "matlab_r2025a.svg"
    fig.save(pth_svg)

    geometry = _subfigure_geometry(pth_svg)

    # two subfigures of equal aspect share the width left by the gap
    w_expected = (fig.width - fig.gap_between) / 2

    for x, _, w, _ in geometry:
        assert w == pytest.approx(w_expected)

    assert geometry[1][0] == pytest.approx(geometry[0][2] + fig.gap_between)
    assert geometry[1][0] + geometry[1][2] == pytest.approx(fig.width)


def test_matlab_export_formats_agree(tmp_path):
    """R2024b and R2025a exports of the same plot must compose identically."""

    geometry = []

    for name in ("matlab_r2024b", "matlab_r2025a"):
        fig = SVGSubFigure()
        fig.gap_between = 5 * util.MM2PX
        fig.width = 120 * util.MM2PX
        fig.add_children((Path(f"./tests/{name}.svg"), Path("./assets/plot_2.png")))

        pth_svg = tmp_path / f"{name}.svg"
        fig.save(pth_svg)
        geometry.append(_subfigure_geometry(pth_svg))

    assert geometry[0] == pytest.approx(geometry[1])


# -----------------------------------------------------------------------------
# a nested array of images lays the subfigures out in rows, each spanning the
# width of the figure; the labels of a row share a baseline and are cropped
# tightly to their ink


def _rows_config(tmp_path: Path, **kwargs) -> Path:
    """Writes a figure configuration of three rows, the second of three subfigures.

    Keys given as ``None`` are left out of the configuration."""

    pth_png = Path("./assets/plot_2.png").resolve()
    pth_svg = Path("./assets/plot_1.svg").resolve()

    config = {
        "gap-between": 5,
        "gap-label": 2,
        "width": 120,
        "font-size": 9,
        "font-family": "Inter",
        "images": [
            [str(pth_svg), str(pth_png)],
            [str(pth_png), str(pth_svg), str(pth_png)],
            str(pth_png),
        ],
        "description": [["Dog", "Cat"], ["Sine", "Cubic"], ["Wide"]],
    } | kwargs

    pth_config = tmp_path / "rows.json"
    pth_config.write_text(
        json.dumps({key: value for key, value in config.items() if value is not None}),
        encoding="utf-8",
    )

    return pth_config


def _ink(pth_svg: Path) -> dict[str, tuple[float, ...]]:
    """(x, y, width, height) of the ink of every object, as rendered by Inkscape."""

    output = subprocess.run(
        ["inkscape", "--query-all", str(pth_svg)], capture_output=True, text=True
    ).stdout

    return {
        line.split(",")[0]: tuple(float(v) for v in line.split(",")[1:])
        for line in output.splitlines()
        if line.count(",") == 4
    }


def test_as_rows():

    assert util.as_rows(["a", "b"]) == [["a", "b"]]
    assert util.as_rows([["a", "b"], ["c"]]) == [["a", "b"], ["c"]]
    assert util.as_rows(["a", ["b", "c"]]) == [["a"], ["b", "c"]]


def test_rows_from_json(tmp_path):

    fig = SVGSubFigure.from_json(_rows_config(tmp_path))

    assert [len(row) for row in fig.rows] == [2, 3, 1]
    assert [pth.name for pth in fig.children] == [
        "plot_1.svg",
        "plot_2.png",
        "plot_2.png",
        "plot_1.svg",
        "plot_2.png",
        "plot_2.png",
    ]

    # nested descriptions stay with their rows, missing ones are left blank
    assert fig.description == ["Dog", "Cat", "Sine", "Cubic", "", "Wide"]


def test_rows_fill_the_width(tmp_path):

    fig = SVGSubFigure.from_json(_rows_config(tmp_path))
    pth_svg = tmp_path / "rows.svg"
    fig.save(pth_svg)

    geometry = _subfigure_geometry(pth_svg)
    no = 0

    for row in fig.rows:
        subfigs = geometry[no:][: len(row)]
        no += len(row)

        # subfigures of a row share their top and height
        for x, y, w, h in subfigs:
            assert y == pytest.approx(subfigs[0][1])
            assert h == pytest.approx(subfigs[0][3])

        for left, right in zip(subfigs, subfigs[1:]):
            assert right[0] == pytest.approx(left[0] + left[2] + fig.gap_between)

        assert subfigs[0][0] == 0
        assert subfigs[-1][0] + subfigs[-1][2] == pytest.approx(fig.width)


def test_gap_row(tmp_path):

    fig = SVGSubFigure()
    fig.gap_between = 7

    # rows are spaced like the subfigures within them unless set otherwise
    assert fig.gap_row == 7

    fig.gap_row = 3

    assert fig.gap_row == 3
    assert fig.gap_between == 7

    fig = SVGSubFigure.from_json(_rows_config(tmp_path, **{"gap-row": 8}))

    assert fig.gap_row == pytest.approx(8 * util.MM2PX)
    assert fig.gap_between == pytest.approx(5 * util.MM2PX)


@pytest.mark.skipif(find_font("Inter") is None, reason="font Inter is not installed")
@pytest.mark.parametrize(
    "config",
    [
        {},
        {"gap-row": 8},
        {
            "description": [
                ["Dog^{2}", "CO_{2} gas"],
                ["e^{x_{i}}", "Cubic", ""],
                ["W_{p}ide"],
            ]
        },
    ],
)
def test_labels_are_tight(tmp_path, config):

    fig = SVGSubFigure.from_json(_rows_config(tmp_path, **config))
    pth_svg = tmp_path / "rows.svg"
    fig.save(pth_svg)

    root = etree.parse(pth_svg).getroot()
    geometry = _subfigure_geometry(pth_svg)
    ink = _ink(pth_svg)

    no = 0
    y_next = 0.0

    for row in fig.rows:
        ids = range(no, no + len(row))
        no += len(row)

        # labels of a row share a baseline
        assert len({root.find(f".//*[@id='label-{i}']").get("y") for i in ids}) == 1

        top = min(ink[f"label-{i}"][1] for i in ids)
        bottom = max(ink[f"label-{i}"][1] + ink[f"label-{i}"][3] for i in ids)
        y, h = geometry[ids[0]][1], geometry[ids[0]][3]

        assert y == pytest.approx(y_next, abs=1e-2)
        assert top == pytest.approx(y + h + fig.gap_label, abs=1e-2)

        y_next = bottom + fig.gap_row

    # the figure ends where the ink of the last labels does
    assert float(root.get("viewBox").split()[3]) == pytest.approx(bottom, abs=1e-2)


def test_missing_font_falls_back(tmp_path):

    fig = SVGSubFigure()
    fig.font_family = "No Such Font"
    fig.add_child(Path("./assets/plot_2.png"))

    pth_svg = tmp_path / "fallback.svg"

    with pytest.warns(UserWarning, match="approximate font metrics"):
        fig.save(pth_svg)

    _, _, _, h = _subfigure_geometry(pth_svg)[0]
    height = float(etree.parse(pth_svg).getroot().get("viewBox").split()[3])

    assert height == pytest.approx(
        h + fig.gap_label + sum(FALLBACK_EXTENT) * fig.font_size
    )


def test_without_labels(tmp_path):

    assert SVGSubFigure().labels

    # unlabeled subfigures need neither a typeface nor a gap to their labels
    fig = SVGSubFigure.from_json(
        _rows_config(
            tmp_path,
            **{
                "labels": False,
                "gap-row": 3,
                "font-size": None,
                "font-family": None,
                "gap-label": None,
            },
        )
    )

    assert not fig.labels

    # nor is a font looked up for them
    fig.font_family = "No Such Font"
    pth_svg = tmp_path / "rows.svg"

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        fig.save(pth_svg)

    root = etree.parse(pth_svg).getroot()
    geometry = _subfigure_geometry(pth_svg)

    assert root.findall("{http://www.w3.org/2000/svg}text") == []

    # the rows follow each other at gap_row and still fill the width, and the figure
    # ends at the bottom of the last row's subfigures
    no = 0
    y_next = 0.0

    for row in fig.rows:
        subfigs = geometry[no:][: len(row)]
        no += len(row)
        _, y, _, h = subfigs[0]

        assert y == pytest.approx(y_next)
        assert subfigs[-1][0] + subfigs[-1][2] == pytest.approx(fig.width)

        y_next = y + h + fig.gap_row

    assert _height(pth_svg) == pytest.approx(y + h)


# -----------------------------------------------------------------------------
# descriptions set sub- and superscripts in TeX notation


def _runs(text: str) -> tuple[list[str], list[float], list[float]]:
    runs = parse_scripts(text)
    return [r[0] for r in runs], [r[1] for r in runs], [r[2] for r in runs]


def test_parse_scripts():

    assert parse_scripts("plain (a) Dog") == [("plain (a) Dog", 0.0, 1.0)]

    chars, rise, size = _runs("CO_{2} gas")
    assert chars == ["CO", "2", " gas"]
    assert rise == pytest.approx([0.0, -0.15, 0.0])
    assert size == pytest.approx([1.0, 0.7, 1.0])

    # a script without braces takes the next character alone
    chars, rise, _ = _runs("x^2 + y^{10}")
    assert chars == ["x", "2", " + y", "10"]
    assert rise == pytest.approx([0.0, 0.363, 0.0, 0.363])

    # nested scripts rise and shrink relative to the script they are attached to
    chars, rise, size = _runs("e^{x_{i}}")
    assert chars == ["e", "x", "i"]
    assert rise == pytest.approx([0.0, 0.363, 0.363 - 0.15 * 0.7])
    assert size == pytest.approx([1.0, 0.7, 0.49])

    # escaped characters are literal, braces only group
    assert parse_scripts(r"a\_b \^ \{c\} {d}") == [("a_b ^ {c} d", 0.0, 1.0)]
    assert parse_scripts("trailing_") == [("trailing_", 0.0, 1.0)]


def test_scripts_in_description(tmp_path):

    fig = SVGSubFigure()
    fig.add_children((Path("./assets/plot_2.png"), Path("./assets/plot_2.png")))
    fig.description = ["CO_{2} gas", "Dog"]

    pth_svg = tmp_path / "scripts.svg"
    fig.save(pth_svg)

    root = etree.parse(pth_svg).getroot()
    labels = root.findall("{http://www.w3.org/2000/svg}text")

    assert ["".join(label.itertext()) for label in labels] == ["(a) CO2 gas", "(b) Dog"]

    # the subscript is set smaller and below the baseline, which the text after it
    # returns to
    spans = labels[0].findall("{http://www.w3.org/2000/svg}tspan")
    assert labels[0].text == "(a) CO"
    assert float(spans[0].get("font-size")) == pytest.approx(0.7 * fig.font_size)
    assert float(spans[0].get("dy")) == pytest.approx(0.15 * fig.font_size)
    assert float(spans[1].get("dy")) == pytest.approx(-0.15 * fig.font_size)

    # blanks leading a span must survive, plain labels need no spans
    assert labels[0].get("{http://www.w3.org/XML/1998/namespace}space") == "preserve"
    assert len(labels[1]) == 0


# -----------------------------------------------------------------------------
# a single image in each margin, e.g. a colorbar for all subfigures, is scaled from
# its physical size like the subfigures and centered along its side


def _svg_mm(tmp_path: Path, w: float, h: float) -> Path:
    """Writes a plain SVG image of the given size in [mm], its viewBox in [pt] like
    MATLAB exports them since R2025a."""

    pth = tmp_path / f"image_{w}x{h}mm.svg"
    w_pt, h_pt = w * 72 / 25.4, h * 72 / 25.4
    pth.write_text(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}mm" height="{h}mm" '
        f'viewBox="0 0 {w_pt} {h_pt}">'
        f'<rect id="area" width="{w_pt}" height="{h_pt}" fill="#21918c"/></svg>',
        encoding="utf-8",
    )

    return pth


def _margin_config(tmp_path: Path, margin: dict[str, Path], **kwargs) -> Path:
    """Two rows of two subfigures of 90 mm x 60 mm each, with images in the margins."""

    pth_plot = str(_svg_mm(tmp_path, 90, 60))

    return _rows_config(
        tmp_path,
        **{
            "images": [[pth_plot, pth_plot], [pth_plot, pth_plot]],
            "description": [],
            "margin": {
                side: m | {"image": str(m["image"])} if isinstance(m, dict) else str(m)
                for side, m in margin.items()
            },
            "gap-margin": 3,
        }
        | kwargs,
    )


def _colorbars(tmp_path: Path, sides: tuple[str, ...]) -> dict[str, Path]:
    """Vertical colorbars of 10 mm x 50 mm to the left and right, horizontal ones of
    60 mm x 10 mm at the top and bottom."""

    return {
        side: (
            _svg_mm(tmp_path, 10, 50)
            if side in ("left", "right")
            else _svg_mm(tmp_path, 60, 10)
        )
        for side in sides
    }


def _height(pth_svg: Path) -> float:
    return float(etree.parse(pth_svg).getroot().get("viewBox").split()[3])


def test_margin_config(tmp_path):

    fig = SVGSubFigure.from_json(
        _margin_config(tmp_path, _colorbars(tmp_path, ("top", "right")))
    )

    assert list(fig.margins) == ["top", "right"]
    assert fig.gap_margin == pytest.approx(3 * util.MM2PX)

    # images in the margins are spaced like the subfigures unless set otherwise
    fig = SVGSubFigure()
    fig.gap_between = 7

    assert fig.gap_margin == 7

    with pytest.raises(ValueError, match="Unknown margin"):
        fig.set_margin("middle", Path("./assets/plot_2.png"))


@pytest.mark.parametrize(
    "sides",
    [("left",), ("right",), ("top",), ("bottom",), ("top", "bottom", "left", "right")],
)
def test_margins_match_the_scale(tmp_path, sides):

    fig = SVGSubFigure.from_json(_margin_config(tmp_path, _colorbars(tmp_path, sides)))
    pth_svg = tmp_path / "margins.svg"
    fig.save(pth_svg)

    root = etree.parse(pth_svg).getroot()
    subfigs = _subfigure_geometry(pth_svg)

    # subfigures of 90 mm x 60 mm all end up at the same scale
    scale = subfigs[0][2] / (90 * util.MM2PX)

    for _, _, w, h in subfigs:
        assert w == pytest.approx(scale * 90 * util.MM2PX)
        assert h == pytest.approx(scale * 60 * util.MM2PX)

    # extent of the subfigures, from the top of the first row to the bottom of the
    # last row's subfigures
    left = min(x for x, _, _, _ in subfigs)
    right = max(x + w for x, _, w, _ in subfigs)
    top = min(y for _, y, _, _ in subfigs)
    bottom = max(y + h for _, y, _, h in subfigs)

    margins = {side: _subfigure_geometry(pth_svg, f"margin-{side}")[0] for side in sides}

    for side, (x, y, w, h) in margins.items():

        if side in ("left", "right"):
            assert w == pytest.approx(scale * 10 * util.MM2PX)
            assert h == pytest.approx(scale * 50 * util.MM2PX)
            assert y + h / 2 == pytest.approx((top + bottom) / 2)

        if side in ("top", "bottom"):
            assert w == pytest.approx(scale * 60 * util.MM2PX)
            assert h == pytest.approx(scale * 10 * util.MM2PX)
            assert x + w / 2 == pytest.approx((left + right) / 2)

        if side == "left":
            assert x == 0
            assert left == pytest.approx(w + fig.gap_margin)

        if side == "right":
            assert x == pytest.approx(right + fig.gap_margin)

        if side == "top":
            assert y == 0
            assert top == pytest.approx(h + fig.gap_margin)

        if side == "bottom":
            assert y + h == pytest.approx(_height(pth_svg))

            # the image follows the labels of the last row, which end the figure
            # without it
            fig.margins.pop("bottom")
            fig.save(tmp_path / "no_bottom.svg")

            assert y == pytest.approx(
                _height(tmp_path / "no_bottom.svg") + fig.gap_margin
            )

    # the figure keeps its width and labels its subfigures only
    if "right" in sides:
        right = margins["right"][0] + margins["right"][2]

    assert right == pytest.approx(fig.width)
    assert "left" in sides or left == 0
    assert len(root.findall("{http://www.w3.org/2000/svg}text")) == len(subfigs)


def test_margin_scale_across_units(tmp_path):
    """Images of the same physical size in [mm], [px] or at a resolution of their
    pixels end up at the same size in the margin."""

    pth_px = tmp_path / "colorbar_px.svg"
    pth_px.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{60 * util.MM2PX}" height="{10 * util.MM2PX}">'
        "<rect width='100%' height='100%'/></svg>",
        encoding="utf-8",
    )

    # 600 px x 100 px at 254 dpi are 60 mm x 10 mm as well
    pth_png = tmp_path / "colorbar.png"
    Image.new("RGB", (600, 100)).save(pth_png, dpi=(254, 254))

    geometry = []

    for pth_colorbar in (_svg_mm(tmp_path, 60, 10), pth_px, pth_png):
        fig = SVGSubFigure.from_json(_margin_config(tmp_path, {"bottom": pth_colorbar}))
        pth_svg = tmp_path / "margins.svg"
        fig.save(pth_svg)
        geometry.append(_subfigure_geometry(pth_svg, "margin-bottom")[0])

    assert geometry[1] == pytest.approx(geometry[0])
    assert geometry[2] == pytest.approx(geometry[0])


def test_margin_taller_than_subfigures(tmp_path):

    fig = SVGSubFigure.from_json(
        _margin_config(tmp_path, {"right": _svg_mm(tmp_path, 10, 150)}, images=[])
    )
    fig.add_children([_svg_mm(tmp_path, 90, 60)] * 2)

    pth_svg = tmp_path / "margins.svg"
    fig.save(pth_svg)

    subfigs = _subfigure_geometry(pth_svg)
    x, y, w, h = _subfigure_geometry(pth_svg, "margin-right")[0]

    # the image overhangs the subfigures evenly, and the figure fits it
    top = subfigs[0][1]
    bottom = subfigs[0][1] + subfigs[0][3]

    assert y == 0
    assert y + h / 2 == pytest.approx((top + bottom) / 2)
    assert y + h == pytest.approx(_height(pth_svg))
    assert x + w == pytest.approx(fig.width)


@pytest.mark.parametrize(
    "side, key, given, other",
    [
        ("right", "height", 40, 8),
        ("left", "width", 6, 30),
        ("top", "width", 30, 5),
        ("bottom", "height", 4, 24),
    ],
)
def test_margin_given_size(tmp_path, side, key, given, other):
    """An image in the margin of a given width or height in [mm] keeps its aspect
    ratio, whatever the scale of the subfigures."""

    colorbar = _colorbars(tmp_path, (side,))[side]
    fig = SVGSubFigure.from_json(
        _margin_config(tmp_path, {side: {"image": colorbar, key: given}})
    )

    assert fig.margin_sizes[side] == pytest.approx(
        (given * util.MM2PX, None) if key == "width" else (None, given * util.MM2PX)
    )

    pth_svg = tmp_path / "margins.svg"
    fig.save(pth_svg)

    x, y, w, h = _subfigure_geometry(pth_svg, f"margin-{side}")[0]
    size = {"width": w, "height": h}

    assert size[key] == pytest.approx(given * util.MM2PX)
    assert size["height" if key == "width" else "width"] == pytest.approx(
        other * util.MM2PX
    )

    # images to the left and right of a given width still leave the figure its width
    subfigs = _subfigure_geometry(pth_svg)
    left = min(x for x, _, _, _ in subfigs)
    right = max(x + w for x, _, w, _ in subfigs)

    if side == "left":
        assert left == pytest.approx(w + fig.gap_margin)
        assert right == pytest.approx(fig.width)

    if side == "right":
        assert x + w == pytest.approx(fig.width)
        assert x == pytest.approx(right + fig.gap_margin)

    # setting the image anew without a size scales it like the subfigures again
    fig.set_margin(side, colorbar)

    assert side not in fig.margin_sizes


def test_margin_width_and_height(tmp_path):

    with pytest.raises(ValueError, match="either a width or a height"):
        SVGSubFigure().set_margin("right", Path("./assets/plot_2.png"), 10, 20)


def test_margin_too_wide(tmp_path):

    # a colorbar wider than the subfigures cannot be centered on them
    fig = SVGSubFigure.from_json(
        _margin_config(tmp_path, {"top": _svg_mm(tmp_path, 300, 10)})
    )

    with pytest.raises(ValueError, match="wider than the subfigures"):
        fig.save(tmp_path / "margins.svg")
