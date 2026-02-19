import hashlib
from pathlib import Path

import svgsubfig.utility as util
from svgsubfig import SVGSubFigure

pth_config = Path("./tests/plots.json")
pth_svg = Path("./assets/plots.svg")

fig = SVGSubFigure.from_json(pth_config)
fig.save(pth_svg)

util.convert_svg(pth_svg)


def test_plots():

    hash_expected = "23a63ed9c039f147d3d40e92934dc66ac3e24b0803ee937337bce79e2666fa38"

    with pth_svg.with_suffix(".png").open(mode="rb") as f:
        h = hashlib.file_digest(f, "sha256")

        assert h.hexdigest() == hash_expected
