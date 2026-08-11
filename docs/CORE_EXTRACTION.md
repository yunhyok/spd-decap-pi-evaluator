# Embedded evaluation core

This repository is operationally independent from `probe-card-mlo-pdn`.
It does not install, import, clone, or update that application at runtime.

The package under `spd_decap_pi._core` is an internal snapshot of only the
calculation and import foundation needed by this program. The legacy CLI and
GUI entry points were deliberately removed. The only installed application
entry point is `spd-decap-pi-evaluator`.

The embedded core preserves the single-selected-PWR-rail and no inter-rail/site
transfer-coupling boundary. v0.22.0 uses the source-derived layer-surface
compiler: exact adjacent-gap artwork Maxwell-Y blocks share physical
`(layer, NET)` nodes and exact same-NET Trace/Via components are eliminated by
one global sparse Schur/Kron solve. For the terminal-complete external Device
port, that global-Y Zii is the sole input: the legacy rectangular modal matrix
is not prepared and no higher-mode one-port difference is added. Optimization
APIs are not exposed by this program or its GUI. The explicit Research and
Legacy profiles retain their separately documented modal boundaries.
