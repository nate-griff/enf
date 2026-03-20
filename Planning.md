# Claude Recomendation
Looking at your images, I can see the problem clearly — four time series plots per image, black background, colored lines (blue/EI, green/WECC, red/ERCOT, magenta/Quebec), y-axis fixed at 59.95–60.05 Hz, and a sliding ~55-second x-axis window. Here's a strategic plan:

## Core Technology Stack

**OpenCV (`cv2`)** is your primary workhorse. It handles image loading, color-based line isolation, and coordinate extraction. Pair it with **NumPy** for array math and **Pandas** for assembling and writing CSVs.

You won't need ML or OCR for the line data itself — this is a purely geometric/color problem, which is great news for reliability.

---

## Step-by-Step Strategy

### 1. Crop Each Sub-Plot

Each image contains four plots stacked vertically, plus the meters on the right you don't care about. You'll hardcode (or auto-detect) crop rectangles for each of the four plot areas. Since your scraping setup is consistent, the plot bounding boxes should be stable across images. OpenCV's `img[y1:y2, x1:x2]` slicing handles this trivially.

### 2. Extract the Plot Region (Axes Inner Area)

Within each cropped subplot, you need to isolate the *inner* plotting area — excluding the axis labels and tick marks. Again, because your layout is fixed, you can hardcode pixel offsets for the left edge (after the y-axis labels) and bottom edge (above the x-axis labels). This gives you a clean rectangle where pixel column = time and pixel row = frequency.

### 3. Isolate Each Line by Color

Each grid has a known line color against a black background. Use OpenCV's `cv2.inRange()` with HSV color thresholds to create a binary mask for each line:
- Blue → EI
- Green → WECC  
- Red → ERCOT
- Magenta → Quebec

For each x-column of pixels in the mask, find the y-coordinate of the line (e.g., `np.argmax()` or the centroid of nonzero pixels in that column). This gives you a raw array of `(pixel_x, pixel_y)` pairs.

### 4. Convert Pixel Coordinates to Real Values

**Y-axis** is easy and stable: you know 59.95 is the bottom pixel row and 60.05 is the top pixel row of the plot area. Linear interpolation:
```
freq = 60.05 - (pixel_y / plot_height) * 0.10
```

**X-axis (time)** is where your 38.6-second overlap trick becomes critical. Rather than trying to OCR the timestamp labels on every image, you can use the vertical grid lines as anchor points. Since you know the grid lines appear at consistent intervals and you know your capture timing, you can compute the time offset of each image from your scrape timestamp metadata (the filenames already contain UTC timestamps like `22-39-56`). Then map pixel_x linearly between known grid line positions.

### 5. Align and Stitch Across Images

This is the most interesting part. Since images overlap by ~16 seconds (55s window, 38.6s step), you can:
- Use the filename timestamps to establish absolute time for each image
- Assign absolute UTC times to each pixel column using the grid line anchors
- When stitching, the overlapping region gives you a cross-check — if the values agree, great; if not, you have a data quality flag

A simple approach: for each image, emit all data points with absolute timestamps, then deduplicate by rounding timestamps to your desired resolution (e.g., 1-second bins) when writing the final CSV, preferring the center of each image's window where the line is most reliably present.

### 6. Handle Missing Data at the Right Edge

As you noted, the rightmost portion sometimes has no line (live data lag). Your color mask will simply return no pixels in those columns — handle this with `np.nan` rather than zero, so your CSV correctly represents gaps rather than flat zeros.

---

## Recommended Package Summary

| Package | Role |
|---------|------|
| `opencv-python` | Image loading, color masking, cropping |
| `numpy` | Pixel array math, interpolation |
| `pandas` | DataFrame assembly, CSV output |
| `pathlib` / `re` | Filename parsing for timestamps |

---

## Suggested Development Sequence

Start with a **single image, single subplot** (ERCOT/red is easiest since it's the most visually distinct). Get the color mask working, extract pixel coordinates, and verify your frequency conversion is correct by spot-checking against the gauge readings on the right side. Once one subplot works reliably, the others are just parameter changes. Then tackle the stitching logic as a second phase once you have a pile of single-image CSVs working correctly.

The hardest part will likely be tuning your HSV color ranges — the magenta and red lines can bleed into similar hue ranges depending on JPEG compression. Testing with `cv2.imshow()` on the masks interactively will save you a lot of time there.
# Other Info
## Plot Details
These should be consistent throughout all of the downloaded images (theoretically). I got them with GIMP. 
**Regions** 
```
PLOT_REGIONS = {
    "EI":     {"x1": 100, "x2": 1180, "y1": 43,  "y2": 220},
    "WECC":   {"x1": 100, "x2": 1180, "y1": 342, "y2": 520},
    "ERCOT":  {"x1": 100, "x2": 1180, "y1": 642, "y2": 820},
    "Quebec": {"x1": 100, "x2": 1180, "y1": 942, "y2": 1120},
}
```
**Color Codes (RGB)**
EI: 5.1, 55.7, 87.1
WECC: 2.0, 58.5, 16.9
ERCOT: 88.6, 26.3, 11.8
Quebec: 82.0, 1.6, 79.2

## Claude recomendations
Yes, GIMP is perfect for this initial calibration step. Here's how to use it effectively:

### Using GIMP to Get Your Reference Coordinates

Open one of your images in GIMP and use the **pointer position display** (bottom of the window shows X, Y coordinates as you hover). You want to record:

#### Plot Area Boundaries
For each of the four subplots, hover to find:
- **Left edge** of the inner plot area (where the y-axis line is)
- **Right edge** (where the plot ends)
- **Top edge** (the 60.05 line)
- **Bottom edge** (the 59.95 line)

Do this for all four rows so you have eight bounding boxes hardcoded.

#### Grid Line X-Positions
Hover over each vertical grid line and note its x-pixel coordinate. You have about 6 visible grid lines per plot — these become your time anchors.

#### Color Sampling
Use GIMP's **color picker tool** and switch the mode to HSV readout. Sample the line color in each subplot and note the Hue/Saturation/Value. This tells you exactly what HSV range to feed into `cv2.inRange()` — typically you'd set a range of roughly ±10 on the hue, and leave saturation/value thresholds fairly loose.

---

### A Practical Workflow

1. Open one representative image in GIMP
2. Use **Windows → Dockable Dialogs → Pointer Information** to get a live coordinate readout
3. Record all your bounding box coordinates in a Python dict like:

```python
PLOT_REGIONS = {
    "EI":     {"x1": 82, "x2": 940, "y1": 28,  "y2": 210},
    "WECC":   {"x1": 82, "x2": 940, "y1": 248, "y2": 430},
    "ERCOT":  {"x1": 82, "x2": 940, "y1": 468, "y2": 650},
    "Quebec": {"x1": 82, "x2": 940, "y1": 688, "y2": 870},
}
```

Those numbers are just illustrative — you'd fill in your actual measurements.

---

### One Important Caveat

Since your images come from a live scraper, double-check that the **image dimensions are always identical**. If the website ever serves a slightly different resolution or your scraper crops inconsistently, your hardcoded coordinates will silently shift. A quick sanity check at the top of your processing script like:

```python
assert img.shape == (900, 1280, 3), f"Unexpected image shape: {img.shape}"
```

...will save you from mysterious bad data down the line. Check this against your existing 10+ minutes of captures now before you build on top of the assumption.