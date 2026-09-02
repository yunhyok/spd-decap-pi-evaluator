from pathlib import Path

from tools.research.manufactured_fastercap_fixture import generate_fixture


def _quads(path: Path) -> list[list[tuple[float, float, float]]]:
    quads = []
    for line in path.read_text().splitlines():
        fields = line.split()
        values = [float(value) for value in fields[2:]]
        quads.append([tuple(values[i : i + 3]) for i in range(0, 12, 3)])
    return quads


def _normal(quad: list[tuple[float, float, float]]) -> tuple[float, float, float]:
    a = tuple(quad[1][i] - quad[0][i] for i in range(3))
    b = tuple(quad[2][i] - quad[0][i] for i in range(3))
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def test_manufactured_fixture_counts_and_determinism(tmp_path: Path) -> None:
    first = generate_fixture(tmp_path / "first")
    second = generate_fixture(tmp_path / "second")
    assert [first["levels"][key]["q_count_total"] for key in ("0.002", "0.001", "0.0005")] == [960, 3504, 13344]
    assert [first["levels"][key]["q_count_ap2"] for key in ("0.002", "0.001", "0.0005")] == [240, 864, 3264]
    c0 = _quads(tmp_path / "first" / "h_0.002" / "C0.qui")
    z_values = [vertex[2] for quad in c0 for vertex in quad]
    assert min(z_values) == -0.0005 and max(z_values) == 0.0
    ap2 = _quads(tmp_path / "first" / "h_0.002" / "AP2.qui")
    inner = {
        "left": [quad for quad in ap2 if all(vertex[0] == -0.002 for vertex in quad)],
        "right": [quad for quad in ap2 if all(vertex[0] == 0.002 for vertex in quad)],
        "bottom": [quad for quad in ap2 if all(vertex[1] == -0.002 for vertex in quad)],
        "top": [quad for quad in ap2 if all(vertex[1] == 0.002 for vertex in quad)],
    }
    assert _normal(inner["left"][0])[0] > 0
    assert _normal(inner["right"][0])[0] < 0
    assert _normal(inner["bottom"][0])[1] > 0
    assert _normal(inner["top"][0])[1] < 0
    for level in ("0.002", "0.001", "0.0005"):
        for name in ("C0", "C1", "AP2", "C3"):
            left = (tmp_path / "first" / f"h_{level}" / f"{name}.qui").read_bytes()
            right = (tmp_path / "second" / f"h_{level}" / f"{name}.qui").read_bytes()
            assert left == right
        assert (tmp_path / "first" / f"h_{level}" / "fixture.lst").read_text() == "C C0.qui 1.0 0 0 0\nC C1.qui 1.0 0 0 0\nC AP2.qui 1.0 0 0 0\nC C3.qui 1.0 0 0 0\n"
