"""Compare saved100MHz DC/internal fields on the existing source-group inventory."""
import argparse
import json
from pathlib import Path

import numpy as np

import census_astra_l14_l25_sheet_field_redistribution as census
from probe_astra_native_loaded_voltage_field import base, _start_shutdown_safe_watchdog
from reconstruct_astra_native_loaded_field import _atomic_exclusive_json, _sha256_file

R = census.RESEARCH
PINS = {name:census.PINNED[name] for name in ("baseline_raw_field","l25_drive_npz","l14_footprint","l14_ledger","target_rail_ranking","l14_mesh_contact_order")}
PINS |= {
    "census_helper": (Path(census.__file__),"60598d753cc02e0cb36296f8328aebd434cb1560e61778c7b4fa4617fead7c3c"),
    "dc": (R / "astra-two-sheet-frequency-shadow-01/result.json","f580a47697385efdd234f6c8738119285bc3cac94a4ad1827ffd5e7b449f4372"),
    "internal": (R / "astra-internal-sheet-100mhz-shadow-01/result.json","36613cde0c127bae3d7af50d9bc8e53296e0538ff473152e04ea591d23e5c93b"),
}


def run(output,budget):
    inputs = {name:census.pin(path,expected) for name,(path,expected) in PINS.items()}
    docs = {name:json.loads(path.read_text(encoding="utf-8")) for name,(path,_) in PINS.items() if path.suffix == ".json"}
    points = {name:next(p for p in docs[name]["points"] if p["frequency_hz"] == 1e8) for name in ("dc","internal")}
    fields = {}
    for name,point in points.items():
        assert docs[name]["rail_id"] == "ADC_VDD_075_VTRIP_SRAM/0"
        path = Path(point["field"]["path"])
        assert _sha256_file(path) == point["field"]["sha256"]
        with np.load(path,allow_pickle=False) as saved:
            fields[name] = {key:saved[key] for key in ("active_voltage","l14_sheet_active_indices","sheet_active_indices")}
        v = fields[name]["active_voltage"]
        assert len(v) == 1436468 and np.all(np.isfinite(v)) and v[2699]-v[2656] == complex(*point["zdd_ohm"])
    assert all(np.array_equal(fields["dc"][key],fields["internal"][key]) for key in ("l14_sheet_active_indices","sheet_active_indices"))
    with np.load(PINS["baseline_raw_field"][0],allow_pickle=False) as raw, np.load(PINS["l25_drive_npz"][0],allow_pickle=False) as drive, np.load(PINS["l14_mesh_contact_order"][0],allow_pickle=False) as mesh:
        first,second = raw["finite_first_active_indices"],raw["finite_second_active_indices"]
        y = raw["finite_count"]/(raw["finite_resistance_ohm_per_via"]+2j*np.pi*1e8*raw["finite_inductance_h_per_via"])
        _,map14,check14 = census.build_l14_maps(first,second,census.packed(raw["all_finite_link_ids"]),raw["finite_active_original_indices"],docs["l14_ledger"],docs["l14_footprint"],json.loads(mesh["contacts_json_utf8"].tobytes()),fields["dc"]["l14_sheet_active_indices"],fields["dc"]["l14_sheet_active_indices"])
        map25,check25 = census.build_l25_map(first,second,{key:drive[key] for key in drive.files},fields["dc"]["sheet_active_indices"])
    relocated_first,relocated_second = first.copy(),second.copy()
    for target,mapping in ((census.L14_TARGET,map14),(census.L25_TARGET,map25)):
        for index,node in mapping.items():
            if first[index] == target: relocated_first[index] = node
            else:
                assert second[index] == target
                relocated_second[index] = node
    currents,powers = {},{}
    for name,field in fields.items():
        v = field["active_voltage"]
        dv = v[relocated_first]-v[relocated_second]
        currents[name] = y*dv
        power = np.sum(dv*np.conj(currents[name]))
        error = abs(power-complex(*points[name]["power_contributions_ohm"]["finite_via"]))
        assert np.all(np.isfinite(currents[name])) and error < 1e-12
        powers[name] = {"finite_complex_power_ohm":census.pair(power),"saved_category_difference_ohm":float(error)}
    budget.emit("saved_field_currents_verified",finite_links=len(y),power=powers)
    groups = []
    for row in docs["target_rail_ranking"]["groups"]:
        anchor = int(row["active_index"])
        indexes = np.flatnonzero((first == anchor)|(second == anchor))
        assert len(indexes) == row["finite_incident_count"]
        groups.append({"active_index":anchor,"source_components":row["source_components"],"source_layers":census.source_label(row["source_components"]),
            **{name:census.stage_metrics(indexes,anchor,current,first,second) for name,current in currents.items()}})
    result = {"program":"SPD Decap PI Evaluator","version":"0.23.1","status":"COMPLETED_SAVED_100MHZ_SOURCE_GROUP_FINITE_CURRENT_CENSUS",
        "rail_id":docs["dc"]["rail_id"],"frequency_hz":1e8,"inputs":inputs,"fields":{name:p["field"] for name,p in points.items()},
        "mapping":{"l14":check14,"l25":check25},"finite_power":powers,"groups":groups,
        "resource":{"elapsed_s":budget.elapsed(),"peak_private_bytes":budget.peak_private},"script_sha256":_sha256_file(Path(__file__)),
        "limitations":["Original1MHz inventory supplies source identities and incidence counts only; both current vectors are evaluated at100MHz with original R/L.",
            "Inventory covers its saved source groups, not all board conductors. Half-sum magnitudes are finite-via throughput, not cut currents, unique routes or magnetic return closure.",
            "No geometry, LU, native compilation, PowerSI fitting or new physical model."]}
    _atomic_exclusive_json(output / "result.json",result)
    print(json.dumps({"status":result["status"],"top_groups":sorted(groups,key=lambda g:g["internal"]["half_sum_abs_a"],reverse=True)[:8]}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("--output",type=Path,required=True); args = parser.parse_args()
    args.output = args.output.resolve(); args.output.mkdir(exist_ok=False)
    (args.output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    budget = base._Budget(120.,8*2**30,args.output / "progress.jsonl"); watchdog = _start_shutdown_safe_watchdog(budget)
    try: run(args.output,budget)
    finally:
        budget.stop.set(); watchdog.join(timeout=2.)
