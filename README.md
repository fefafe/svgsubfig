# svgsubfig

A python package for swift arrangement of raster images and vector graphics into a single figure based on SVG.

The package is focussed on the preparation of high quality figures that consist of several subimages, both vector and raster graphics, for publication in scientific journals.

## Basic usage

Create a JSON configuration file with the following structure. The name of the config file will be used as name for the SVG figure file, which will be created in the same directory.

Use relative filenames for the individual images (the directory of the config file acts as base directory).

```json
{
    "gap-between": 5,
    "gap-label": 3,
    "width": 150,
    "font-size": 9,
    "font-family": "Arial, Helvetica, sans-serif",
    "images": [
        "img/dog.jpeg",
        "img/population.svg"
    ]
}
```

The keys of the config file are as follows:

- ``font-family``: Typeface used in the SVG file for text.
- ``font-size``: Size of the labeling of the images in **pt**.
- ``gab-between``: Spacing between the subimages in **mm**.
- ``gap-label``: Spacing between labels and lower boundary of the images.
- ``images``: Array of file paths of the images to include into the figure.
- ``width``: Width of the figure.

Create a Python script as below to merge the images into the figure.

The function for file conversion from SVG into PDF and PNG  ``convert_svg`` requires [Inkscape](https://inkscape.org/) to be installed and accessible on PATH.

```python
import svgsubfig.utility as util

from svgsubfig import SVGSubFigure
from pathlib import Path

pth_config = Path("figure.json")
pth_svg = pth_config.with_suffix(".svg")

fig = SVGSubFigure.from_json(pth_config)
fig.save(pth_svg)

util.convert_svg(pth_svg)
```
