"""Experimental, geometry-agnostic sparse global MNA kernel.

This module is deliberately independent of the SPD importer and the modal
evaluator.  It provides the algebra needed once artwork, vias, pads and plane
patches have been compiled into a common absolute-node network.  In
particular, it does *not* turn a differential return path into a scalar loop
impedance: every conductor is a node and the MNA solve determines current
sharing.

The formulation is complex-symmetric (ordinary transpose, never conjugate
transpose)::

    [ Y  A  E.T G.T ] [v]   [P]
    [ A.T -Z  0   0  ] [i] = [0]
    [ E   0  0   0  ] [l]   [0]
    [ G   0  0   0  ] [g]   [0]

``G`` has one row for every *structural* component.  This makes the voltage
reference explicit and prevents an arbitrary numerical ground from silently
coupling independent circuits.  A differential port must balance within one
such component; otherwise the problem is physically incomplete and is
rejected before factorisation.

Reference semantics are deliberately narrow: a nodal block is an
*indefinite*, absolute-conductor operator, so its row and column sums must
vanish in every structural component.  A grounded/reduced nodal matrix is
rejected because adding a gauge to such a matrix changes its physics.  The
current differential MFDM surface operator has only a projected per-column
nullity and is therefore **not** a compatible :class:`NodalAdmittanceBlock`.
It first needs either a sheet-current PEEC formulation or an explicit
balanced projection API.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from math import asinh, isfinite, sqrt
from types import MappingProxyType
from typing import Literal, TypeAlias

import numpy as np
from numpy.typing import NDArray
from scipy.sparse import block_diag, csc_matrix, hstack, issparse, spmatrix, vstack
from scipy.sparse.linalg import LinearOperator, eigsh, norm as sparse_norm, onenormest, splu

from spd_decap_pi._core.solver.via_peec import (
    MU_0_H_PER_M,
    FilledMicroviaSegment,
    ViaPeecError,
    parallel_finite_wire_mutual_inductance,
    solid_cylinder_internal_impedance,
    straight_wire_external_self_inductance,
)


_TINY = float(np.finfo(np.float64).tiny)
_MAX_CONDITION = 1.0e13
_REL_RESIDUAL_LIMIT = 1.0e-10
_RECIPROCITY_REL_LIMIT = 1.0e-9
_RECIPROCITY_ABS_LIMIT = 1.0e-12

SparseOrDenseMatrix: TypeAlias = spmatrix | NDArray[np.complex128]
MatrixOrFunction: TypeAlias = SparseOrDenseMatrix | Callable[[float], SparseOrDenseMatrix]
ReferenceSemantics: TypeAlias = Literal["floating_indefinite"]


class GlobalMnaError(ValueError):
    """Raised when an absolute-node MNA problem is incomplete or nonphysical."""


def _readonly(values: object, *, dtype: np.dtype) -> NDArray[np.generic]:
    result = np.asarray(values, dtype=dtype).copy()
    result.setflags(write=False)
    return result


def _require_id(value: str, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise GlobalMnaError(f"{name} must be a non-empty string")
    return value


def _normalise_owner_ids(
    values: tuple[str, ...] | None,
    *,
    fallback: str,
    name: str,
) -> tuple[str, ...]:
    """Return immutable physical-owner evidence for one source block.

    A block with no explicit owner is still auditable: its block ID becomes a
    deliberately unique provisional owner.  Adapters which know that two
    numerical blocks represent the same copper face, gap, via, or local
    replacement must provide that common physical ID; compilation then rejects
    the double stamp before a port reduction can hide it.
    """

    if values is None:
        return (fallback,)
    if isinstance(values, str):
        raise GlobalMnaError(f"{name} must be a tuple of non-empty owner IDs, not a string")
    result = tuple(_require_id(value, name=name) for value in values)
    if not result or len(set(result)) != len(result):
        raise GlobalMnaError(f"{name} must contain unique non-empty owner IDs")
    return result


def _finite_frequency(value: float) -> float:
    frequency = float(value)
    if not isfinite(frequency) or frequency < 0.0:
        raise GlobalMnaError("frequency_hz must be finite and >= 0")
    return frequency


def _evaluate_sparse(
    value: MatrixOrFunction,
    frequency_hz: float,
    shape: tuple[int, int],
    *,
    name: str,
) -> csc_matrix:
    try:
        raw = value(frequency_hz) if callable(value) else value
    except Exception as exc:  # pragma: no cover - caller-specific callable
        raise GlobalMnaError(f"{name} evaluation failed at {frequency_hz:g} Hz") from exc
    if issparse(raw):
        matrix = csc_matrix(raw, dtype=np.complex128, copy=True)
    else:
        dense = np.asarray(raw, dtype=np.complex128)
        if dense.shape != shape or not np.all(np.isfinite(dense)):
            raise GlobalMnaError(f"{name} must return a finite matrix of shape {shape}")
        matrix = csc_matrix(dense)
    if matrix.shape != shape or not np.all(np.isfinite(matrix.data)):
        raise GlobalMnaError(f"{name} must return a finite matrix of shape {shape}")
    matrix.sum_duplicates()
    matrix.eliminate_zeros()
    return matrix


def _complex_symmetric_error(matrix: csc_matrix) -> tuple[float, float]:
    mismatch = matrix - matrix.T
    return float(np.max(np.abs(mismatch.data), initial=0.0)), float(
        sparse_norm(mismatch) / max(float(sparse_norm(matrix)), _TINY)
    )


def _dense_local(matrix: csc_matrix) -> NDArray[np.complex128]:
    """Materialise one explicitly coupled local block, never a global matrix."""

    dense = np.zeros(matrix.shape, dtype=np.complex128)
    coo = matrix.tocoo()
    np.add.at(dense, (coo.row, coo.col), coo.data)
    return dense


def _minimum_hermitian_eigenvalue(
    matrix: csc_matrix, *, name: str
) -> tuple[float, float]:
    """Return ``(lambda_min, tolerance)`` with a sparse large-block path."""

    if matrix.shape[0] == 0:
        return 0.0, 0.0
    hermitian = csc_matrix((matrix + matrix.getH()) * 0.5)
    scale = max(float(sparse_norm(hermitian)), 1.0e-18)
    tolerance = scale * 1.0e-12
    try:
        if hermitian.shape[0] <= 64:
            minimum = float(np.min(np.linalg.eigvalsh(_dense_local(hermitian))))
        else:
            minimum = float(
                eigsh(
                    hermitian,
                    k=1,
                    which="SA",
                    return_eigenvectors=False,
                    tol=1.0e-10,
                    maxiter=max(1000, 20 * hermitian.shape[0]),
                )[0]
            )
    except Exception as exc:
        raise GlobalMnaError(f"{name} Hermitian passivity estimate failed") from exc
    if not isfinite(minimum):
        raise GlobalMnaError(f"{name} Hermitian passivity estimate is non-finite")
    return minimum, tolerance


def _require_local_passivity(matrix: csc_matrix, *, name: str) -> tuple[float, float]:
    minimum, tolerance = _minimum_hermitian_eigenvalue(matrix, name=name)
    if minimum < -tolerance:
        raise GlobalMnaError(f"{name} has a hidden non-passive conductance mode")
    return minimum, tolerance


def _sparse_condition(matrix: csc_matrix, *, name: str) -> float:
    if matrix.shape == (1, 1):
        if matrix.nnz != 1 or matrix.data[0] == 0.0:
            raise GlobalMnaError(f"{name} is singular")
        return 1.0
    try:
        factor = splu(matrix)
        inverse = LinearOperator(
            matrix.shape,
            matvec=lambda x: factor.solve(x),
            rmatvec=lambda x: factor.solve(x, trans="H"),
            dtype=np.complex128,
        )
        estimate = float(onenormest(matrix) * onenormest(inverse))
    except Exception as exc:
        raise GlobalMnaError(f"{name} condition estimate failed or matrix is singular") from exc
    if not isfinite(estimate) or estimate > _MAX_CONDITION:
        raise GlobalMnaError(f"{name} is ill-conditioned ({estimate:.3e})")
    return estimate


@dataclass(frozen=True, slots=True)
class NodalAdmittanceBlock:
    """One source-proven nodal admittance stamp on named absolute nodes."""

    node_ids: tuple[str, ...]
    admittance_s: MatrixOrFunction
    block_id: str
    owner_ids: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        ids = tuple(_require_id(node, name="nodal node_id") for node in self.node_ids)
        if not ids or len(set(ids)) != len(ids):
            raise GlobalMnaError("nodal admittance block needs unique node_ids")
        block_id = _require_id(self.block_id, name="nodal block_id")
        object.__setattr__(self, "node_ids", ids)
        object.__setattr__(self, "block_id", block_id)
        object.__setattr__(
            self,
            "owner_ids",
            _normalise_owner_ids(self.owner_ids, fallback=block_id, name="nodal owner_id"),
        )


@dataclass(frozen=True, slots=True)
class SeriesBranchBlock:
    """Coupled series branches, with positive-to-negative endpoint incidence."""

    branch_ids: tuple[str, ...]
    positive_node_ids: tuple[str, ...]
    negative_node_ids: tuple[str, ...]
    impedance_ohm: MatrixOrFunction
    block_id: str
    owner_ids: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        branches = tuple(_require_id(item, name="branch_id") for item in self.branch_ids)
        positive = tuple(_require_id(item, name="positive_node_id") for item in self.positive_node_ids)
        negative = tuple(_require_id(item, name="negative_node_id") for item in self.negative_node_ids)
        if not branches or len(set(branches)) != len(branches):
            raise GlobalMnaError("series branch block needs unique branch_ids")
        if not (len(branches) == len(positive) == len(negative)):
            raise GlobalMnaError("branch IDs and endpoint IDs must have equal length")
        if any(a == b for a, b in zip(positive, negative, strict=True)):
            raise GlobalMnaError("a series branch cannot have identical endpoints")
        object.__setattr__(self, "branch_ids", branches)
        object.__setattr__(self, "positive_node_ids", positive)
        object.__setattr__(self, "negative_node_ids", negative)
        block_id = _require_id(self.block_id, name="series block_id")
        object.__setattr__(self, "block_id", block_id)
        object.__setattr__(
            self,
            "owner_ids",
            _normalise_owner_ids(self.owner_ids, fallback=block_id, name="series owner_id"),
        )


@dataclass(frozen=True, slots=True)
class EquipotentialConstraint:
    """An exact zero-voltage constraint ``V(first) - V(second) = 0``."""

    constraint_id: str
    first_node_id: str
    second_node_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "constraint_id", _require_id(self.constraint_id, name="constraint_id"))
        first = _require_id(self.first_node_id, name="first_node_id")
        second = _require_id(self.second_node_id, name="second_node_id")
        if first == second:
            raise GlobalMnaError("equipotential constraint cannot reference one node twice")
        object.__setattr__(self, "first_node_id", first)
        object.__setattr__(self, "second_node_id", second)


@dataclass(frozen=True, slots=True)
class DifferentialPort:
    """A finite differential current port represented by two absolute nodes."""

    port_id: str
    positive_node_id: str
    negative_node_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "port_id", _require_id(self.port_id, name="port_id"))
        positive = _require_id(self.positive_node_id, name="positive_node_id")
        negative = _require_id(self.negative_node_id, name="negative_node_id")
        if positive == negative:
            raise GlobalMnaError("differential port cannot have identical nodes")
        object.__setattr__(self, "positive_node_id", positive)
        object.__setattr__(self, "negative_node_id", negative)


@dataclass(frozen=True, slots=True)
class GlobalMnaDiagnostics:
    """Raw, independently checked numerical evidence for one solve."""

    component_count: int
    gauge_node_ids: tuple[str, ...]
    branch_impedance_condition_estimate: float
    branch_impedance_nnz: int
    saddle_nnz: int
    scaled_saddle_condition_estimate: float
    backward_relative_residual: float
    nodal_kcl_relative_residual: float
    branch_equation_relative_residual: float
    equipotential_relative_residual: float
    gauge_relative_residual: float
    raw_reciprocity_absolute_error_ohm: float
    raw_reciprocity_relative_error: float
    min_hermitian_impedance_eigenvalue_ohm: float
    passivity_tolerance_ohm: float


@dataclass(frozen=True, slots=True)
class GlobalMnaSolveResult:
    """Open-port impedance, with no post-solve symmetrisation."""

    frequency_hz: float
    port_ids: tuple[str, ...]
    impedance_ohm: NDArray[np.complex128]
    node_voltages_v_per_a: NDArray[np.complex128]
    branch_currents_a_per_a: NDArray[np.complex128]
    diagnostics: GlobalMnaDiagnostics

    def __post_init__(self) -> None:
        ports = len(self.port_ids)
        impedance = _readonly(self.impedance_ohm, dtype=np.complex128)
        voltages = _readonly(self.node_voltages_v_per_a, dtype=np.complex128)
        currents = _readonly(self.branch_currents_a_per_a, dtype=np.complex128)
        if impedance.shape != (ports, ports) or voltages.ndim != 2 or voltages.shape[1] != ports:
            raise GlobalMnaError("global MNA solve result has invalid port matrix shapes")
        if currents.ndim != 2 or currents.shape[1] != ports:
            raise GlobalMnaError("global MNA branch-current result has invalid shape")
        object.__setattr__(self, "impedance_ohm", impedance)
        object.__setattr__(self, "node_voltages_v_per_a", voltages)
        object.__setattr__(self, "branch_currents_a_per_a", currents)


@dataclass(frozen=True, slots=True)
class GlobalMnaOperator:
    """Compiled immutable topology; evaluate it at one frequency with ``solve``."""

    node_ids: tuple[str, ...]
    nodal_blocks: tuple[NodalAdmittanceBlock, ...]
    branch_blocks: tuple[SeriesBranchBlock, ...]
    equipotential_constraints: tuple[EquipotentialConstraint, ...]
    ports: tuple[DifferentialPort, ...]
    reference_semantics: ReferenceSemantics
    _node_index: dict[str, int]
    _nodal_indices: tuple[NDArray[np.int64], ...]
    _branch_incidence: csc_matrix
    _branch_slices: tuple[slice, ...]
    _constraint_matrix: csc_matrix
    _port_incidence: csc_matrix
    ownership_ledger: Mapping[str, str]

    @property
    def branch_count(self) -> int:
        return int(self._branch_incidence.shape[1])

    def solve(self, frequency_hz: float) -> GlobalMnaSolveResult:
        return solve_global_mna(self, frequency_hz)


class _UnionFind:
    def __init__(self, count: int) -> None:
        self.parent = list(range(count))

    def find(self, value: int) -> int:
        while self.parent[value] != value:
            self.parent[value] = self.parent[self.parent[value]]
            value = self.parent[value]
        return value

    def union(self, first: int, second: int) -> bool:
        left, right = self.find(first), self.find(second)
        if left == right:
            return False
        self.parent[right] = left
        return True


def compile_global_mna(
    node_ids: Iterable[str],
    *,
    nodal_admittances: Iterable[NodalAdmittanceBlock] = (),
    branch_blocks: Iterable[SeriesBranchBlock] = (),
    equipotential_constraints: Iterable[EquipotentialConstraint] = (),
    ports: Iterable[DifferentialPort] = (),
    reference_semantics: ReferenceSemantics = "floating_indefinite",
) -> GlobalMnaOperator:
    """Compile a floating indefinite absolute-node topology.

    Grounded/reduced operators are intentionally not accepted: their reference
    node has already been eliminated, so adding a component gauge would change
    the physical network.
    """

    nodes = tuple(_require_id(node, name="node_id") for node in node_ids)
    if not nodes or len(set(nodes)) != len(nodes):
        raise GlobalMnaError("global MNA requires non-empty, unique node_ids")
    index = {node: position for position, node in enumerate(nodes)}
    nodal = tuple(nodal_admittances)
    branches = tuple(branch_blocks)
    constraints = tuple(equipotential_constraints)
    port_tuple = tuple(ports)
    if reference_semantics != "floating_indefinite":
        raise GlobalMnaError(
            "only floating_indefinite nodal reference semantics are supported"
        )
    if not all(isinstance(item, NodalAdmittanceBlock) for item in nodal):
        raise GlobalMnaError("nodal_admittances must contain NodalAdmittanceBlock values")
    if not all(isinstance(item, SeriesBranchBlock) for item in branches):
        raise GlobalMnaError("branch_blocks must contain SeriesBranchBlock values")
    if not all(isinstance(item, EquipotentialConstraint) for item in constraints):
        raise GlobalMnaError("equipotential_constraints must contain EquipotentialConstraint values")
    if not port_tuple or not all(isinstance(item, DifferentialPort) for item in port_tuple):
        raise GlobalMnaError("global MNA requires at least one DifferentialPort")
    if len({port.port_id for port in port_tuple}) != len(port_tuple):
        raise GlobalMnaError("port_id values must be unique")
    source_block_ids = [block.block_id for block in nodal] + [
        block.block_id for block in branches
    ]
    if len(set(source_block_ids)) != len(source_block_ids):
        raise GlobalMnaError("source block_id values must be globally unique")
    ownership_ledger: dict[str, str] = {}
    for block in (*nodal, *branches):
        for owner_id in block.owner_ids:
            previous = ownership_ledger.setdefault(owner_id, block.block_id)
            if previous != block.block_id:
                raise GlobalMnaError(
                    f"physical owner {owner_id!r} is stamped by both {previous!r} "
                    f"and {block.block_id!r}; declare one owner or an explicit exact-core replacement"
                )
    all_branch_ids = [branch_id for block in branches for branch_id in block.branch_ids]
    if len(set(all_branch_ids)) != len(all_branch_ids):
        raise GlobalMnaError("branch_id values must be globally unique")
    constraint_ids = [constraint.constraint_id for constraint in constraints]
    if len(set(constraint_ids)) != len(constraint_ids):
        raise GlobalMnaError("constraint_id values must be globally unique")
    known = lambda name: index.get(name, -1)
    nodal_indices: list[NDArray[np.int64]] = []
    for block in nodal:
        positions = np.asarray([known(node) for node in block.node_ids], dtype=np.int64)
        if np.any(positions < 0):
            raise GlobalMnaError(f"nodal block {block.block_id!r} references an unknown node")
        nodal_indices.append(positions)
    branch_cols: list[int] = []
    branch_rows: list[int] = []
    branch_values: list[float] = []
    block_slices: list[slice] = []
    start = 0
    for block in branches:
        for column, (positive, negative) in enumerate(zip(block.positive_node_ids, block.negative_node_ids, strict=True), start):
            p, n = known(positive), known(negative)
            if p < 0 or n < 0:
                raise GlobalMnaError(f"branch block {block.block_id!r} references an unknown node")
            branch_rows.extend((p, n))
            branch_cols.extend((column, column))
            branch_values.extend((1.0, -1.0))
        start += len(block.branch_ids)
        block_slices.append(slice(start - len(block.branch_ids), start))
    incidence = csc_matrix((branch_values, (branch_rows, branch_cols)), shape=(len(nodes), start), dtype=np.complex128)
    # Equality rows must be linearly independent.  Pairwise zero-voltage
    # constraints are independent exactly when their edge set is a forest.
    equality_uf = _UnionFind(len(nodes))
    e_rows: list[int] = []
    e_cols: list[int] = []
    e_values: list[float] = []
    for row, constraint in enumerate(constraints):
        first, second = known(constraint.first_node_id), known(constraint.second_node_id)
        if first < 0 or second < 0:
            raise GlobalMnaError(f"equipotential constraint {constraint.constraint_id!r} references an unknown node")
        if not equality_uf.union(first, second):
            raise GlobalMnaError(f"equipotential constraint {constraint.constraint_id!r} is redundant")
        e_rows.extend((row, row))
        e_cols.extend((first, second))
        e_values.extend((1.0, -1.0))
    equality = csc_matrix((e_values, (e_rows, e_cols)), shape=(len(constraints), len(nodes)), dtype=np.complex128)
    p_rows: list[int] = []
    p_cols: list[int] = []
    p_values: list[float] = []
    for column, port in enumerate(port_tuple):
        positive, negative = known(port.positive_node_id), known(port.negative_node_id)
        if positive < 0 or negative < 0:
            raise GlobalMnaError(f"port {port.port_id!r} references an unknown node")
        p_rows.extend((positive, negative))
        p_cols.extend((column, column))
        p_values.extend((1.0, -1.0))
    port_incidence = csc_matrix((p_values, (p_rows, p_cols)), shape=(len(nodes), len(port_tuple)), dtype=np.complex128)
    return GlobalMnaOperator(
        nodes,
        nodal,
        branches,
        constraints,
        port_tuple,
        reference_semantics,
        index,
        tuple(nodal_indices),
        incidence,
        tuple(block_slices),
        equality,
        port_incidence,
        MappingProxyType(dict(ownership_ledger)),
    )


def _assemble_nodal_admittance(operator: GlobalMnaOperator, frequency_hz: float) -> csc_matrix:
    count = len(operator.node_ids)
    result = csc_matrix((count, count), dtype=np.complex128)
    for block, positions in zip(operator.nodal_blocks, operator._nodal_indices, strict=True):
        stamp = _evaluate_sparse(
            block.admittance_s,
            frequency_hz,
            (len(positions), len(positions)),
            name=f"nodal block {block.block_id!r} admittance",
        )
        absolute, relative = _complex_symmetric_error(stamp)
        if absolute > _RECIPROCITY_ABS_LIMIT and relative > _RECIPROCITY_REL_LIMIT:
            raise GlobalMnaError(f"nodal block {block.block_id!r} violates complex-symmetric reciprocity")
        _require_local_passivity(stamp, name=f"nodal block {block.block_id!r}")
        ones = np.ones(stamp.shape[0], dtype=np.complex128)
        null_error = max(
            float(np.max(np.abs(stamp @ ones), initial=0.0)),
            float(np.max(np.abs(stamp.T @ ones), initial=0.0)),
        )
        null_tolerance = max(1.0e-14, float(sparse_norm(stamp)) * 1.0e-10)
        if null_error > null_tolerance:
            raise GlobalMnaError(
                f"nodal block {block.block_id!r} is grounded/reduced; "
                "floating_indefinite stamps require zero row and column sums"
            )
        local = stamp.tocoo()
        result += csc_matrix(
            (local.data, (positions[local.row], positions[local.col])),
            shape=(count, count),
        )
    if not np.all(np.isfinite(result.data)):
        raise GlobalMnaError("assembled nodal admittance is non-finite")
    result.sum_duplicates()
    result.eliminate_zeros()
    return result


def _assemble_branch_impedance(
    operator: GlobalMnaOperator, frequency_hz: float
) -> tuple[csc_matrix, float]:
    stamps: list[csc_matrix] = []
    maximum_condition = 1.0
    for block in operator.branch_blocks:
        stamp = _evaluate_sparse(
            block.impedance_ohm,
            frequency_hz,
            (len(block.branch_ids), len(block.branch_ids)),
            name=f"branch block {block.block_id!r} impedance",
        )
        absolute, relative = _complex_symmetric_error(stamp)
        if absolute > _RECIPROCITY_ABS_LIMIT and relative > _RECIPROCITY_REL_LIMIT:
            raise GlobalMnaError(f"branch block {block.block_id!r} violates complex-symmetric reciprocity")
        _require_local_passivity(stamp, name=f"branch block {block.block_id!r}")
        maximum_condition = max(
            maximum_condition,
            _sparse_condition(stamp, name=f"branch block {block.block_id!r}"),
        )
        stamps.append(stamp)
    result = csc_matrix(
        block_diag(stamps, format="csc")
        if stamps
        else csc_matrix((0, 0), dtype=np.complex128)
    )
    if result.shape != (operator.branch_count, operator.branch_count):
        raise GlobalMnaError("assembled branch impedance has an invalid shape")
    if not np.all(np.isfinite(result.data)):
        raise GlobalMnaError("assembled branch impedance is non-finite")
    return result, maximum_condition


def _components(operator: GlobalMnaOperator, nodal_admittance: csc_matrix) -> tuple[tuple[tuple[int, ...], ...], tuple[int, ...]]:
    count = len(operator.node_ids)
    union = _UnionFind(count)
    # Actual nonzero Y terms, not just a block's declared node list, decide the
    # component.  A diagonal shunt does not invent a physical conductor path.
    coo = nodal_admittance.tocoo()
    for row, col, value in zip(coo.row, coo.col, coo.data, strict=True):
        if row != col and value != 0.0:
            union.union(int(row), int(col))
    coo = operator._branch_incidence.tocoo()
    per_branch: dict[int, list[int]] = {}
    for row, col in zip(coo.row, coo.col, strict=True):
        per_branch.setdefault(int(col), []).append(int(row))
    for endpoints in per_branch.values():
        if len(endpoints) == 2:
            union.union(endpoints[0], endpoints[1])
    coo = operator._constraint_matrix.tocoo()
    per_constraint: dict[int, list[int]] = {}
    for row, col in zip(coo.row, coo.col, strict=True):
        per_constraint.setdefault(int(row), []).append(int(col))
    for endpoints in per_constraint.values():
        if len(endpoints) == 2:
            union.union(endpoints[0], endpoints[1])
    groups: dict[int, list[int]] = {}
    for node in range(count):
        groups.setdefault(union.find(node), []).append(node)
    components = tuple(tuple(items) for _, items in sorted(groups.items(), key=lambda pair: min(pair[1])))
    component_by_node = [-1] * count
    for component, members in enumerate(components):
        for node in members:
            component_by_node[node] = component
    return components, tuple(component_by_node)


def _gauge_matrix(operator: GlobalMnaOperator, nodal_admittance: csc_matrix) -> tuple[csc_matrix, tuple[str, ...]]:
    components, component_by_node = _components(operator, nodal_admittance)
    port = operator._port_incidence.tocsc()
    for col, item in enumerate(operator.ports):
        rows = port[:, col].nonzero()[0]
        if len(rows) != 2 or component_by_node[int(rows[0])] != component_by_node[int(rows[1])]:
            raise GlobalMnaError(
                f"port {item.port_id!r} injects net current into separate structural components"
            )
    for members in components:
        positions = np.asarray(members, dtype=np.int64)
        local = csc_matrix(nodal_admittance[positions, :][:, positions])
        branch_activity = int(operator._branch_incidence[positions, :].nnz)
        equality_activity = int(operator._constraint_matrix[:, positions].nnz)
        if local.nnz == 0 and branch_activity == 0 and equality_activity == 0:
            labels = tuple(operator.node_ids[index] for index in members)
            raise GlobalMnaError(
                f"singular structural island has no source stamp: {labels!r}"
            )
        ones = np.ones(len(members), dtype=np.complex128)
        row_sum = np.asarray(local @ ones).ravel()
        column_sum = np.asarray(local.T @ ones).ravel()
        error = max(
            float(np.max(np.abs(row_sum), initial=0.0)),
            float(np.max(np.abs(column_sum), initial=0.0)),
        )
        tolerance = max(1.0e-14, float(sparse_norm(local)) * 1.0e-10)
        if error > tolerance:
            labels = tuple(operator.node_ids[index] for index in members[:3])
            raise GlobalMnaError(
                "floating_indefinite nodal admittance must have zero row and "
                f"column sums in every structural component; failed near {labels!r}"
            )
    rows = list(range(len(components)))
    cols = [members[0] for members in components]
    gauge = csc_matrix((np.ones(len(rows)), (rows, cols)), shape=(len(rows), len(operator.node_ids)), dtype=np.complex128)
    return gauge, tuple(operator.node_ids[column] for column in cols)


def _scaled_condition(saddle: csc_matrix) -> float:
    row_norm = np.asarray(np.abs(saddle).sum(axis=1)).ravel()
    if np.any(~np.isfinite(row_norm)) or np.any(row_norm <= 0.0):
        raise GlobalMnaError("saddle system contains a singular structural row")
    scale = 1.0 / np.sqrt(row_norm)
    scaled = csc_matrix(saddle.multiply(scale[:, None]).multiply(scale[None, :]))
    try:
        scaled_factor = splu(scaled)
        inverse = LinearOperator(
            scaled.shape,
            matvec=lambda x: scaled_factor.solve(x),
            rmatvec=lambda x: scaled_factor.solve(x, trans="H"),
            dtype=np.complex128,
        )
        estimate = float(onenormest(scaled) * onenormest(inverse))
    except Exception as exc:
        raise GlobalMnaError("saddle-system condition estimate failed") from exc
    if not isfinite(estimate) or estimate > _MAX_CONDITION:
        raise GlobalMnaError(f"saddle system is ill-conditioned ({estimate:.3e})")
    return estimate


def solve_global_mna(operator: GlobalMnaOperator, frequency_hz: float) -> GlobalMnaSolveResult:
    """Solve every port with one sparse factorisation and strict raw-result gates."""

    if not isinstance(operator, GlobalMnaOperator):
        raise GlobalMnaError("operator must be a GlobalMnaOperator")
    frequency = _finite_frequency(frequency_hz)
    y = _assemble_nodal_admittance(operator, frequency)
    z, branch_condition = _assemble_branch_impedance(operator, frequency)
    a = operator._branch_incidence
    e = operator._constraint_matrix
    g, gauge_ids = _gauge_matrix(operator, y)
    zero_nn = csc_matrix((e.shape[0], e.shape[0]), dtype=np.complex128)
    zero_ng = csc_matrix((e.shape[0], g.shape[0]), dtype=np.complex128)
    zero_gn = csc_matrix((g.shape[0], e.shape[0]), dtype=np.complex128)
    zero_gg = csc_matrix((g.shape[0], g.shape[0]), dtype=np.complex128)
    zero_be = csc_matrix((a.shape[1], e.shape[0]), dtype=np.complex128)
    zero_bg = csc_matrix((a.shape[1], g.shape[0]), dtype=np.complex128)
    saddle = csc_matrix(vstack((
        hstack((y, a, e.T, g.T), format="csc"),
        hstack((a.T, -z, zero_be, zero_bg), format="csc"),
        hstack((e, zero_be.T, zero_nn, zero_ng), format="csc"),
        hstack((g, zero_bg.T, zero_gn, zero_gg), format="csc"),
    ), format="csc"))
    if not np.all(np.isfinite(saddle.data)):
        raise GlobalMnaError("saddle system is non-finite")
    try:
        factor = splu(saddle)
    except Exception as exc:
        raise GlobalMnaError("saddle system is singular; topology has an unresolved island") from exc
    condition = _scaled_condition(saddle)
    node_count = len(operator.node_ids)
    port_count = len(operator.ports)
    node_rhs = np.zeros((node_count, port_count), dtype=np.complex128)
    port_coo = operator._port_incidence.tocoo()
    np.add.at(node_rhs, (port_coo.row, port_coo.col), port_coo.data)
    rhs = np.zeros((saddle.shape[0], port_count), dtype=np.complex128)
    rhs[:node_count, :] = node_rhs
    try:
        solution = np.asarray(factor.solve(rhs), dtype=np.complex128)
    except Exception as exc:
        raise GlobalMnaError("sparse saddle solve failed") from exc
    if not np.all(np.isfinite(solution)):
        raise GlobalMnaError("sparse saddle solve produced non-finite values")
    residual = saddle @ solution - rhs
    backward = float(
        np.linalg.norm(residual)
        / max(
            float(sparse_norm(saddle) * np.linalg.norm(solution) + np.linalg.norm(rhs)),
            _TINY,
        )
    )
    if not isfinite(backward) or backward > _REL_RESIDUAL_LIMIT:
        raise GlobalMnaError(f"saddle solve has excessive backward residual ({backward:.3e})")
    branch_count, equality_count = a.shape[1], e.shape[0]
    voltages = solution[:node_count, :]
    currents = solution[node_count : node_count + branch_count, :]
    multipliers = solution[node_count + branch_count : node_count + branch_count + equality_count, :]
    gauges = solution[node_count + branch_count + equality_count :, :]
    nodal_residual = y @ voltages + a @ currents + e.T @ multipliers + g.T @ gauges - node_rhs
    branch_residual = a.T @ voltages - z @ currents
    equality_residual = e @ voltages
    gauge_residual = g @ voltages
    def relative(value: NDArray[np.complex128], scale: float) -> float:
        return float(np.linalg.norm(value) / max(scale, _TINY))
    nodal_error = relative(nodal_residual, float(np.linalg.norm(rhs[:node_count, :])))
    branch_error = relative(branch_residual, float(np.linalg.norm(a.T @ voltages) + np.linalg.norm(z @ currents)))
    equality_error = relative(equality_residual, float(np.linalg.norm(voltages)))
    gauge_error = relative(gauge_residual, float(np.linalg.norm(voltages)))
    if max(nodal_error, branch_error, equality_error, gauge_error) > _REL_RESIDUAL_LIMIT:
        raise GlobalMnaError("MNA KCL, branch, equality, or gauge residual exceeds gate")
    raw_impedance = np.asarray(operator._port_incidence.T @ voltages, dtype=np.complex128)
    reciprocity_absolute, reciprocity_relative = _complex_symmetric_error(
        csc_matrix(raw_impedance)
    )
    if reciprocity_absolute > _RECIPROCITY_ABS_LIMIT and reciprocity_relative > _RECIPROCITY_REL_LIMIT:
        raise GlobalMnaError("raw open-port impedance violates reciprocity")
    hermitian = (raw_impedance + raw_impedance.conj().T) * 0.5
    min_hermitian = float(np.min(np.linalg.eigvalsh(hermitian)))
    passivity_tolerance = max(float(np.linalg.norm(raw_impedance, ord=2)), 1.0e-18) * 1.0e-9
    if min_hermitian < -passivity_tolerance:
        raise GlobalMnaError("raw open-port impedance is non-passive")
    return GlobalMnaSolveResult(
        frequency,
        tuple(port.port_id for port in operator.ports),
        raw_impedance,
        voltages,
        currents,
        GlobalMnaDiagnostics(
            component_count=g.shape[0],
            gauge_node_ids=gauge_ids,
            branch_impedance_condition_estimate=branch_condition,
            branch_impedance_nnz=int(z.nnz),
            saddle_nnz=int(saddle.nnz),
            scaled_saddle_condition_estimate=condition,
            backward_relative_residual=backward,
            nodal_kcl_relative_residual=nodal_error,
            branch_equation_relative_residual=branch_error,
            equipotential_relative_residual=equality_error,
            gauge_relative_residual=gauge_error,
            raw_reciprocity_absolute_error_ohm=reciprocity_absolute,
            raw_reciprocity_relative_error=reciprocity_relative,
            min_hermitian_impedance_eigenvalue_ohm=min_hermitian,
            passivity_tolerance_ohm=passivity_tolerance,
        ),
    )


@dataclass(frozen=True, slots=True)
class FilledMicroviaBranch:
    """A source-dimensioned filled-via segment mapped to two graph nodes.

    Stacked segments simply use an intermediate node.  They are intentionally
    *not* passed through the legacy equal-endpoint parallel-via reducer.
    ``current_sign`` maps the positive branch current to the canonical +z
    direction used by the unsigned PEEC geometry matrix.
    """

    branch_id: str
    positive_node_id: str
    negative_node_id: str
    x_m: float
    y_m: float
    z0_m: float
    z1_m: float
    radius_m: float
    conductivity_s_per_m: float
    current_sign: int = 1

    def __post_init__(self) -> None:
        _require_id(self.branch_id, name="branch_id")
        _require_id(self.positive_node_id, name="positive_node_id")
        _require_id(self.negative_node_id, name="negative_node_id")
        if self.positive_node_id == self.negative_node_id:
            raise GlobalMnaError("microvia branch cannot have identical endpoint nodes")
        try:
            segment = FilledMicroviaSegment(
                self.branch_id, self.x_m, self.y_m, self.z0_m, self.z1_m,
                self.radius_m, self.conductivity_s_per_m, self.branch_id, self.branch_id,
                self.current_sign,
            )
        except ViaPeecError as exc:
            raise GlobalMnaError(str(exc)) from exc
        for name in ("x_m", "y_m", "z0_m", "z1_m", "radius_m", "conductivity_s_per_m", "current_sign"):
            object.__setattr__(self, name, getattr(segment, name))

    @property
    def length_m(self) -> float:
        return self.z1_m - self.z0_m

    def _segment(self) -> FilledMicroviaSegment:
        return FilledMicroviaSegment(self.branch_id, self.x_m, self.y_m, self.z0_m, self.z1_m, self.radius_m, self.conductivity_s_per_m, self.branch_id, self.branch_id, self.current_sign)


@dataclass(frozen=True, slots=True)
class FilledMicroviaDiagnostic:
    """Isolated partial-L/R reference that cannot enter global MNA.

    Partial inductance depends on a reference boundary.  Until the plate core
    owned by that boundary is available and an actual ``exact - core``
    correction is calculated, this matrix is useful only for canonical tests
    and diagnostics.  Keeping a distinct type prevents a role string or owner
    ID from masquerading as subtraction data.
    """

    branches: tuple[FilledMicroviaBranch, ...]
    external_partial_inductance_h: NDArray[np.float64]

    def __post_init__(self) -> None:
        branches = tuple(self.branches)
        partial = _readonly(self.external_partial_inductance_h, dtype=np.float64)
        if not branches or not all(isinstance(item, FilledMicroviaBranch) for item in branches):
            raise GlobalMnaError("filled microvia diagnostic requires source branches")
        if partial.shape != (len(branches), len(branches)) or not np.array_equal(partial, partial.T):
            raise GlobalMnaError("filled microvia diagnostic partial-L matrix is invalid")
        object.__setattr__(self, "branches", branches)
        object.__setattr__(self, "external_partial_inductance_h", partial)

    @property
    def global_mna_composable(self) -> bool:
        return False

    def require_global_mna_composable(self) -> None:
        raise GlobalMnaError(
            "isolated filled-microvia partial inductance is diagnostic-only; "
            "global MNA requires actual exact/core matrices, stable owner hashes, "
            "and a verified exact-minus-core correction"
        )

    def branch_impedance_ohm(self, frequency_hz: float) -> NDArray[np.complex128]:
        frequency = _finite_frequency(frequency_hz)
        internal = np.asarray(
            [
                solid_cylinder_internal_impedance(
                    frequency,
                    length_m=item.length_m,
                    radius_m=item.radius_m,
                    conductivity_s_per_m=item.conductivity_s_per_m,
                )
                for item in self.branches
            ],
            dtype=np.complex128,
        )
        signs = np.asarray([item.current_sign for item in self.branches], dtype=np.float64)
        result = np.diag(internal) + 1j * 2.0 * np.pi * frequency * (
            self.external_partial_inductance_h * np.outer(signs, signs)
        )
        result.setflags(write=False)
        return result


def _collinear_disjoint_mutual(first: FilledMicroviaBranch, second: FilledMicroviaBranch) -> float:
    """Radius-regularized mutual L for same-axis, disjoint via segments.

    ``straight_wire_external_self_inductance`` is the double integral of
    ``1/sqrt((z-z')**2 + radius**2)``.  Using the same physical radius in
    every cross interval makes a contiguous subdivision an exact partition of
    that integral: the total external L is therefore invariant to any 2/4/N
    split.  A rho->0 filament kernel does not have this property and
    overstates the cross term.

    No validated volume regularization is available here for unequal-radius
    collinear segments, so that topology fails closed.
    """

    if not (first.z1_m <= second.z0_m or second.z1_m <= first.z0_m):
        raise GlobalMnaError("coincident or overlapping microvia source segments need PEEC subdivision")
    if first.radius_m != second.radius_m:
        raise GlobalMnaError(
            "same-axis stacked microvia segments with unequal radii require "
            "a validated volume-current regularization"
        )
    radius = first.radius_m

    def primitive(value: float) -> float:
        return value * asinh(value / radius) - sqrt(value * value + radius * radius)

    value = (
        primitive(first.z1_m - second.z0_m)
        - primitive(first.z0_m - second.z0_m)
        - primitive(first.z1_m - second.z1_m)
        + primitive(first.z0_m - second.z1_m)
    )
    if not isfinite(value) or value <= 0.0:
        raise GlobalMnaError("collinear stacked microvia mutual inductance is nonphysical")
    return MU_0_H_PER_M / (4.0 * np.pi) * value


def inspect_filled_microvia_reference(
    branches: Iterable[FilledMicroviaBranch],
) -> FilledMicroviaDiagnostic:
    """Calculate an isolated reference matrix without claiming composability."""

    items = tuple(branches)
    if not items or not all(isinstance(item, FilledMicroviaBranch) for item in items):
        raise GlobalMnaError("filled microvia adapter requires FilledMicroviaBranch values")
    if len({item.branch_id for item in items}) != len(items):
        raise GlobalMnaError("filled microvia branch_id values must be unique")
    geometry = np.empty((len(items), len(items)), dtype=np.float64)
    for first_index, first in enumerate(items):
        geometry[first_index, first_index] = straight_wire_external_self_inductance(first.length_m, first.radius_m)
        for second_index in range(first_index + 1, len(items)):
            second = items[second_index]
            separation = float(np.hypot(first.x_m - second.x_m, first.y_m - second.y_m))
            if separation == 0.0:
                mutual = _collinear_disjoint_mutual(first, second)
            else:
                try:
                    mutual = parallel_finite_wire_mutual_inductance(first._segment(), second._segment())
                except ViaPeecError as exc:
                    raise GlobalMnaError(str(exc)) from exc
            geometry[first_index, second_index] = mutual
            geometry[second_index, first_index] = mutual
    minimum = float(np.min(np.linalg.eigvalsh(geometry)))
    if minimum < -max(float(np.max(np.abs(geometry))), _TINY) * 1.0e-11:
        raise GlobalMnaError("microvia PEEC partial-inductance matrix is not positive semidefinite")
    return FilledMicroviaDiagnostic(items, geometry)


def filled_microvia_branch_block(
    branches: Iterable[FilledMicroviaBranch], *, block_id: str = "filled-microvias"
) -> SeriesBranchBlock:
    """Rejected legacy adapter retained only for an actionable API failure.

    The old implementation stamped an isolated partial-inductance matrix as a
    passive series block.  That bypassed the plate-core ownership boundary and
    could double count spreading/return energy.  No argument, role string, or
    owner ID can make the old path valid.
    """

    del branches, block_id
    raise GlobalMnaError(
        "filled_microvia_branch_block is non-composable: isolated PEEC partial "
        "inductance cannot be stamped into global MNA without actual exact/core "
        "matrices, stable owner hashes, and verified subtraction; use "
        "inspect_filled_microvia_reference for diagnostic calculations"
    )


__all__ = [
    "DifferentialPort",
    "EquipotentialConstraint",
    "FilledMicroviaBranch",
    "FilledMicroviaDiagnostic",
    "GlobalMnaDiagnostics",
    "GlobalMnaError",
    "GlobalMnaOperator",
    "GlobalMnaSolveResult",
    "NodalAdmittanceBlock",
    "SeriesBranchBlock",
    "compile_global_mna",
    "filled_microvia_branch_block",
    "inspect_filled_microvia_reference",
    "solve_global_mna",
]
