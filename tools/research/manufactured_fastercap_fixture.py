"""Generate deterministic, stdlib-only FasterCap quadrilateral fixtures."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Iterable


LEVELS = (0.002, 0.001, 0.0005)
CONDUCTORS = {
    "C0": (-0.0005, 0.0),
    "C1": (0.0045, 0.0050),
    "AP2": (0.0095, 0.0100),
    "C3": (0.0145, 0.0150),
}
SIDE = 0.020
APERTURE = (-0.002, 0.002)


def _grid(start: float, stop: float, cells: int) -> list[float]:
    return [start + (stop - start) * i / cells for i in range(cells + 1)]


def _cells(a: float, b: float, h: float) -> list[tuple[float, float]]:
    n = max(1, round((b - a) / h))
    return list(zip(_grid(a, b, n), _grid(a, b, n)[1:]))


def _q(name: str, vertices: Iterable[tuple[float, float, float]]) -> str:
    coords = " ".join(f"{value:.12g}" for vertex in vertices for value in vertex)
    return f"Q {name} {coords}\n"


def _solid(name: str, z0: float, z1: float, h: float) -> list[str]:
    xs = _cells(-SIDE / 2, SIDE / 2, h)
    ys = _cells(-SIDE / 2, SIDE / 2, h)
    zs = _cells(z0, z1, h)
    lines: list[str] = []
    for x0, x1 in xs:
        for y0, y1 in ys:
            lines.append(_q(name, ((x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1))))
            lines.append(_q(name, ((x0, y0, z0), (x0, y1, z0), (x1, y1, z0), (x1, y0, z0))))
    for y0, y1 in ys:
        for z0c, z1c in zs:
            lines.append(_q(name, ((-SIDE / 2, y0, z0c), (-SIDE / 2, y0, z1c), (-SIDE / 2, y1, z1c), (-SIDE / 2, y1, z0c))))
            lines.append(_q(name, ((SIDE / 2, y0, z0c), (SIDE / 2, y1, z0c), (SIDE / 2, y1, z1c), (SIDE / 2, y0, z1c))))
    for x0, x1 in xs:
        for z0c, z1c in zs:
            lines.append(_q(name, ((x0, -SIDE / 2, z0c), (x1, -SIDE / 2, z0c), (x1, -SIDE / 2, z1c), (x0, -SIDE / 2, z1c))))
            lines.append(_q(name, ((x0, SIDE / 2, z0c), (x0, SIDE / 2, z1c), (x1, SIDE / 2, z1c), (x1, SIDE / 2, z0c))))
    return lines


def _aperture(name: str, z0: float, z1: float, h: float) -> list[str]:
    xs = _cells(-SIDE / 2, SIDE / 2, h)
    ys = _cells(-SIDE / 2, SIDE / 2, h)
    hx0, hx1 = APERTURE
    rectangles = (
        (-SIDE / 2, hx0, -SIDE / 2, SIDE / 2),
        (hx1, SIDE / 2, -SIDE / 2, SIDE / 2),
        (hx0, hx1, -SIDE / 2, hx0),
        (hx0, hx1, hx1, SIDE / 2),
    )
    lines: list[str] = []
    for rx0, rx1, ry0, ry1 in rectangles:
        for x0, x1 in _cells(rx0, rx1, h):
            for y0, y1 in _cells(ry0, ry1, h):
                lines.append(_q(name, ((x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1))))
                lines.append(_q(name, ((x0, y0, z0), (x0, y1, z0), (x1, y1, z0), (x1, y0, z0))))
    hz = _cells(z0, z1, h)
    for y0, y1 in ys:
        for z0c, z1c in hz:
            lines.append(_q(name, ((-SIDE / 2, y0, z0c), (-SIDE / 2, y0, z1c), (-SIDE / 2, y1, z1c), (-SIDE / 2, y1, z0c))))
            lines.append(_q(name, ((SIDE / 2, y0, z0c), (SIDE / 2, y1, z0c), (SIDE / 2, y1, z1c), (SIDE / 2, y0, z1c))))
    for x0, x1 in xs:
        for z0c, z1c in hz:
            lines.append(_q(name, ((x0, -SIDE / 2, z0c), (x1, -SIDE / 2, z0c), (x1, -SIDE / 2, z1c), (x0, -SIDE / 2, z1c))))
            lines.append(_q(name, ((x0, SIDE / 2, z0c), (x0, SIDE / 2, z1c), (x1, SIDE / 2, z1c), (x1, SIDE / 2, z0c))))
    for x, outward in ((hx0, "left"), (hx1, "right")):
        for y0, y1 in _cells(hx0, hx1, h):
            for z0c, z1c in hz:
                vertices = ((x, y0, z0c), (x, y0, z1c), (x, y1, z1c), (x, y1, z0c))
                if outward == "left":
                    vertices = tuple(reversed(vertices))
                lines.append(_q(name, vertices))
    for y, outward in ((hx0, "bottom"), (hx1, "top")):
        for x0, x1 in _cells(hx0, hx1, h):
            for z0c, z1c in hz:
                vertices = ((x0, y, z0c), (x1, y, z0c), (x1, y, z1c), (x0, y, z1c))
                if outward == "bottom":
                    vertices = tuple(reversed(vertices))
                lines.append(_q(name, vertices))
    return lines


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def generate_fixture(root: Path) -> dict:
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    manifest = {
        "format": "manufactured-fastercap-qui-v1",
        "epsilon_r": 1.0,
        "conductor_order": ["C0", "C1", "AP2", "C3"],
        "expected_solver_labels": ["g1_C0", "g2_C1", "g3_AP2", "g4_C3"],
        "levels": {},
    }
    expected_solid = {0.002: 240, 0.001: 880, 0.0005: 3360}
    expected_ap2 = {0.002: 240, 0.001: 864, 0.0005: 3264}
    for h in LEVELS:
        level_key = f"{h:g}"
        level_dir = root / f"h_{level_key}"
        level_dir.mkdir(exist_ok=True)
        counts: dict[str, int] = {}
        for name, (z0, z1) in CONDUCTORS.items():
            lines = _aperture(name, z0, z1, h) if name == "AP2" else _solid(name, z0, z1, h)
            path = level_dir / f"{name}.qui"
            path.write_text("".join(lines), encoding="ascii", newline="\n")
            counts[name] = len(lines)
            expected = expected_ap2[h] if name == "AP2" else expected_solid[h]
            if len(lines) != expected:
                raise AssertionError(f"{name} h={h}: {len(lines)} != {expected}")
        lst = level_dir / "fixture.lst"
        lst.write_text("".join(f"C {name}.qui 1.0 0 0 0\n" for name in CONDUCTORS), encoding="ascii", newline="\n")
        manifest["levels"][level_key] = {
            "h_m": h,
            "files": {f"{name}.qui": {"q_count": counts[name], "sha256": _sha256(level_dir / f"{name}.qui")} for name in CONDUCTORS},
            "fixture.lst": {"sha256": _sha256(lst)},
            "q_count_total": sum(counts.values()),
            "q_count_ap2": counts["AP2"],
            "q_count_solid": {name: counts[name] for name in ("C0", "C1", "C3")},
        }
    manifest_path = root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    generate_fixture(args.output)


if __name__ == "__main__":
    main()
