import base64
import subprocess
import json
import svgsubfig.utility as util

from typing import Union
from pathlib import Path
from lxml import etree
from PIL import Image


numeric = Union[float, int]

MM2PX = 3.77952755906
PT2PX = 4 / 3


class SVGSubFigure:

    NS_SVG = "http://www.w3.org/2000/svg"
    NS_XLINK = "http://www.w3.org/1999/xlink"

    NSMAP = {
        None: NS_SVG,
        "xlink": NS_XLINK,
    }

    def __init__(self):
        self._children = []
        self._font_size = 12
        self._font_family = "Arial, Helvetica, sans-serif"
        self._gap_between = 20
        self._gap_label = 10

    @classmethod
    def from_json(cls, pth: Path) -> SVGSubFigure:

        ins = cls()

        with pth.open(encoding="utf-8", mode="r") as f:
            data = json.load(f)
            ins._font_size = data["font-size"] * util.PT2PX
            ins._font_family = data["font-family"]
            ins._gap_between = data["gap-between"] * util.MM2PX
            ins._gap_label = data["gap-label"] * util.MM2PX
            ins._width = data["width"] * util.MM2PX

            if "images" in data:
                for pth_img in data["images"]:
                    ins.add_child(pth.parent / pth_img)

        return ins

    @property
    def font_family(self) -> str:
        """Label font family string."""
        return self._font_family

    @font_family.setter
    def font_family(self, f: str):
        self._font_family = f

    @property
    def font_size(self) -> numeric:
        """Label font size in px."""
        return self._font_size

    @font_size.setter
    def font_size(self, f: numeric):
        self._font_size = f

    @property
    def gap_between(self) -> numeric:
        """Spacing between subfigures."""
        return self._gap_between

    @gap_between.setter
    def gap_between(self, g: numeric):
        self._gap_between = g

    @property
    def gap_label(self) -> numeric:
        "Spacing between subfigure and label"
        return self._gap_label

    @gap_label.setter
    def gap_label(self, g: numeric):
        self._gap_label = g

    @property
    def height(self) -> numeric:
        """Height of the figure."""
        return self._height

    @height.setter
    def height(self, h: numeric):
        self._height = h

    @property
    def width(self) -> numeric:
        """Width of the figure."""
        return self._width

    @width.setter
    def width(self, w: numeric):
        self._width = w

    @property
    def size(self) -> tuple[numeric, numeric]:
        return (self.width, self.height)

    def add_child(self, child: Path) -> SVGSubFigure:
        self._children.append(child)
        return self

    def add_children(self, children: tuple[Path, ...]) -> SVGSubFigure:
        """Adds subimages to the figure."""
        self._children.extend(children)
        return self

    def save(self, path: Path) -> SVGSubFigure:
        """Save the total figure to the specified path."""

        w_netto = self.width - self.gap_between * (len(self._children) - 1)

        w_single = []
        h_single = []
        a_single = []

        for pth_img in self._children:
            if pth_img.suffix != ".svg":
                w, h = Image.open(pth_img).size
            else:
                img_svg = etree.parse(pth_img)
                vb = img_svg.xpath('//*[name()="svg"]/@viewBox')
                if vb:
                    w = float(vb[0].split(" ")[2])  # type: ignore
                    h = float(vb[0].split(" ")[3])  # type: ignore
                else:
                    w = float(img_svg.xpath('//*[name()="svg"]/@width')[0])  # type: ignore
                    h = float(img_svg.xpath('//*[name()="svg"]/@height')[0])  # type: ignore

            w_single.append(w)
            h_single.append(h)
            a_single.append(w / h)

        a_single_total = sum(a_single)

        w_frac = []

        for i in range(len(a_single)):
            w_frac.append(w_netto * a_single[i] / a_single_total)

        h = w_frac[0] / a_single[0]

        svg = etree.Element(f"{{{self.NS_SVG}}}svg", nsmap=self.NSMAP)
        svg.set(
            "viewBox", f"0 0 {self.width} {h + self.font_size * 2 + self.gap_between}"
        )

        x = 0
        y = 0

        for no, pth_subfig in enumerate(self._children):

            if pth_subfig.suffix == ".svg":
                img_svg = etree.parse(pth_subfig)
                for node in img_svg.xpath("//*[@id]"):
                    id_old = node.get("id")
                    id_new = f"img-{no}-" + id_old
                    node.set("id", id_new)
                    for node in img_svg.xpath(f"//*[@*[contains(.,'#{id_old}')]]"):
                        for key, value in node.items():
                            if id_old in value:
                                node.set(key, value.replace(id_old, id_new))
                img = etree.SubElement(svg, f"{{{self.NS_SVG}}}g", id=f"subfig-{no}")
                img.append(img_svg.getroot())
                img.set(
                    "transform",
                    f"translate({str(x)}, {str(y)}) scale({w_frac[no] / w_single[no]})",
                )

            else:

                # check format
                if pth_subfig.suffix == ".png":
                    format = "png"
                elif pth_subfig.suffix in [".jpg", ".jpeg"]:
                    format = "jpeg"
                else:
                    raise Exception(
                        "Unknown picture format. Known formats are: PNG and JPG."
                    )

                with pth_subfig.open(mode="rb") as f:
                    img_encoded = base64.b64encode(f.read())

                img = etree.SubElement(
                    svg, f"{{{self.NS_SVG}}}image", id=f"subfig-{no}"
                )
                img.set(
                    f"{{{self.NS_XLINK}}}href",
                    f"data:image/{format};base64,{img_encoded.decode("utf-8")}",
                )
                img.set("width", str(w_frac[no]))
                img.set("height", str(w_frac[no] / a_single[no]))
                img.set("x", str(x))
                img.set("y", str(y))

            label = etree.SubElement(svg, f"{{{self.NS_SVG}}}text")
            label.set("x", str(x + 0.5 * w_frac[no]))
            label.set("y", str(y + h + self.font_size + self.gap_label))
            label.set("text-anchor", "middle")
            label.set("vertical-align", "hanging")
            label.set("font-size", str(self.font_size))
            label.set("font-family", self.font_family)
            label.text = f"({chr(97 + no)})"

            x += w_frac[no] + self.gap_between

        tree = etree.ElementTree(svg)
        tree.write(path, pretty_print=True, xml_declaration=True, encoding="utf-8")

        return self


def convert_svg(pth: Path):
    subprocess.call(
        'inkscape --export-type="pdf,png,svg" --export-overwrite -d 600 -D -T '
        + str(pth)
    )
