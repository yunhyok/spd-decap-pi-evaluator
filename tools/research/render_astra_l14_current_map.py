"""Render source artwork and saved native via currents as a static research map."""
from collections import defaultdict
import hashlib
import json
from math import sqrt
from pathlib import Path
from zipfile import ZipFile

from PIL import Image, ImageDraw, ImageFont

from qualify_astra_l14_trace_centerlines import DIRECTORY, GEOMETRY_SHA, services, source
from recover_astra_field_run02 import RUN


def main():
    ledger_path = RUN / "l14-island-external-current-ledger.json"
    census_path = DIRECTORY / "l14-endpoint-polygon-census.json"
    assert hashlib.sha256(ledger_path.read_bytes()).hexdigest() == "8cf11e747532b87f2955fbfb65ec65fab12fd18bafa1da08e9b3ca47ee83bc5b"
    assert hashlib.sha256(census_path.read_bytes()).hexdigest() == "82dcc35354101389509e87a8ef9eb050b0b47af50cb0d1a0156c26d1bd73a152"
    ledger, census = json.loads(ledger_path.read_text()), json.loads(census_path.read_text())
    native = {r["link_id"]: complex(*r["into_l14_a"]) for r in ledger["finite_boundary"]}
    points = defaultdict(complex)
    for row in census["boundary"]["endpoints"]:
        points[tuple(row["l14_center_um"])] += native[row["native_link"]["link_id"]]
    assert len(points) == 1660
    with ZipFile(source.DEFAULT_BUNDLE) as archive:
        compressed = archive.read("attachments/geometry/0090-eb5c10758ed3e08d.spdgeom.zlib")
    assert hashlib.sha256(compressed).hexdigest() == GEOMETRY_SHA
    geometry = services._ordered_spd_geometry(services._decode_spd_geometry_asset(GEOMETRY_SHA, compressed))
    islands = services._spd_surface_islands(layer=source.PWR_LAYER, net=source.RAIL, asset_sha256=GEOMETRY_SHA, shape=geometry)
    width, height = 1600, 1080
    picture = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(picture)
    font_path = Path("C:/Windows/Fonts/segoeui.ttf")
    assert font_path.is_file()
    font = lambda size: ImageFont.truetype(str(font_path), size)
    x0, y0, x1, y1 = geometry.bounds
    left, top, right, bottom = 105, 140, 1540, 875
    scale = min((right - left) / (x1 - x0), (bottom - top) / (y1 - y0))
    ox = left + ((right - left) - scale * (x1 - x0)) / 2
    oy = top + ((bottom - top) - scale * (y1 - y0)) / 2
    def xy(x, y):
        return ox + scale * (x - x0), oy + scale * (y1 - y)
    draw.text((80, 28), "L14 via current map | 1 MHz, 1 A Device drive", fill="#17232f", font=font(34))
    draw.text((80, 78), "SPD Decap PI Evaluator v0.23.1  /  ADC_VDD_075_VTRIP_SRAM/0", fill="#42505c", font=font(24))
    for _, polygon in islands:
        # Display-only 1 um simplification; all electrical work used original geometry.
        display = polygon.simplify(1.0, preserve_topology=True)
        draw.polygon([xy(x, y) for x, y in display.exterior.coords], fill="#e2e8ee", outline="#bbc6d0")
        for ring in display.interiors:
            draw.polygon([xy(x, y) for x, y in ring.coords], fill="white", outline="#d4dce3")
    largest = max(abs(i) for i in points.values())
    for (x, y), current in sorted(points.items(), key=lambda item: abs(item[1])):
        px, py = xy(x, y)
        radius = 2 + 10 * sqrt(abs(current) / largest)
        if abs(current) < 1e-6:
            draw.ellipse((px - 2, py - 2, px + 2, py + 2), fill="#939fa8")
        elif current.real >= 0:
            draw.ellipse((px - radius, py - radius, px + radius, py + radius), fill="#1375c4", outline="white", width=1)
        else:
            draw.polygon([(px, py - radius), (px + radius, py + radius), (px - radius, py + radius)], fill="#cb542c", outline="white", width=1)
    for fraction in (0, .25, .5, .75, 1):
        x, y = x0 + fraction * (x1 - x0), y0 + fraction * (y1 - y0)
        px, _ = xy(x, y0)
        _, py = xy(x0, y)
        draw.text((px, bottom + 12), f"{x / 1000:.1f}", fill="#42505c", font=font(21), anchor="mt")
        draw.text((ox - 16, py), f"{y / 1000:.1f}", fill="#42505c", font=font(21), anchor="rm")
    draw.text(((left + right) / 2, bottom + 47), "X (mm)", fill="#17232f", font=font(24), anchor="mt")
    y_label = Image.new("RGBA", (100, 35))
    ImageDraw.Draw(y_label).text((0, 0), "Y (mm)", fill="#17232f", font=font(22))
    y_label = y_label.rotate(90, expand=True)
    picture.paste(y_label, (int(ox) - 95, int((top + bottom) / 2) - 50), y_label)
    draw.ellipse((100, 960, 118, 978), fill="#1375c4")
    draw.text((132, 952), "Into L14 (+Re I)", fill="#17232f", font=font(24))
    draw.polygon([(462, 958), (474, 978), (450, 978)], fill="#cb542c")
    draw.text((486, 952), "Out of L14 (-Re I)", fill="#17232f", font=font(24))
    draw.text((850, 952), f"Marker size: |I|, maximum {largest * 1000:.3f} mA", fill="#17232f", font=font(24))
    draw.text((80, 1008), "2,110 via branches / 1,660 centers. Gray: |I| < 1 uA. Source artwork shown; ideal trace currents are not inferred.", fill="#42505c", font=font(23))
    output = RUN / "l14-native-via-current-map-02.png"
    if output.exists():
        raise FileExistsError(output)
    picture.save(output)
    print(json.dumps({"path": str(output), "point_count": len(points), "maximum_current_a": largest, "bytes": output.stat().st_size}))


if __name__ == "__main__":
    main()
