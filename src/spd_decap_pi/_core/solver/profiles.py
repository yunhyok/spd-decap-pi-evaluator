"""Immutable identities for guarded Evaluation solver profiles."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json


PROFILE_IDENTITY_FORMAT = "solver-profile-identity-v1"


class SolverProfileError(ValueError):
    """Raised when an unknown or malformed solver profile is requested."""


@dataclass(frozen=True, slots=True)
class SolverProfile:
    """Stable, cache-safe identity and user-facing disclosure for one backend."""

    key: str
    label: str
    badge: str
    description: str
    experimental: bool
    compiler_algorithm_id: str

    def __post_init__(self) -> None:
        for name in ("key", "label", "badge", "description", "compiler_algorithm_id"):
            if not str(getattr(self, name)).strip():
                raise SolverProfileError(f"solver profile {name} must not be blank")
        if self.key != self.key.strip().lower() or any(
            character not in "abcdefghijklmnopqrstuvwxyz0123456789_"
            for character in self.key
        ):
            raise SolverProfileError(
                "solver profile key must use lowercase ASCII letters, digits, and underscores"
            )


LEGACY_MODAL_PROFILE = SolverProfile(
    key="legacy_modal_v017",
    label="Legacy modal",
    badge="LEGACY",
    description=(
        "v0.17 rectangular finite-port modal solver; retained as the explicit "
        "compatibility and rollback path"
    ),
    experimental=False,
    compiler_algorithm_id="legacy-modal-v017-regression",
)

RESEARCH_UNIFORM_ADMITTANCE_PROFILE = SolverProfile(
    key="research_uniform_admittance",
    label="Research: actual-artwork uniform mode",
    badge="RESEARCH",
    description=(
        "source-only exact-artwork adjacent-gap uniform C00 replacement; "
        "experimental, fail-closed, and not validated for sign-off"
    ),
    experimental=True,
    compiler_algorithm_id="research-uniform-c00-source-only-v2",
)

LAYERWISE_ADMITTANCE_PROFILE = SolverProfile(
    key="layerwise_admittance_v1",
    label="Layer-surface terminal-complete network",
    badge="LAYERWISE",
    description=(
        "independent physical layer/net surfaces with exact adjacent-artwork Maxwell Y, "
        "source-proven finite Via links and exact same-layer Trace connectivity, "
        "all mounted decap terminations, and one global Schur/Kron reduction at "
        "the external Device port; legacy rectangular higher-mode one-port "
        "differences are not mixed into this terminal-complete passive result"
    ),
    experimental=False,
    compiler_algorithm_id=(
        "layer-surface-adjacent-y-island-finite-via-termination-kron-v8"
    ),
)

SOLVER_PROFILES = (
    LEGACY_MODAL_PROFILE,
    LAYERWISE_ADMITTANCE_PROFILE,
    RESEARCH_UNIFORM_ADMITTANCE_PROFILE,
)
# Low-level dataclass/API defaults stay legacy so older callers and persisted
# results do not suddenly require artwork attachments. The desktop product
# deliberately selects the validated layerwise profile through the separate
# application default below.
DEFAULT_SOLVER_PROFILE_KEY = LEGACY_MODAL_PROFILE.key
APPLICATION_DEFAULT_SOLVER_PROFILE_KEY = LAYERWISE_ADMITTANCE_PROFILE.key
_BY_KEY = {profile.key: profile for profile in SOLVER_PROFILES}


def solver_profile(value: str | SolverProfile | None = None) -> SolverProfile:
    """Resolve a public profile identity without silently selecting research."""

    if value is None:
        return LEGACY_MODAL_PROFILE
    if isinstance(value, SolverProfile):
        registered = _BY_KEY.get(value.key)
        if registered != value:
            raise SolverProfileError(
                f"solver profile {value.key!r} is not a registered immutable identity"
            )
        return registered
    key = str(value).strip()
    try:
        return _BY_KEY[key]
    except KeyError as exc:
        choices = ", ".join(profile.key for profile in SOLVER_PROFILES)
        raise SolverProfileError(
            f"unknown solver profile {key!r}; choose one of: {choices}"
        ) from exc


def solver_profile_static_identity_sha256(
    value: str | SolverProfile | None = None,
) -> str:
    """Return the immutable compiler/algorithm identity for a registered profile.

    A source-derived profile cannot safely reuse an old result merely because a
    source layout hash happens to match: the source compiler and the algorithm
    that interprets it are both part of the numerical identity. Legacy keeps
    its historical cache settings intentionally; source-derived production and
    research callers include this value in their cache identity.
    """

    profile = solver_profile(value)
    payload = {
        "format": PROFILE_IDENTITY_FORMAT,
        "key": profile.key,
        "compiler_algorithm_id": profile.compiler_algorithm_id,
    }
    return sha256(
        (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode(
            "utf-8"
        )
    ).hexdigest()


__all__ = [
    "APPLICATION_DEFAULT_SOLVER_PROFILE_KEY",
    "DEFAULT_SOLVER_PROFILE_KEY",
    "LEGACY_MODAL_PROFILE",
    "LAYERWISE_ADMITTANCE_PROFILE",
    "PROFILE_IDENTITY_FORMAT",
    "RESEARCH_UNIFORM_ADMITTANCE_PROFILE",
    "SOLVER_PROFILES",
    "SolverProfile",
    "SolverProfileError",
    "solver_profile",
    "solver_profile_static_identity_sha256",
]
