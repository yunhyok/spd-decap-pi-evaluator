"""SPD Decap PI Evaluator v0.23.1: PWR/G R-D-C-H-contact component.

Combine the retained L02 sheet and selected G volume with the complete actual
PWR TOP component.  Retained L02 contact charge rows exchange current with the
existing native circuit through explicit S_q/S_n restrictions.  L and P are
deliberately absent.
"""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
from time import monotonic

import numpy as np
from scipy import sparse
from scipy.sparse.linalg import LinearOperator

import assemble_astra_g_window_sheet_volume_coupling as g_component


PROGRAM = "SPD Decap PI Evaluator"
VERSION = "0.23.1"
ROOT = Path(__file__).resolve().parents[2]
RESEARCH = ROOT / "outputs/research"
OUTPUT = RESEARCH / "astra-pwr-g-contact-exchange-component-20260912"
PINS = {
    ROOT / "tools/research/assemble_astra_g_window_sheet_volume_coupling.py":
        "0af4c2e85a368c2ddb7b2f4d3117ec250f8842c20eb227c4eaaa9940b51b68ef",
    RESEARCH / "astra-g-window-sheet-volume-coupling-20260912/sheet-volume-coupling.npz":
        "72ee8291d74c2bd3abaf1b32b9d59e5db9f65cbdc5431957ebdd45289ef22cdf",
    RESEARCH / "astra-pwr-top-component-closure-20260912-02/pwr-top-component-closure.npz":
        "a5bbd79e55b2ff9d25a09c21bbc368b424b4d37f4f7c6fa3c0be97f87a055d7b",
    RESEARCH / "astra-pwr-g-native-frequency-boundary-20260912/pwr-g-native-frequency-boundary.npz":
        "f456db948e8f1a77788808054870bb8346debfe4f5b9cc49b6570697de5d6cfa",
    RESEARCH / "astra-l02-retained-thickness-charge-support-20260912-05/retained-thickness-charge-support.npz":
        "9a220f746759895feb35e86fda5bf21629dae5ac61fd239a41fb578f8f97ee1c",
}


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return sha256(stream.read()).hexdigest()


def unpack_csr(archive, prefix: str) -> sparse.csr_matrix:
    return sparse.csr_matrix(
        (archive[prefix + "_data"], archive[prefix + "_col"], archive[prefix + "_row_ptr"]),
        shape=tuple(archive[prefix + "_shape"]),
    )


def pack_csr(payload: dict[str, np.ndarray], prefix: str, matrix: sparse.spmatrix) -> None:
    matrix = matrix.tocsr()
    payload[prefix + "_shape"] = np.asarray(matrix.shape, dtype=np.int64)
    payload[prefix + "_row_ptr"] = matrix.indptr
    payload[prefix + "_col"] = matrix.indices
    payload[prefix + "_data"] = matrix.data


def assemble():
    for path, expected in PINS.items():
        assert digest(path) == expected, path
    assert Path(g_component.__file__).resolve() == next(iter(PINS)).resolve()

    g_resistance, g_divergence, g_constraint, _, g_payload = g_component.assemble()
    g_artifact_path, pwr_path, native_path, support_path = list(PINS)[1:]
    with np.load(g_artifact_path, allow_pickle=False) as archive:
        assert np.array_equal(g_payload["retained_sheet_charge_row_ids"], archive["retained_sheet_charge_row_ids"])
        assert np.array_equal(g_payload["retained_sheet_current_ids"], archive["retained_sheet_current_ids"])

    with np.load(pwr_path, allow_pickle=False) as archive:
        pwr_resistance = unpack_csr(archive, "resistance")
        pwr_divergence = unpack_csr(archive, "charge_divergence")
        pwr_h_geometric = unpack_csr(archive, "terminal_outward")
        pwr_volume_count = len(archive["charge_volume_cell_ids"])
        pwr_free_surface_count = len(archive["free_surface_face_ids"])
    with np.load(native_path, allow_pickle=False) as archive:
        native_potential_count = int(archive["native_potential_count"][0])
        source_outward = unpack_csr(archive, "source_outward_active")
        field_current_offsets = archive["field_current_block_offset"]
    with np.load(support_path, allow_pickle=False) as archive:
        sheet_charge_row_ids = archive["charge_row_ids"]
        contact_charge_columns = archive["contact_charge_columns"]
        contact_charge_row_ids = archive["contact_charge_row_ids"]
        contact_ids = archive["contact_ids"]
        contact_support_index = archive["contact_support_index"]
        contact_native = archive["contact_global_active_index"]

    g_current_count = g_resistance.shape[0]
    pwr_current_count = pwr_resistance.shape[0]
    total_current_count = g_current_count + pwr_current_count
    g_charge_count = g_divergence.shape[0]
    pwr_charge_count = pwr_divergence.shape[0]
    total_charge_count = g_charge_count + pwr_charge_count
    sheet_current_count = int(g_payload["sheet_current_count"][0])
    sheet_charge_count = int(g_payload["sheet_charge_count"][0])
    assert np.array_equal(sheet_charge_row_ids, g_payload["retained_sheet_charge_row_ids"])
    assert len(sheet_charge_row_ids) == sheet_charge_count == 2_440_492
    assert g_current_count == 3_109_672 and pwr_current_count == 681_396
    assert g_charge_count == 2_449_578
    assert pwr_charge_count == pwr_volume_count + pwr_free_surface_count == 434_320
    assert source_outward.shape == (native_potential_count, total_current_count)
    assert np.array_equal(field_current_offsets, [0, g_current_count, total_current_count])
    assert pwr_h_geometric.shape[1] == pwr_current_count

    divergence = sparse.block_diag((g_divergence, pwr_divergence), format="csr")
    constraint = sparse.hstack(
        (g_constraint, sparse.csr_matrix((g_constraint.shape[0], pwr_current_count))), format="csr"
    )

    contact_count = len(contact_ids)
    assert contact_count == 38_854
    assert np.array_equal(sheet_charge_row_ids[contact_charge_columns], contact_charge_row_ids)
    assert np.all((contact_native >= 0) & (contact_native < native_potential_count))
    assert len(np.unique(contact_native)) == contact_count
    contact_exchange_charge = sparse.coo_matrix(
        (np.ones(contact_count), (contact_charge_columns, np.arange(contact_count))),
        shape=(total_charge_count, contact_count),
    ).tocsr()
    contact_exchange_native = sparse.coo_matrix(
        (np.ones(contact_count), (contact_native, np.arange(contact_count))),
        shape=(native_potential_count, contact_count),
    ).tocsr()

    def resistance_action(current):
        current = np.asarray(current).reshape(-1)
        assert current.shape == (total_current_count,)
        return np.r_[g_resistance @ current[:g_current_count], pwr_resistance @ current[g_current_count:]]

    resistance = LinearOperator(
        (total_current_count, total_current_count),
        matvec=resistance_action,
        rmatvec=resistance_action,
        dtype=np.float64,
    )
    offsets = dict(
        current=np.asarray([0, sheet_current_count, g_current_count, total_current_count], dtype=np.int64),
        charge=np.asarray([0, sheet_charge_count, g_charge_count, total_charge_count], dtype=np.int64),
    )
    return resistance, divergence, constraint, source_outward, contact_exchange_charge, contact_exchange_native, offsets, dict(
        contact_ids=contact_ids,
        contact_support_index=contact_support_index,
        contact_native_active_index=contact_native,
        contact_charge_columns=contact_charge_columns,
        contact_charge_row_ids=contact_charge_row_ids,
        pwr_geometric_terminal_count=np.asarray([pwr_h_geometric.shape[0]], dtype=np.int64),
        pwr_volume_charge_count=np.asarray([pwr_volume_count], dtype=np.int64),
        pwr_free_surface_charge_count=np.asarray([pwr_free_surface_count], dtype=np.int64),
    )


def run() -> None:
    started = monotonic()
    assert not OUTPUT.exists()
    resistance, divergence, constraint, source_outward, s_q, s_n, offsets, metadata = assemble()
    ncurrent = resistance.shape[0]
    ncharge = divergence.shape[0]
    nnative = source_outward.shape[0]
    ncontact = s_q.shape[1]

    column_identity = np.asarray(
        divergence.sum(axis=0) - source_outward.sum(axis=0) - constraint.sum(axis=0)
    ).ravel()
    conservation = float(np.max(abs(column_identity)))
    sq_sum = np.asarray(s_q.sum(axis=0)).ravel()
    sn_sum = np.asarray(s_n.sum(axis=0)).ravel()
    exchange_conservation = float(np.max(abs(sq_sum - sn_sum)))

    rng = np.random.default_rng(20260912)
    current_a = rng.normal(size=ncurrent) + 1j * rng.normal(size=ncurrent)
    current_b = rng.normal(size=ncurrent) + 1j * rng.normal(size=ncurrent)
    ra = resistance @ current_a
    rb = resistance @ current_b
    reciprocity = float(abs(current_a @ rb - current_b @ ra) / max(abs(current_a @ rb), abs(current_b @ ra), 1e-30))
    joule = float(np.vdot(current_a, ra).real)

    transfer = rng.normal(size=ncontact) + 1j * rng.normal(size=ncontact)
    charge_potential = np.zeros(ncharge, dtype=complex)
    native_voltage = np.zeros(nnative, dtype=complex)
    charge_potential[metadata["contact_charge_columns"]] = rng.normal(size=ncontact) + 1j * rng.normal(size=ncontact)
    native_voltage[metadata["contact_native_active_index"]] = rng.normal(size=ncontact) + 1j * rng.normal(size=ncontact)
    left = (s_q.T @ charge_potential - s_n.T @ native_voltage) @ transfer
    right = charge_potential @ (s_q @ transfer) - native_voltage @ (s_n @ transfer)
    exchange_duality = float(abs(left - right) / max(abs(left), abs(right), 1e-30))
    full_exchange_balance = float(abs(
        np.sum(divergence @ current_a - s_q @ transfer)
        - np.sum(constraint @ current_a)
        - np.sum(source_outward @ current_a)
        + np.sum(s_n @ transfer)
    ) / max(np.linalg.norm(divergence @ current_a, 1), np.linalg.norm(source_outward @ current_a, 1), 1e-30))

    gates = dict(
        current_column_conservation=conservation < 2e-10,
        contact_exchange_column_conservation=exchange_conservation == 0.0,
        contact_exchange_ordinary_duality=exchange_duality < 2e-12,
        full_current_and_contact_exchange_balance=full_exchange_balance < 2e-12,
        resistance_ordinary_reciprocity=reciprocity < 2e-12,
        resistance_positive_witness=joule > 0.0,
        all_sparse_operators_finite=all(np.isfinite(matrix.data).all() for matrix in (divergence, constraint, source_outward, s_q, s_n)),
        exact_current_order=bool(np.array_equal(offsets["current"], [0, 3_095_418, 3_109_672, 3_791_068])),
        exact_charge_order=bool(np.array_equal(offsets["charge"], [0, 2_440_492, 2_449_578, 2_883_898])),
    )
    assert all(gates.values()), gates

    payload = dict(
        current_block_offsets=offsets["current"],
        current_block_names=np.asarray(["G_RETAINED_L02_SHEET", "G_LOCAL_3D", "PWR_TOP_COMPONENT"]),
        charge_block_offsets=offsets["charge"],
        charge_block_names=np.asarray(["G_RETAINED_L02_SHEET", "G_LOCAL_3D", "PWR_TOP_COMPONENT"]),
        native_potential_count=np.asarray([nnative], dtype=np.int64),
        **metadata,
    )
    pack_csr(payload, "charge_divergence", divergence)
    pack_csr(payload, "cut_constraint", constraint)
    pack_csr(payload, "source_outward_active", source_outward)
    pack_csr(payload, "contact_exchange_charge", s_q)
    pack_csr(payload, "contact_exchange_native", s_n)
    OUTPUT.mkdir(parents=True)
    (OUTPUT / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    artifact = OUTPUT / "pwr-g-contact-exchange-component.npz"
    np.savez_compressed(artifact, **payload)
    result = dict(
        program=PROGRAM,
        version=VERSION,
        status="PASS_EXECUTABLE_PWR_G_R_D_C_H_CONTACT_EXCHANGE",
        elapsed_s=monotonic() - started,
        driver_sha256=digest(Path(__file__)),
        artifact_sha256=digest(artifact),
        pins={str(path.relative_to(ROOT)): expected for path, expected in PINS.items()},
        counts=dict(
            currents=ncurrent,
            charge_rows=ncharge,
            native_potentials=nnative,
            retained_contacts=ncontact,
            cut_constraints=constraint.shape[0],
            divergence_nnz=divergence.nnz,
            source_outward_nnz=source_outward.nnz,
        ),
        order=dict(
            current="G_RETAINED_L02_SHEET, G_LOCAL_3D, PWR_TOP_COMPONENT",
            charge="G_RETAINED_L02_SHEET, G_LOCAL_3D, PWR_TOP_COMPONENT",
            field_action_warning=(
                "The separate point-action implementation orders PWR cells, G cells, PWR faces, G faces. "
                "It must apply an explicit reviewed scatter/gather; these coordinate vectors must not be passed through directly."
            ),
        ),
        equations=[
            "R*i + future(jw*L*i-D.T*P*q+C.T*lambda+H.T*v)=0",
            "D*i + jw*q - S_q*t = 0",
            "S_q.T*P*q - S_n.T*v = 0",
            "Y_native*v + S_n*t - H*i = source",
            "C*i = 0",
        ],
        metrics=dict(
            current_column_conservation_max_abs=conservation,
            contact_exchange_column_conservation_max_abs=exchange_conservation,
            contact_exchange_ordinary_duality_relative=exchange_duality,
            full_exchange_normalized_balance=full_exchange_balance,
            resistance_ordinary_reciprocity_relative=reciprocity,
            resistance_positive_witness_w=joule,
        ),
        gates={name: bool(value) for name, value in gates.items()},
        scope=(
            "Executable coordinate and sparse-incidence component for retained DGND L02 plus the selected G 3D window "
            "and complete actual PWR TOP component. R is exposed by assemble() as a block LinearOperator; D, C, H, "
            "S_q and S_n are saved. Contact transfer t uses native-to-field orientation. L, dielectric P, native Y, "
            "the five retained exterior categories, L25 currents, RHS and a solve remain separate owned components. "
            "No field quadrature, convergence, impedance, or board/PowerSI accuracy claim."
        ),
    )
    (OUTPUT / "result.json").write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "elapsed_s": result["elapsed_s"], "counts": result["counts"]}), flush=True)


if __name__ == "__main__":
    run()
