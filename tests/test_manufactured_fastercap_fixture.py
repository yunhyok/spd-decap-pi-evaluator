from pathlib import Path

from tools.research.manufactured_fastercap_fixture import generate_fixture


def test_manufactured_fixture_counts_and_determinism(tmp_path: Path) -> None:
    first = generate_fixture(tmp_path / "first")
    second = generate_fixture(tmp_path / "second")
    assert [first["levels"][key]["q_count_total"] for key in ("0.002", "0.001", "0.0005")] == [960, 3504, 13344]
    assert [first["levels"][key]["q_count_ap2"] for key in ("0.002", "0.001", "0.0005")] == [240, 864, 3264]
    for level in ("0.002", "0.001", "0.0005"):
        for name in ("C0", "C1", "AP2", "C3"):
            left = (tmp_path / "first" / f"h_{level}" / f"{name}.qui").read_bytes()
            right = (tmp_path / "second" / f"h_{level}" / f"{name}.qui").read_bytes()
            assert left == right
        assert (tmp_path / "first" / f"h_{level}" / "fixture.lst").read_text() == "C C0.qui 1.0 0 0 0\nC C1.qui 1.0 0 0 0\nC AP2.qui 1.0 0 0 0\nC C3.qui 1.0 0 0 0\n"
