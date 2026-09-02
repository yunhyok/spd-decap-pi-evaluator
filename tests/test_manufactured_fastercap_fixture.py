import hashlib
import json
import math
from pathlib import Path

from tools.research.manufactured_fastercap_fixture import generate_fixture


def _quads(path: Path) -> list[list[tuple[float, float, float]]]:
    quads = []
    for line in path.read_text().splitlines():
        values = [float(value) for value in line.split()[2:]]
        quads.append([tuple(values[i : i + 3]) for i in range(0, 12, 3)])
    return quads


def _canonical_quad(quad: list[tuple[float, float, float]]) -> tuple:
    cycles = []
    for sequence in (quad, list(reversed(quad))):
        cycles.extend(tuple(sequence[offset:] + sequence[:offset]) for offset in range(4))
    return min(cycles)


def _triangle_volume(a: tuple[float, float, float], b: tuple[float, float, float], c: tuple[float, float, float]) -> float:
    cross = (b[1] * c[2] - b[2] * c[1], b[2] * c[0] - b[0] * c[2], b[0] * c[1] - b[1] * c[0])
    return sum(a[i] * cross[i] for i in range(3)) / 6.0


def _topology(quad_list: list[list[tuple[float, float, float]]], h: float, expected_volume: float) -> None:
    assert len({_canonical_quad(quad) for quad in quad_list}) == len(quad_list)
    edges: dict[tuple, list[int]] = {}
    volume = 0.0
    for quad in quad_list:
        assert len(set(quad)) == 4
        a, b, c, d = quad
        ab = tuple(b[i] - a[i] for i in range(3))
        ac = tuple(c[i] - a[i] for i in range(3))
        normal = (ab[1] * ac[2] - ab[2] * ac[1], ab[2] * ac[0] - ab[0] * ac[2], ab[0] * ac[1] - ab[1] * ac[0])
        assert math.sqrt(sum(value * value for value in normal)) > 0.0
        for start, end in zip(quad, quad[1:] + quad[:1]):
            assert math.dist(start, end) <= h + 1e-12
            key = tuple(sorted((start, end)))
            edges.setdefault(key, []).append(1 if start <= end else -1)
        volume += _triangle_volume(a, b, c) + _triangle_volume(a, c, d)
    assert all(len(signs) == 2 and sum(signs) == 0 for signs in edges.values())
    assert math.isclose(volume, expected_volume, rel_tol=1e-9, abs_tol=1e-15)


def test_manufactured_fixture_topology_and_manifest(tmp_path: Path) -> None:
    first = generate_fixture(tmp_path / "first")
    second = generate_fixture(tmp_path / "second")
    first_manifest = (tmp_path / "first" / "manifest.json").read_bytes()
    second_manifest = (tmp_path / "second" / "manifest.json").read_bytes()
    assert first == second and first_manifest == second_manifest
    assert first["conductor_order"] == ["C0", "C1", "AP2", "C3"]
    assert first["expected_solver_labels"] == ["g1_C0", "g2_C1", "g3_AP2", "g4_C3"]
    for level, h in (("0.002", 0.002), ("0.001", 0.001), ("0.0005", 0.0005)):
        level_dir = tmp_path / "first" / f"h_{level}"
        expected_volume = {"C0": 2.0e-7, "C1": 2.0e-7, "AP2": 1.92e-7, "C3": 2.0e-7}
        for name in first["conductor_order"]:
            path = level_dir / f"{name}.qui"
            _topology(_quads(path), h, expected_volume[name])
            recorded = first["levels"][level]["files"][f"{name}.qui"]["sha256"]
            assert hashlib.sha256(path.read_bytes()).hexdigest() == recorded
        assert (level_dir / "fixture.lst").read_text() == "C C0.qui 1.0 0 0 0\nC C1.qui 1.0 0 0 0\nC AP2.qui 1.0 0 0 0\nC C3.qui 1.0 0 0 0\n"
    assert json.loads(first_manifest) == first
