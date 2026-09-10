"""Compare the frozen junction-only response with the existing1MHz reference."""
import hashlib
import json
from pathlib import Path
import numpy as np
import compare_astra_loaded_development_points as comparison

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
PRIMARY = ROOT / "docs/evaluation-research/astra_primary_band_comparison_2026-09-07.json"
PINS = {HERE / "result.json": "24b607363552baa6518f79f972335da378e833d852e8290c7659e07fba7b541c",
        PRIMARY: "ac08d7ea36a09989fb83e9113af399db41f698bf68e061e3c75020f4c41a5f8d",
        Path(comparison.__file__): "0e12464d1297caa9964404a2a0777d22964d6817069f752641b2d7945c8f4f4f"}
def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()

assert comparison.errors(1+2j,1+2j)["complex_relative_error"] == 0
for path, expected in PINS.items():
    assert sha(path) == expected
actual = json.loads((HERE / "result.json").read_bytes())
assert actual["status"] == "COMPLETED_CONDITIONAL_L02_IDEAL_JUNCTION_SHADOW"
assert actual["frequency_hz"] == 1e6
assert sha(HERE / "driver-at-run.py") == actual["script_sha256"]
point = actual["point"]
assert max(point["physical_residual_max_abs_a"],point["csc_matvec_residual_max_abs_a"]) < 1e-7
assert point["power_closure_error_ohm"] < 1e-7*abs(complex(*point["zdd_ohm"]))
field = Path(point["field"]["path"])
assert sha(field) == point["field"]["sha256"]
with np.load(field, allow_pickle=False) as data:
    v = data["active_voltage"]
    assert v.shape == (756889,) and np.isfinite(v).all()
    changed = complex(v[2699]-v[2656])
assert changed == complex(*point["zdd_ohm"])
primary = json.loads(PRIMARY.read_bytes())
assert primary["rail_id"] == comparison.RAIL and primary["reference_port_one_based"] == 18
reference_row, = [row for row in primary["points"] if row["frequency_hz"] == 1e6]
reference = complex(*reference_row["reference_zdd_ohm"])
baseline = complex(*actual["assembly"]["original_zdd_ohm"])
rows = [{"model":name,"zdd_ohm":[z.real,z.imag],"errors":comparison.errors(z,reference)}
        for name,z in (("original_ideal_sheet_native",baseline),("conditional20_l02_junctions",changed))]
result = {"program":"SPD Decap PI Evaluator","version":"0.23.1","status":"COMPLETED_POST_FREEZE_IDEAL_L02_JUNCTION_COMPARISON",
          "inputs":{str(path):digest for path,digest in PINS.items()},"script_sha256":sha(Path(__file__)),
          "rail_id":comparison.RAIL,"frequency_hz":1e6,"reference_port_one_based":18,"reference_zdd_ohm":[reference.real,reference.imag],
          "points":rows,"absolute_change_ohm":abs(changed-baseline),"change_over_original_reference_gap":abs(changed-baseline)/abs(baseline-reference),
          "scope":"Post-freeze original ideal-sheet topology discriminator only. No fitted input, finite L02 sheet, L14/L25 composition, broadband or PowerSI accuracy acceptance. The small observed change is not an error bound; independent solve/reconstruction roundoff remains explicit."}
with (HERE / "post-freeze-comparison.json").open("x",encoding="utf-8") as stream:
    json.dump(result,stream,indent=2,allow_nan=False)
print(json.dumps(result))
