import svgsubfig.utility as util
import argparse

from svgsubfig import SVGSubFigure
from pathlib import Path

# initialize argument parser
parser = argparse.ArgumentParser("SVGSubFig generator")
parser.add_argument("config")
parser.add_argument("--noconvert", action='store_true')
args = parser.parse_args()

# configuration file path
pth_config = Path(args.config)

# append .json file type if not given
if not pth_config.suffix:
    pth_config = pth_config.with_suffix(".json")

# derive figure file path
pth_svg = pth_config.with_suffix(".svg")

# read figure configuration and create figure
fig = SVGSubFigure.from_json(pth_config)
fig.save(pth_svg)

# convert SVG into PDF & PNG
if not args.noconvert:
    util.convert_svg(pth_svg)
