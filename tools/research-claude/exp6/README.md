# EXP-6 — void / perforation audit on the frozen adopted model

`run6.py` (imports the EXP-5 `pipeline.ModelB`, i.e. S2 + (b), frozen):

- `--port P --stats`: counts voids per layer (rail planes of the port and the DGND planes L02/L13/L16/L18/L24/L27), gives the equivalent-diameter bins, and computes the removed-area fraction. Tagged SmallHole voids: none in 260729.
- `--port P`: V0 (all voids).
- `--port P --dmax D`: V3 diagnostic, which fills every rail-plane void with equivalent diameter < D µm.

`runall6.sh` runs port 18 (stats, V0, D = 200/400/1500). `runall6b.sh` runs ports 1 and 19 (stats, V0, D = 400/1500).
Report: `$SPD_PI_WORK_DIR/exp6/EXP6_REPORT.md`.
