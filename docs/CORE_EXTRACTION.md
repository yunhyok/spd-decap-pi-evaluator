# Embedded evaluation core

This repository is operationally independent from `probe-card-mlo-pdn`.
It does not install, import, clone, or update that application at runtime.

The package under `spd_decap_pi._core` is an internal snapshot of only the
calculation and import foundation needed by this program. The legacy CLI and
GUI entry points were deliberately removed. The only installed application
entry point is `spd-decap-pi-evaluator`.

The embedded core currently preserves the validated rectangular plane-pair
Evaluation boundary: one selected PWR rail at a time, continuous DGND, and no
inter-rail/site transfer coupling. Optimization APIs are not exposed by this
program or its GUI.

