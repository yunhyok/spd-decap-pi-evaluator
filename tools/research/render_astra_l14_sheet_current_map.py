"""Static scientific figure from the saved conditional P1 sheet field."""
import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from verify_astra_l14_mesh_snapshot import RUN as MESH_RUN, PINS


def color(value):
    stops = np.asarray([[68, 1, 84], [59, 82, 139], [33, 145, 140], [94, 201, 98], [253, 231, 37]])
    position = np.clip(value, 0., 1.) * 4
    index = min(int(position), 3)
    return tuple(np.rint(stops[index] + (position - index) * (stops[index + 1] - stops[index])).astype(int))


def main():
    run = MESH_RUN.parent / "astra-l14-sheet-sensitivity-01"
    field_path = run / "sheet-sensitivity-field.npz"
    assert hashlib.sha256(field_path.read_bytes()).hexdigest() == "7a65b34e8f13173f8d1ab86e6ad8ea27fcb1adf01f638cec4ef172c6c85c7003"
    assert hashlib.sha256((MESH_RUN / "mesh-stiffness.npz").read_bytes()).hexdigest() == PINS["mesh-stiffness.npz"]
    with np.load(MESH_RUN / "mesh-stiffness.npz", allow_pickle=False) as saved:
        nodes, triangles = saved["node_xy_um"], saved["triangles"]
    with np.load(field_path, allow_pickle=False) as saved:
        density = saved["ideal_limit_sheet_current_density_a_per_m"]
        loss = float(saved["triangle_joule_ohm"].sum())
    render(nodes, triangles, density, loss, run / "l14-sheet-current-density.png")


def render(nodes, triangles, density, loss, out, *, actual=False):
    magnitude = np.sqrt(np.sum(np.abs(density)**2, axis=1))
    assert np.isfinite(magnitude).all() and len(magnitude) == len(triangles)
    normalized = np.clip((np.log10(np.maximum(magnitude, .1)) + 1) / 4, 0, 1)
    palette = [color(value / 255) for value in range(256)]
    width, height = 1600, 1080
    picture = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(picture)
    font = lambda size: ImageFont.truetype("C:/Windows/Fonts/segoeui.ttf", size)
    x0, y0 = nodes.min(axis=0)
    x1, y1 = nodes.max(axis=0)
    left, top, right, bottom = 115, 165, 1295, 870
    scale = min((right-left)/(x1-x0), (bottom-top)/(y1-y0))
    ox = left + ((right-left)-scale*(x1-x0))/2
    oy = top + ((bottom-top)-scale*(y1-y0))/2
    pixels = np.column_stack((ox + scale*(nodes[:, 0]-x0), oy + scale*(y1-nodes[:, 1])))
    for triangle, value in zip(triangles, normalized, strict=True):
        draw.polygon([tuple(point) for point in pixels[triangle]], fill=palette[int(round(value*255))])
    draw.text((75, 25), "L14 distributed sheet current | 1 MHz, 1 A Device drive", fill="#17232f", font=font(33))
    draw.text((75, 75), "SPD Decap PI Evaluator v0.23.1  /  ADC_VDD_075_VTRIP_SRAM/0", fill="#42505c", font=font(24))
    subtitle = "Actual source DC sheet resistance: epsilon = 1, global current redistribution" if actual else "Conditional ideal-limit field: resistance scale epsilon approaches zero"
    draw.text((75, 114), subtitle, fill="#42505c", font=font(23))
    for fraction in (0, .25, .5, .75, 1):
        x, y = x0+fraction*(x1-x0), y0+fraction*(y1-y0)
        px, py = ox+scale*(x-x0), oy+scale*(y1-y)
        draw.text((px, bottom+12), f"{x/1000:.1f}", fill="#42505c", font=font(21), anchor="mt")
        draw.text((ox-16, py), f"{y/1000:.1f}", fill="#42505c", font=font(21), anchor="rm")
    draw.text(((left+right)/2, bottom+48), "X (mm)", fill="#17232f", font=font(24), anchor="mt")
    label = Image.new("RGBA", (100, 35))
    ImageDraw.Draw(label).text((0, 0), "Y (mm)", fill="#17232f", font=font(22))
    label = label.rotate(90, expand=True)
    picture.paste(label, (int(ox)-100, int((top+bottom)/2)-50), label)
    lx, ly, lh = 1370, 265, 420
    draw.text((1340, 185), "|J sheet|", fill="#17232f", font=font(27))
    draw.text((1340, 224), "A/m (log scale)", fill="#42505c", font=font(22))
    for offset in range(lh):
        draw.line((lx, ly+offset, lx+30, ly+offset), fill=color(1-offset/(lh-1)))
    for exponent in (-1, 0, 1, 2, 3):
        py = ly+lh*(3-exponent)/4
        value = "<= 0.1" if exponent == -1 else f"{10**exponent:g}"
        draw.text((lx+45, py), value, fill="#17232f", font=font(22), anchor="lm")
    draw.text((1335, 730), f"Maximum\n{magnitude.max():.1f} A/m", fill="#17232f", font=font(24))
    loss_label = "Sheet loss at the solved current" if actual else "Conditional Joule coefficient"
    draw.text((75, 961), f"{loss_label}: {loss*1000:.6f} mOhm. Source via and spatial G/C couplings are preserved.", fill="#17232f", font=font(23))
    caveat = "Fixed mesh and assumed filled-core electrodes; other layers remain native. No full AC or accuracy certification." if actual else "Fixed mesh and assumed filled-core electrodes. This is not a finite-resistance Device response or an accuracy result."
    draw.text((75, 1006), caveat, fill="#42505c", font=font(22))
    if out.exists():
        raise FileExistsError(out)
    picture.save(out)
    print(json.dumps({"path": str(out), "maximum_current_density_a_per_m": float(magnitude.max()),
                      "joule_coefficient_ohm": loss, "bytes": out.stat().st_size}))


if __name__ == "__main__":
    assert color(0) == (68, 1, 84) and color(1) == (253, 231, 37)
    main()
