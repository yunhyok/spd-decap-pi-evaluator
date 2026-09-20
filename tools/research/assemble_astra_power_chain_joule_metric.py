"""SPD Decap PI Evaluator v0.23.1: single-owned 933-bridge Joule metric.

The five accepted local interface-lift coordinates are translated onto every
actual selected-power bridge.  A physical post shared by two adjacent bridges
is evaluated once with the sum of both incident fields, so its cross term is
retained.  This helper assembles only a sparse static Joule metric; it does not
set a physical exterior boundary condition or solve a field problem.
"""

from __future__ import annotations

import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
from time import monotonic
import traceback

import numpy as np


ROOT = Path(__file__).resolve().parents[2]
PINS = {
    "outputs/research/astra-device-power-top-bridges-02/result.json":
        "150cef6607c156e1be6d4c1b97a63d48cdabadb43ed3d7ae25cae430615ffb0c",
    "outputs/research/astra-device-power-top-bridges-02/bridge-assembly.json":
        "583c9d0da72b82bf3d8304cd9ad9b98a316dafcbdb79caa1a13c8e9f69073790",
    "outputs/research/astra-boundary-conforming-power-joint-01/joint-template.npz":
        "14b927da78b3cbf9d68f49c89b84dbeda746a99cef0bc6d9bcefd597f9db49eb",
    "outputs/research/astra-boundary-joint-current-space-01/current-space.npz":
        "2e356ee882623509cec9d62879283092a230c652ea4a74576076a695d39483f5",
    "outputs/research/astra-shared-interface-interior-lifts-04/result.json":
        "2df58fa459a0e016916b8c6f3e23c8c44cedd58d191b69111a2ad6688e131eb8",
    "outputs/research/astra-shared-interface-interior-lifts-04/interior-lifts.npz":
        "9bdd7de919d425e3da295787810d11aea278b696b527314a7bf46b2fdc526e92",
}


def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def normalized_error(actual: np.ndarray, expected: np.ndarray) -> float:
    scale = max(float(np.linalg.norm(expected)), np.finfo(float).tiny)
    return float(np.linalg.norm(actual - expected) / scale)


def centered_rt0_fields(
    vertices_m: np.ndarray,
    cells: np.ndarray,
    local_columns: np.ndarray,
    local_signs: np.ndarray,
    face_flux_basis: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Return center current, radial coefficient, volume and radial variance.

    For unit integrated local-face flux, f_i=(r-v_i)/(3V).  The centered
    representation J(r)=J(c)+a(r-c) makes the physical Joule integral exact.
    """

    tetrahedra = vertices_m[cells]
    centers = tetrahedra.mean(axis=1)
    volumes = np.abs(
        np.linalg.det(tetrahedra[:, 1:] - tetrahedra[:, :1])
    ) / 6.0
    assert np.all(volumes > 0.0)
    variance = np.sum(
        (tetrahedra - centers[:, None, :]) ** 2, axis=(1, 2)
    ) / 20.0
    signed_flux = local_signs[:, :, None] * face_flux_basis[local_columns]
    center_current = np.einsum(
        "cim,cid->cmd",
        signed_flux,
        centers[:, None, :] - tetrahedra,
    ) / (3.0 * volumes[:, None, None])
    radial = signed_flux.sum(axis=1) / (3.0 * volumes[:, None])
    return center_current, radial, volumes, variance


def body_gram(
    center_a: np.ndarray,
    radial_a: np.ndarray,
    center_b: np.ndarray,
    radial_b: np.ndarray,
    volumes: np.ndarray,
    variance: np.ndarray,
    conductivity: float,
) -> np.ndarray:
    return (
        np.einsum(
            "c,cid,cjd->ij",
            volumes / conductivity,
            center_a,
            center_b,
        )
        + np.einsum(
            "c,ci,cj->ij",
            volumes * variance / conductivity,
            radial_a,
            radial_b,
        )
    )


def explicit_body_energy(
    coefficients: np.ndarray,
    left_edge_for_post: np.ndarray,
    right_edge_for_post: np.ndarray,
    center_left: np.ndarray,
    radial_left: np.ndarray,
    center_right: np.ndarray,
    radial_right: np.ndarray,
    center_bridge: np.ndarray,
    radial_bridge: np.ndarray,
    post_volumes: np.ndarray,
    post_variance: np.ndarray,
    bridge_volumes: np.ndarray,
    bridge_variance: np.ndarray,
    conductivity: float,
) -> float:
    """Evaluate each of 978 posts and 933 bridges exactly once."""

    energy = 0.0
    for post in range(len(left_edge_for_post)):
        center = np.zeros((len(post_volumes), 3), dtype=np.complex128)
        radial = np.zeros(len(post_volumes), dtype=np.complex128)
        outgoing = int(left_edge_for_post[post])
        incoming = int(right_edge_for_post[post])
        if outgoing >= 0:
            center += np.einsum("cmd,m->cd", center_left, coefficients[outgoing])
            radial += radial_left @ coefficients[outgoing]
        if incoming >= 0:
            center += np.einsum("cmd,m->cd", center_right, coefficients[incoming])
            radial += radial_right @ coefficients[incoming]
        energy += float(
            np.sum(
                post_volumes
                / conductivity
                * (
                    np.sum(np.abs(center) ** 2, axis=1)
                    + post_variance * np.abs(radial) ** 2
                )
            )
        )
    for edge_coefficients in coefficients:
        center = np.einsum("cmd,m->cd", center_bridge, edge_coefficients)
        radial = radial_bridge @ edge_coefficients
        energy += float(
            np.sum(
                bridge_volumes
                / conductivity
                * (
                    np.sum(np.abs(center) ** 2, axis=1)
                    + bridge_variance * np.abs(radial) ** 2
                )
            )
        )
    return energy


def connected_components(adjacency: list[list[int]]) -> list[list[int]]:
    seen: set[int] = set()
    components: list[list[int]] = []
    for start in range(len(adjacency)):
        if start in seen:
            continue
        stack = [start]
        seen.add(start)
        component: list[int] = []
        while stack:
            node = stack.pop()
            component.append(node)
            for neighbor in adjacency[node]:
                if neighbor not in seen:
                    seen.add(neighbor)
                    stack.append(neighbor)
        components.append(component)
    return components


def run(output: Path) -> None:
    start = monotonic()
    if output.exists():
        raise FileExistsError(output)
    output.mkdir(parents=True)
    (output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    try:
        for relative, expected in PINS.items():
            assert digest(ROOT / relative) == expected, relative

        from scipy.sparse import coo_matrix, csr_matrix

        bridge_ledger = json.loads(
            (ROOT / "outputs/research/astra-device-power-top-bridges-02/bridge-assembly.json")
            .read_text(encoding="utf-8")
        )
        instances = bridge_ledger["instances"]
        assert len(instances) == 933

        with np.load(
            ROOT / "outputs/research/astra-boundary-conforming-power-joint-01/joint-template.npz",
            allow_pickle=False,
        ) as mesh:
            vertices_m = mesh["vertices_local_um"] * 1.0e-6
            cells = mesh["cells"]
            cell_body = mesh["cell_body"]
        with np.load(
            ROOT / "outputs/research/astra-boundary-joint-current-space-01/current-space.npz",
            allow_pickle=False,
        ) as current:
            local_columns = current["local_rt0_face_columns"]
            local_signs = current["local_rt0_face_signs"]
            conductivity = float(current["conductivity_s_m"][0])
            resistance = csr_matrix(
                (
                    current["resistance_data_ohm"],
                    current["mass_col"],
                    current["mass_row_ptr"],
                ),
                shape=tuple(current["mass_shape"]),
            )
        with np.load(
            ROOT / "outputs/research/astra-shared-interface-interior-lifts-04/interior-lifts.npz",
            allow_pickle=False,
        ) as lifts:
            face_flux_basis = lifts["face_flux_basis"]
            saved_joint_gram = lifts["energy_gram_ohm"]

        center_current, radial, volumes, variance = centered_rt0_fields(
            vertices_m, cells, local_columns, local_signs, face_flux_basis
        )
        body_cells = [np.flatnonzero(cell_body == body) for body in range(3)]
        assert [len(part) for part in body_cells] == [2604, 2604, 96]
        left_cells, right_cells, bridge_cells = body_cells
        assert np.array_equal(cells[right_cells] - cells[left_cells], np.full((2604, 4), 1172))
        assert (
            np.max(
                np.abs(
                    vertices_m[cells[right_cells]]
                    - vertices_m[cells[left_cells]]
                    - np.array([130.0e-6, 0.0, 0.0])
                )
            )
            < 1.0e-18
        )

        center_left = center_current[left_cells]
        center_right = center_current[right_cells]
        center_bridge = center_current[bridge_cells]
        radial_left = radial[left_cells]
        radial_right = radial[right_cells]
        radial_bridge = radial[bridge_cells]
        post_volumes = volumes[left_cells]
        post_variance = variance[left_cells]
        bridge_volumes = volumes[bridge_cells]
        bridge_variance = variance[bridge_cells]

        left_gram = body_gram(
            center_left,
            radial_left,
            center_left,
            radial_left,
            post_volumes,
            post_variance,
            conductivity,
        )
        right_gram = body_gram(
            center_right,
            radial_right,
            center_right,
            radial_right,
            post_volumes,
            post_variance,
            conductivity,
        )
        bridge_gram = body_gram(
            center_bridge,
            radial_bridge,
            center_bridge,
            radial_bridge,
            bridge_volumes,
            bridge_variance,
            conductivity,
        )
        shared_cross = body_gram(
            center_right,
            radial_right,
            center_left,
            radial_left,
            post_volumes,
            post_variance,
            conductivity,
        )
        joint_gram = left_gram + right_gram + bridge_gram
        sparse_joint_gram = face_flux_basis.T @ (resistance @ face_flux_basis)
        joint_saved_relative = normalized_error(joint_gram, saved_joint_gram)
        joint_sparse_relative = normalized_error(joint_gram, sparse_joint_gram)

        # Recover actual post coordinates and directed x-increasing graph.
        pin_xy: dict[str, tuple[float, float]] = {}
        edge_left_pin: list[str] = []
        edge_right_pin: list[str] = []
        translations = np.empty((len(instances), 2), dtype=float)
        trace_ids: list[str] = []
        for edge, record in enumerate(instances):
            left = record["left_pin"]
            right = record["right_pin"]
            x, y = map(float, record["translation_xy_um"])
            assert left != right
            for pin, point in ((left, (x, y)), (right, (x + 130.0, y))):
                if pin in pin_xy:
                    assert np.max(np.abs(np.asarray(pin_xy[pin]) - point)) < 1.0e-9
                else:
                    pin_xy[pin] = point
            edge_left_pin.append(left)
            edge_right_pin.append(right)
            translations[edge] = [x, y]
            trace_ids.append(record["trace_id"])
        assert len(pin_xy) == 978
        post_pins = sorted(pin_xy, key=lambda pin: (pin_xy[pin][1], pin_xy[pin][0], pin))
        post_index = {pin: index for index, pin in enumerate(post_pins)}
        left_post = np.asarray([post_index[pin] for pin in edge_left_pin], dtype=np.int64)
        right_post = np.asarray([post_index[pin] for pin in edge_right_pin], dtype=np.int64)
        adjacency: list[list[int]] = [[] for _ in post_pins]
        left_edge_for_post = np.full(len(post_pins), -1, dtype=np.int64)
        right_edge_for_post = np.full(len(post_pins), -1, dtype=np.int64)
        for edge, (left, right) in enumerate(zip(left_post, right_post, strict=True)):
            assert left_edge_for_post[left] < 0
            assert right_edge_for_post[right] < 0
            left_edge_for_post[left] = edge
            right_edge_for_post[right] = edge
            adjacency[left].append(int(right))
            adjacency[right].append(int(left))
        degree = np.asarray([len(neighbors) for neighbors in adjacency], dtype=np.int64)
        assert Counter(degree.tolist()) == Counter({1: 90, 2: 888})
        components = connected_components(adjacency)
        assert len(components) == 45
        assert Counter(map(len, components)) == Counter({4: 6, 8: 21, 12: 12, 107: 6})

        component_id_by_post = np.full(len(post_pins), -1, dtype=np.int64)
        component_edge_lists: list[list[int]] = []
        edge_component = np.full(len(instances), -1, dtype=np.int64)
        edge_position = np.full(len(instances), -1, dtype=np.int64)
        adjacent_edge_pairs: list[tuple[int, int]] = []
        for component_id, component in enumerate(components):
            ordered_posts = sorted(component, key=lambda post: pin_xy[post_pins[post]][0])
            ys = np.asarray([pin_xy[post_pins[post]][1] for post in ordered_posts])
            xs = np.asarray([pin_xy[post_pins[post]][0] for post in ordered_posts])
            assert float(np.ptp(ys)) < 1.0e-9
            assert np.max(np.abs(np.diff(xs) - 130.0)) < 1.0e-9
            component_id_by_post[ordered_posts] = component_id
            ordered_edges: list[int] = []
            for position, (a, b) in enumerate(zip(ordered_posts[:-1], ordered_posts[1:], strict=True)):
                edge = int(left_edge_for_post[a])
                assert edge >= 0 and int(right_post[edge]) == b
                ordered_edges.append(edge)
                edge_component[edge] = component_id
                edge_position[edge] = position
                if position:
                    adjacent_edge_pairs.append((ordered_edges[-2], edge))
            component_edge_lists.append(ordered_edges)
        assert np.all(edge_component >= 0) and np.all(edge_position >= 0)
        assert len(adjacent_edge_pairs) == 888

        # Sparse 5x5 block tridiagonal metric in input-instance order.
        row: list[int] = []
        col: list[int] = []
        data: list[float] = []

        def add_block(edge_a: int, edge_b: int, block: np.ndarray) -> None:
            base_a = 5 * edge_a
            base_b = 5 * edge_b
            for i in range(5):
                for j in range(5):
                    row.append(base_a + i)
                    col.append(base_b + j)
                    data.append(float(block[i, j]))

        for edge in range(len(instances)):
            add_block(edge, edge, joint_gram)
        for incoming, outgoing in adjacent_edge_pairs:
            add_block(incoming, outgoing, shared_cross)
            add_block(outgoing, incoming, shared_cross.T)
        coordinate_count = 5 * len(instances)
        metric = coo_matrix((data, (row, col)), shape=(coordinate_count, coordinate_count)).tocsr()
        metric.sum_duplicates()
        metric.eliminate_zeros()
        symmetry_relative = normalized_error(metric.toarray() if False else metric.data, metric.data)
        difference = metric - metric.T
        sparse_symmetry = (
            float(np.max(np.abs(difference.data))) if difference.nnz else 0.0
        )

        # A unique positive bridge body for every edge is a direct SPD certificate.
        bridge_scale = np.sqrt(np.diag(bridge_gram))
        normalized_bridge = bridge_gram / bridge_scale[:, None] / bridge_scale[None, :]
        bridge_eigenvalues = np.linalg.eigvalsh(normalized_bridge)
        bridge_cholesky = np.linalg.cholesky(bridge_gram)
        assert bridge_eigenvalues[0] > 0.0

        # Each disconnected chain is small enough for a direct normalized SPD check.
        component_min_eigenvalue: list[float] = []
        component_cholesky_min_diagonal: list[float] = []
        for ordered_edges in component_edge_lists:
            coordinates = np.concatenate(
                [np.arange(5 * edge, 5 * edge + 5, dtype=np.int64) for edge in ordered_edges]
            )
            block = metric[coordinates][:, coordinates].toarray()
            scale = np.sqrt(np.diag(block))
            normalized = block / scale[:, None] / scale[None, :]
            component_min_eigenvalue.append(float(np.linalg.eigvalsh(normalized)[0]))
            component_cholesky_min_diagonal.append(
                float(np.min(np.diag(np.linalg.cholesky(block))))
            )

        # Independent bodywise checks use affine current fields, not the block formula.
        rng = np.random.default_rng(20260909)
        random_coefficients = rng.standard_normal((3, len(instances), 5)) + 1j * rng.standard_normal(
            (3, len(instances), 5)
        )
        assembled_energy: list[float] = []
        explicit_energy: list[float] = []
        for coefficients in random_coefficients:
            flat = coefficients.reshape(-1)
            assembled_energy.append(float(np.real(np.vdot(flat, metric @ flat))))
            explicit_energy.append(
                explicit_body_energy(
                    coefficients,
                    left_edge_for_post,
                    right_edge_for_post,
                    center_left,
                    radial_left,
                    center_right,
                    radial_right,
                    center_bridge,
                    radial_bridge,
                    post_volumes,
                    post_variance,
                    bridge_volumes,
                    bridge_variance,
                    conductivity,
                )
            )
        assembled_energy_array = np.asarray(assembled_energy)
        explicit_energy_array = np.asarray(explicit_energy)
        random_energy_relative = normalized_error(
            assembled_energy_array, explicit_energy_array
        )

        # Actual instance translations must map every shared post identically.
        shared_translation_error = 0.0
        for incoming, outgoing in adjacent_edge_pairs:
            shared_translation_error = max(
                shared_translation_error,
                float(
                    np.max(
                        np.abs(
                            translations[incoming]
                            + np.array([130.0, 0.0])
                            - translations[outgoing]
                        )
                    )
                ),
            )

        checks = {
            "post_count": len(post_pins),
            "bridge_count": len(instances),
            "component_count": len(components),
            "degree_one_posts": int(np.count_nonzero(degree == 1)),
            "degree_two_posts": int(np.count_nonzero(degree == 2)),
            "adjacent_shared_post_pairs": len(adjacent_edge_pairs),
            "coordinate_count": coordinate_count,
            "metric_nnz": int(metric.nnz),
            "joint_gram_saved_relative": joint_saved_relative,
            "joint_gram_sparse_mass_relative": joint_sparse_relative,
            "global_sparse_symmetry_absolute_ohm": sparse_symmetry,
            "shared_post_translation_error_um": shared_translation_error,
            "bridge_normalized_min_eigenvalue": float(bridge_eigenvalues[0]),
            "bridge_cholesky_min_diagonal_sqrt_ohm": float(
                np.min(np.diag(bridge_cholesky))
            ),
            "global_component_normalized_min_eigenvalue": float(
                np.min(component_min_eigenvalue)
            ),
            "global_component_cholesky_min_diagonal_sqrt_ohm": float(
                np.min(component_cholesky_min_diagonal)
            ),
            "random_complex_body_energy_relative": random_energy_relative,
            "cross_frobenius_relative_to_joint": float(
                np.linalg.norm(shared_cross) / np.linalg.norm(joint_gram)
            ),
        }
        assert checks["joint_gram_saved_relative"] < 3.0e-13
        assert checks["joint_gram_sparse_mass_relative"] < 3.0e-13
        assert checks["global_sparse_symmetry_absolute_ohm"] < 1.0e-16
        assert checks["shared_post_translation_error_um"] < 1.0e-9
        assert checks["global_component_normalized_min_eigenvalue"] > 0.0
        assert checks["random_complex_body_energy_relative"] < 3.0e-13

        artifact = output / "power-chain-joule-metric.npz"
        mode_names = np.asarray(
            ["constant_trace"] + [f"zero_net_trace_{index}" for index in range(1, 5)]
        )
        np.savez_compressed(
            artifact,
            resistance_shape=np.asarray(metric.shape, dtype=np.int64),
            resistance_row_ptr=metric.indptr,
            resistance_col=metric.indices,
            resistance_data_ohm=metric.data,
            trace_ids=np.asarray(trace_ids),
            edge_left_pin=np.asarray(edge_left_pin),
            edge_right_pin=np.asarray(edge_right_pin),
            edge_translation_xy_um=translations,
            edge_left_post=left_post,
            edge_right_post=right_post,
            edge_component=edge_component,
            edge_position=edge_position,
            post_pins=np.asarray(post_pins),
            post_xy_um=np.asarray([pin_xy[pin] for pin in post_pins]),
            post_degree=degree,
            post_left_outgoing_edge=left_edge_for_post,
            post_right_incoming_edge=right_edge_for_post,
            adjacent_edge_pairs=np.asarray(adjacent_edge_pairs, dtype=np.int64),
            mode_names=mode_names,
            local_joint_gram_ohm=joint_gram,
            local_left_post_gram_ohm=left_gram,
            local_right_post_gram_ohm=right_gram,
            local_bridge_gram_ohm=bridge_gram,
            shared_post_right_left_cross_ohm=shared_cross,
            bridge_cholesky=bridge_cholesky,
            bridge_normalized_eigenvalues=bridge_eigenvalues,
            component_normalized_min_eigenvalue=np.asarray(component_min_eigenvalue),
            component_cholesky_min_diagonal_sqrt_ohm=np.asarray(
                component_cholesky_min_diagonal
            ),
            random_complex_coefficients=random_coefficients,
            random_assembled_energy_ohm=assembled_energy_array,
            random_explicit_single_owned_energy_ohm=explicit_energy_array,
        )
        result = {
            "program": "SPD Decap PI Evaluator",
            "version": "0.23.1",
            "status": "PASS_SELECTED_POWER_CHAIN_SINGLE_OWNED_JOULE_METRIC",
            "elapsed_s": monotonic() - start,
            "driver_sha256": digest(Path(__file__)),
            "pins": PINS,
            "artifact_sha256": digest(artifact),
            "checks": checks,
            "component_post_count_histogram": dict(
                sorted(Counter(map(len, components)).items())
            ),
            "scope": (
                "Static Joule metric for 4,665 coordinates made from five accepted local "
                "interface-lift columns on each of 933 actual selected-power bridges. Each "
                "physical post and bridge volume is counted once and all 888 adjacent-post "
                "cross blocks are retained. The constant local trace coordinate still uses "
                "the declared whole-top-pad balancing fixture. Complementary fine current, "
                "exterior/contact charge, lower/vertical continuation and circulation spaces "
                "remain available and are not declared zero. This is not a Green operator, "
                "field solve, physical port, full-rail model, impedance, board response or "
                "PowerSI accuracy certificate."
            ),
        }
        (output / "result.json").write_text(
            json.dumps(result, indent=2, allow_nan=False), encoding="utf-8"
        )
        print(json.dumps({"status": result["status"], "checks": checks}))
    except Exception:
        (output / "failure.json").write_text(
            json.dumps(
                {"status": "STOP_SELECTED_POWER_CHAIN_JOULE_METRIC", "traceback": traceback.format_exc()},
                indent=2,
            ),
            encoding="utf-8",
        )
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    run(arguments.output.resolve())
