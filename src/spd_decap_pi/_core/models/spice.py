"""Strict passive two-terminal SPICE subcircuit support.

Only linear R, L, C and mutual-inductance K statements are accepted.  The
restriction is deliberate: unsupported sources or nonlinear devices must not
be silently approximated in a deterministic PI evaluation.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re

import numpy as np
from numpy.typing import ArrayLike, NDArray

from .impedance import ImpedanceModelError, frequency_array


class SpiceModelError(ImpedanceModelError):
    """Raised for unsupported syntax, invalid values, or singular circuits."""


_NUMBER_RE = re.compile(
    r"^([+-]?(?:(?:\d+(?:\.\d*)?)|(?:\.\d+))(?:e[+-]?\d+)?)([a-z]+)?$",
    re.IGNORECASE,
)
_SCALE_FACTORS = {
    "t": 1e12,
    "g": 1e9,
    "meg": 1e6,
    "k": 1e3,
    "m": 1e-3,
    "u": 1e-6,
    "n": 1e-9,
    "p": 1e-12,
    "f": 1e-15,
}
_UNIT_REMAINDERS = {"", "f", "farad", "farads", "h", "henry", "henries", "r", "ohm", "ohms"}


def parse_spice_number(token: str) -> float:
    """Parse a numeric SPICE literal using standard case-insensitive suffixes."""

    match = _NUMBER_RE.fullmatch(token.strip())
    if match is None:
        raise SpiceModelError(f"unsupported SPICE numeric literal {token!r}")
    base = float(match.group(1))
    suffix = (match.group(2) or "").lower()
    if not suffix:
        return base

    if suffix.startswith("meg"):
        scale_key = "meg"
    else:
        scale_key = suffix[0]

    if scale_key in _SCALE_FACTORS:
        remainder = suffix[len(scale_key) :]
        if remainder not in _UNIT_REMAINDERS:
            raise SpiceModelError(f"unsupported SPICE suffix in {token!r}")
        return base * _SCALE_FACTORS[scale_key]

    if suffix in _UNIT_REMAINDERS - {"f"}:
        return base
    raise SpiceModelError(f"unsupported SPICE suffix in {token!r}")


@dataclass(frozen=True, slots=True)
class Resistor:
    name: str
    positive: str
    negative: str
    resistance_ohm: float


@dataclass(frozen=True, slots=True)
class Capacitor:
    name: str
    positive: str
    negative: str
    capacitance_f: float


@dataclass(frozen=True, slots=True)
class Inductor:
    name: str
    positive: str
    negative: str
    inductance_h: float


@dataclass(frozen=True, slots=True)
class MutualCoupling:
    name: str
    first_inductor: str
    second_inductor: str
    coefficient: float


PassiveElement = Resistor | Capacitor | Inductor | MutualCoupling


@dataclass(frozen=True, slots=True)
class PassiveSubcircuitModel:
    """Normalized passive two-terminal R/L/C/K network solved by MNA."""

    model_id: str
    positive_terminal: str
    negative_terminal: str
    elements: tuple[PassiveElement, ...]
    source_hash: str

    def __post_init__(self) -> None:
        if not self.model_id:
            raise SpiceModelError("subcircuit model_id must not be empty")
        if self.positive_terminal == self.negative_terminal:
            raise SpiceModelError("the two external terminals must be distinct")
        if not self.elements:
            raise SpiceModelError("the subcircuit contains no passive elements")
        self._validate_network()

    @property
    def resistors(self) -> tuple[Resistor, ...]:
        return tuple(item for item in self.elements if isinstance(item, Resistor))

    @property
    def capacitors(self) -> tuple[Capacitor, ...]:
        return tuple(item for item in self.elements if isinstance(item, Capacitor))

    @property
    def inductors(self) -> tuple[Inductor, ...]:
        return tuple(item for item in self.elements if isinstance(item, Inductor))

    @property
    def couplings(self) -> tuple[MutualCoupling, ...]:
        return tuple(item for item in self.elements if isinstance(item, MutualCoupling))

    def _validate_network(self) -> None:
        names: set[str] = set()
        for element in self.elements:
            if element.name in names:
                raise SpiceModelError(f"duplicate element name {element.name!r}")
            names.add(element.name)
            if isinstance(element, Resistor) and element.resistance_ohm <= 0.0:
                raise SpiceModelError(f"{element.name}: resistance must be > 0")
            if isinstance(element, Capacitor) and element.capacitance_f <= 0.0:
                raise SpiceModelError(f"{element.name}: capacitance must be > 0")
            if isinstance(element, Inductor) and element.inductance_h <= 0.0:
                raise SpiceModelError(f"{element.name}: inductance must be > 0")

        inductors = {element.name: element for element in self.inductors}
        inductance_matrix = np.diag([item.inductance_h for item in self.inductors])
        index = {item.name: i for i, item in enumerate(self.inductors)}
        coupled_pairs: set[tuple[str, str]] = set()
        for coupling in self.couplings:
            if coupling.first_inductor not in inductors or coupling.second_inductor not in inductors:
                raise SpiceModelError(
                    f"{coupling.name}: K statement references an unknown inductor"
                )
            if coupling.first_inductor == coupling.second_inductor:
                raise SpiceModelError(f"{coupling.name}: an inductor cannot couple to itself")
            if not -1.0 <= coupling.coefficient <= 1.0:
                raise SpiceModelError(f"{coupling.name}: coupling coefficient must be in [-1, 1]")
            pair = tuple(sorted((coupling.first_inductor, coupling.second_inductor)))
            if pair in coupled_pairs:
                raise SpiceModelError(f"{coupling.name}: duplicate coupling for {pair}")
            coupled_pairs.add(pair)
            first = inductors[coupling.first_inductor]
            second = inductors[coupling.second_inductor]
            mutual_h = coupling.coefficient * np.sqrt(first.inductance_h * second.inductance_h)
            i = index[first.name]
            j = index[second.name]
            inductance_matrix[i, j] = mutual_h
            inductance_matrix[j, i] = mutual_h

        if inductance_matrix.size:
            eigenvalues = np.linalg.eigvalsh(inductance_matrix)
            tolerance = max(float(np.max(np.diag(inductance_matrix))), 1.0) * 1e-12
            if float(eigenvalues.min()) < -tolerance:
                raise SpiceModelError(
                    "mutual-inductance matrix is not positive semidefinite; model is non-passive"
                )

    def impedance(self, frequencies_hz: ArrayLike) -> NDArray[np.complex128]:
        frequencies = frequency_array(frequencies_hz)
        ground = self.negative_terminal
        node_names = sorted(
            {
                node
                for item in self.elements
                if not isinstance(item, MutualCoupling)
                for node in (item.positive, item.negative)
                if node != ground
            }
            | {self.positive_terminal}
        )
        node_index = {node: index for index, node in enumerate(node_names)}
        inductors = self.inductors
        inductor_index = {item.name: index for index, item in enumerate(inductors)}
        node_count = len(node_names)
        branch_count = len(inductors)
        size = node_count + branch_count
        result = np.empty(frequencies.shape, dtype=np.complex128)

        incidence = np.zeros((node_count, branch_count), dtype=np.complex128)
        for column, inductor in enumerate(inductors):
            if inductor.positive != ground:
                incidence[node_index[inductor.positive], column] += 1.0
            if inductor.negative != ground:
                incidence[node_index[inductor.negative], column] -= 1.0

        inductance_matrix = np.diag([item.inductance_h for item in inductors]).astype(
            np.float64
        )
        for coupling in self.couplings:
            i = inductor_index[coupling.first_inductor]
            j = inductor_index[coupling.second_inductor]
            mutual_h = coupling.coefficient * np.sqrt(
                inductors[i].inductance_h * inductors[j].inductance_h
            )
            inductance_matrix[i, j] = mutual_h
            inductance_matrix[j, i] = mutual_h

        rhs = np.zeros(size, dtype=np.complex128)
        rhs[node_index[self.positive_terminal]] = 1.0
        for output_index, frequency in enumerate(frequencies):
            omega = 2.0 * np.pi * frequency
            matrix = np.zeros((size, size), dtype=np.complex128)
            for resistor in self.resistors:
                _stamp_admittance(
                    matrix,
                    node_index,
                    ground,
                    resistor.positive,
                    resistor.negative,
                    1.0 / resistor.resistance_ohm,
                )
            for capacitor in self.capacitors:
                _stamp_admittance(
                    matrix,
                    node_index,
                    ground,
                    capacitor.positive,
                    capacitor.negative,
                    1j * omega * capacitor.capacitance_f,
                )
            if branch_count:
                matrix[:node_count, node_count:] = incidence
                matrix[node_count:, :node_count] = incidence.T
                matrix[node_count:, node_count:] = -1j * omega * inductance_matrix
            try:
                solution = np.linalg.solve(matrix, rhs)
            except np.linalg.LinAlgError as exc:
                raise SpiceModelError(
                    f"{self.model_id}: singular passive network at {frequency:g} Hz; "
                    "check floating or disconnected internal nodes"
                ) from exc
            voltage = solution[node_index[self.positive_terminal]]
            if not np.isfinite(voltage.real) or not np.isfinite(voltage.imag):
                raise SpiceModelError(
                    f"{self.model_id}: non-finite impedance at {frequency:g} Hz"
                )
            result[output_index] = voltage
        return result


def _stamp_admittance(
    matrix: NDArray[np.complex128],
    node_index: dict[str, int],
    ground: str,
    positive: str,
    negative: str,
    admittance: complex,
) -> None:
    if positive != ground:
        i = node_index[positive]
        matrix[i, i] += admittance
    if negative != ground:
        j = node_index[negative]
        matrix[j, j] += admittance
    if positive != ground and negative != ground:
        i = node_index[positive]
        j = node_index[negative]
        matrix[i, j] -= admittance
        matrix[j, i] -= admittance


def _logical_lines(text: str) -> list[tuple[int, str]]:
    logical: list[tuple[int, str]] = []
    for line_number, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("*"):
            continue
        stripped = re.split(r"(?<!\\)[;$]", stripped, maxsplit=1)[0].strip()
        if not stripped:
            continue
        if stripped.startswith("+"):
            if not logical:
                raise SpiceModelError(f"line {line_number}: continuation has no prior line")
            original_number, previous = logical[-1]
            logical[-1] = (original_number, previous + " " + stripped[1:].strip())
        else:
            logical.append((line_number, stripped))
    return logical


@dataclass(frozen=True, slots=True)
class _SubcircuitBlock:
    name: str
    terminals: tuple[str, str] | None
    element_lines: tuple[tuple[int, str], ...]
    declaration_line: int


def _subcircuit_blocks(
    text: str, *, source_name: str
) -> tuple[_SubcircuitBlock, ...]:
    """Split a library without requiring every declaration to be two-terminal."""

    blocks: list[_SubcircuitBlock] = []
    active_name: str | None = None
    active_terminals: tuple[str, str] | None = None
    active_lines: list[tuple[int, str]] = []
    declaration_line = 0

    for line_number, line in _logical_lines(text):
        tokens = line.split()
        directive = tokens[0].lower()
        if directive == ".subckt":
            if active_name is not None:
                raise SpiceModelError(f"line {line_number}: nested .SUBCKT is unsupported")
            if len(tokens) < 2:
                raise SpiceModelError(f"line {line_number}: .SUBCKT name is missing")
            active_name = tokens[1].upper()
            active_terminals = (
                (tokens[2].upper(), tokens[3].upper())
                if len(tokens) == 4
                else None
            )
            active_lines = []
            declaration_line = line_number
        elif directive == ".ends":
            if active_name is None:
                raise SpiceModelError(f"line {line_number}: .ENDS has no matching .SUBCKT")
            if len(tokens) > 2 or (len(tokens) == 2 and tokens[1].upper() != active_name):
                raise SpiceModelError(f"line {line_number}: .ENDS name does not match .SUBCKT")
            blocks.append(
                _SubcircuitBlock(
                    name=active_name,
                    terminals=active_terminals,
                    element_lines=tuple(active_lines),
                    declaration_line=declaration_line,
                )
            )
            active_name = None
            active_terminals = None
            active_lines = []
            declaration_line = 0
        elif active_name is not None:
            active_lines.append((line_number, line))
        elif directive not in {".end", ".title"}:
            raise SpiceModelError(
                f"line {line_number}: content outside a .SUBCKT is unsupported"
            )

    if active_name is not None:
        raise SpiceModelError(f"{source_name}: unterminated .SUBCKT {active_name}")
    if not blocks:
        raise SpiceModelError(f"{source_name}: no .SUBCKT declaration found")
    return tuple(blocks)


def parse_passive_subcircuit(
    text: str,
    *,
    subckt_name: str | None = None,
    source_name: str = "<memory>",
) -> PassiveSubcircuitModel:
    """Parse one passive two-terminal ``.SUBCKT`` from SPICE text.

    Unsupported devices and behavioral constructs raise :class:`SpiceModelError`
    with a conversion hint instead of being ignored.
    """

    blocks = _subcircuit_blocks(text, source_name=source_name)
    selected_name = subckt_name.upper() if subckt_name else None
    if selected_name is not None:
        matching = [block for block in blocks if block.name == selected_name]
        if not matching:
            raise SpiceModelError(f"{source_name}: subcircuit {subckt_name!r} was not found")
        if len(matching) != 1:
            raise SpiceModelError(
                f"{source_name}: duplicate subcircuit declaration {selected_name!r} is ambiguous"
            )
        selected = matching[0]
        if selected.terminals is None:
            raise SpiceModelError(
                f"line {selected.declaration_line}: selected subcircuit {selected.name!r} "
                "is not a two-terminal .SUBCKT"
            )
    else:
        matching = [block for block in blocks if block.terminals is not None]
        if not matching:
            raise SpiceModelError(f"{source_name}: no two-terminal .SUBCKT declaration found")
        if len(matching) == 1:
            selected = matching[0]
        else:
            names = ", ".join(block.name for block in matching)
            raise SpiceModelError(
                f"{source_name}: multiple subcircuits found ({names}); select subckt_name"
            )
    terminals = selected.terminals
    if terminals is None:  # pragma: no cover - guarded above for static narrowing
        raise SpiceModelError(f"{source_name}: selected subcircuit is not two-terminal")
    elements = tuple(
        _parse_element(line_number, line)
        for line_number, line in selected.element_lines
    )
    return PassiveSubcircuitModel(
        model_id=selected.name,
        positive_terminal=terminals[0],
        negative_terminal=terminals[1],
        elements=elements,
        source_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )


def passive_subcircuit_names(text: str, *, source_name: str = "<memory>") -> tuple[str, ...]:
    """Return declared two-terminal subcircuit names for an import chooser.

    Full passive-element validation remains the responsibility of
    :func:`parse_passive_subcircuit` after the user selects one declaration.
    """

    names: list[str] = []
    for block in _subcircuit_blocks(text, source_name=source_name):
        if block.terminals is None:
            continue
        name = block.name
        if name in names:
            raise SpiceModelError(
                f"{source_name}: duplicate .SUBCKT declaration {name!r} is ambiguous"
            )
        names.append(name)
    if not names:
        raise SpiceModelError(f"{source_name}: no two-terminal .SUBCKT declaration found")
    return tuple(names)


def _parse_element(line_number: int, line: str) -> PassiveElement:
    tokens = line.split()
    name = tokens[0].upper()
    kind = name[0]
    if kind in {"R", "L", "C"}:
        if len(tokens) != 4:
            raise SpiceModelError(
                f"line {line_number}: {kind} statement must be NAME NODE+ NODE- VALUE"
            )
        positive, negative = tokens[1].upper(), tokens[2].upper()
        if positive == negative:
            raise SpiceModelError(f"line {line_number}: element terminals must be distinct")
        value = parse_spice_number(tokens[3])
        if not np.isfinite(value) or value <= 0.0:
            raise SpiceModelError(f"line {line_number}: passive element value must be > 0")
        if kind == "R":
            return Resistor(name, positive, negative, value)
        if kind == "L":
            return Inductor(name, positive, negative, value)
        return Capacitor(name, positive, negative, value)
    if kind == "K":
        if len(tokens) != 4:
            raise SpiceModelError(
                f"line {line_number}: K statement must be NAME L1 L2 COEFFICIENT"
            )
        coefficient = parse_spice_number(tokens[3])
        return MutualCoupling(name, tokens[1].upper(), tokens[2].upper(), coefficient)

    raise SpiceModelError(
        f"line {line_number}: unsupported element {tokens[0]!r}; only passive R/L/C/K "
        "is accepted. Convert the model to series RLC, sampled Z CSV, or Touchstone."
    )


def parse_passive_subcircuit_file(
    path: str,
    *,
    subckt_name: str | None = None,
    encoding: str = "utf-8-sig",
) -> PassiveSubcircuitModel:
    with open(path, "r", encoding=encoding) as handle:
        return parse_passive_subcircuit(
            handle.read(), subckt_name=subckt_name, source_name=path
        )
