"""SPD Decap PI Evaluator v0.23.1: saved group-correction02 independent review."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
from time import monotonic
import traceback

import numpy as np
from qualify_astra_tetra_volume_green import tetra_pair

ROOT = Path(__file__).resolve().parents[2]
PINS = {
    "outputs/research/astra-xg7-jac27-group-correction-02/driver-at-run.py": "05b0de992fba40dc8e2b2148795ba12103093d2279fcbeb056e76d505c5119c5",
    "outputs/research/astra-xg7-jac27-group-correction-02/group-correction.npz": "ca0c1d600316fd704aff4e741f956a36eed9c1fd224d10f1384c3aaf17ce9c44",
    "outputs/research/astra-xg7-jac27-group-correction-02/pair-records.json": "823b715b2b2cb4035f7625fe2503f1f7850652ac33086053dfa122b4af3b2a91",
    "outputs/research/astra-compact-rule-static-matrix-01/static-matrices.npz": "2b739de10382dbeb1cb327948d4d5f8b540cfc61944e92788ad8093f5af9de45",
    "outputs/research/astra-compact-rule-static-matrix-02/static-matrices.npz": "6cab19f0cf9e906e123f6aef8dfd39b5c82e4e496907ea85ead7f3f365d816dd",
    "outputs/research/astra-boundary-conforming-power-joint-01/joint-template.npz": "14b927da78b3cbf9d68f49c89b84dbeda746a99cef0bc6d9bcefd597f9db49eb",
    "outputs/research/astra-boundary-joint-current-space-01/current-space.npz": "2e356ee882623509cec9d62879283092a230c652ea4a74576076a695d39483f5",
    "outputs/research/astra-lift-static-self-matrix-01/static-matrices.npz": "dc75659efcba65119682b2f3b5ba540a65a5babf3699d631a42bc7d70e6dfa95",
}


def digest(path): return sha256(path.read_bytes()).hexdigest()

def rigid(reference, target):
    rc, tc = reference.mean(0), target.mean(0)
    u, _, vt = np.linalg.svd((reference - rc).T @ (target - tc))
    rot = u @ vt
    if np.linalg.det(rot) < 0:
        u[:, -1] *= -1; rot = u @ vt
    shift = tc - rc @ rot
    return float(np.max(np.linalg.norm(reference @ rot + shift - target, axis=1)) / max(np.ptp(reference, axis=0).max(), 1e-300))

def weighted(tetra, local, bary, weights):
    points = np.einsum("pa,cad->cpd", bary, tetra, optimize=True)
    return points, np.einsum("cim,cpid->cpmd", local, points[:, :, None] - tetra[:, None], optimize=True) * weights[None, :, None, None] / 3.0

def blocks(a, b, points, weight, chunk=64):
    out = np.empty((len(a), 5, 5))
    for first in range(0, len(a), chunk):
        sl = slice(first, min(len(a), first + chunk)); aa, bb = a[sl], b[sl]
        distance = np.linalg.norm(points[aa, :, None] - points[bb, None, :], axis=3)
        assert np.all(distance > 0)
        pot = np.einsum("bpq,bqnd->bpnd", 1/distance, weight[bb], optimize=True)
        fwd = 1e-7*np.einsum("bpmd,bpnd->bmn", weight[aa], pot, optimize=True)
        out[sl] = fwd + fwd.transpose(0,2,1)
    return out

def run(output):
    start, arrays = monotonic(), {}
    output.mkdir(parents=False, exist_ok=False)
    (output/"driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    try:
        for path, pin in PINS.items(): assert digest(ROOT/path) == pin, path
        with np.load(ROOT/"outputs/research/astra-xg7-jac27-group-correction-02/group-correction.npz", allow_pickle=False) as d:
            saved = {k:d[k] for k in d.files}
        with np.load(ROOT/"outputs/research/astra-compact-rule-static-matrix-01/static-matrices.npz", allow_pickle=False) as d:
            jac0, jb, jw, olda, oldb = d["jacobi_corrected_matrix"], d["jacobi_rule_barycentric"], d["jacobi_rule_weights"], d["corrected_pair_a"], d["corrected_pair_b"]
        with np.load(ROOT/"outputs/research/astra-compact-rule-static-matrix-02/static-matrices.npz", allow_pickle=False) as d:
            xg0, xb, xw = d["xiao_gimbutas7_corrected_matrix"], d["xiao_gimbutas7_rule_barycentric"], d["xiao_gimbutas7_rule_weights"]
        with np.load(ROOT/"outputs/research/astra-boundary-conforming-power-joint-01/joint-template.npz", allow_pickle=False) as d:
            tetra = (d["vertices_local_um"]*1e-6)[d["cells"].astype(np.int64)]
        with np.load(ROOT/"outputs/research/astra-boundary-joint-current-space-01/current-space.npz", allow_pickle=False) as d:
            cols, signs = d["local_rt0_face_columns"], d["local_rt0_face_signs"]
        with np.load(ROOT/"outputs/research/astra-lift-static-self-matrix-01/static-matrices.npz", allow_pickle=False) as d:
            current = d["whitened_face_currents"]
        a, b = saved["member_a"].astype(np.int64), saved["member_b"].astype(np.int64)
        assert len(a) == 40928 and np.all(saved["member_is_new"])
        old = {(int(x),int(y)) for x,y in zip(olda,oldb)}
        assert not any((int(x),int(y)) in old for x,y in zip(a,b)) and len(set(zip(a,b))) == len(a)
        local = signs[:,:,None]*current[cols]
        pj,wj=weighted(tetra,local,jb,jw); px,wx=weighted(tetra,local,xb,xw)
        independent_j, independent_x = blocks(a,b,pj,wj), blocks(a,b,px,wx)
        point_scale=max(float(np.linalg.norm(xg0)),1e-300)
        point_j_err=float(np.linalg.norm(independent_j-saved["point_pair_jac27"])/point_scale)
        point_x_err=float(np.linalg.norm(independent_x-saved["point_pair_xg31"])/point_scale)
        assert point_j_err < 2e-12 and point_x_err < 2e-12
        # Every member is checked against its canonical ordered-eight map and raw H.
        key={(int(k),int(g)):i for i,(k,g) in enumerate(zip(saved["canonical_kind"],saved["canonical_group"]))}
        rigid_err=[]; projection_err=[]
        for i,(kind,group,aa,bb) in enumerate(zip(saved["member_kind"],saved["member_group"],a,b)):
            ci=key[(int(kind),int(group))]; ca,cb=saved["canonical_a"][ci],saved["canonical_b"][ci]
            rigid_err.append(rigid(np.r_[tetra[ca],tetra[cb]],np.r_[tetra[aa],tetra[bb]]))
            h=saved["canonical_forward_reverse_h"][ci,0]
            projected=local[aa].T@h@local[bb]; projected+=projected.T
            projection_err.append(np.linalg.norm(projected-saved["projected_exact_blocks"][i]))
        rigid_max=float(max(rigid_err)); projection_max=float(max(projection_err))
        assert rigid_max < 1e-12 and projection_max < 2e-20
        exact=saved["projected_exact_blocks"]
        jac=jac0-independent_j.sum(0)+exact.sum(0); xg=xg0-independent_x.sum(0)+exact.sum(0)
        jac_err=float(np.linalg.norm(jac-saved["corrected_jac27_matrix"])/point_scale); xg_err=float(np.linalg.norm(xg-saved["corrected_xg31_matrix"])/point_scale)
        full=xg0-jac0; signed=xg-jac
        replay=float(np.linalg.norm(signed-(full-(independent_x-independent_j).sum(0)))/point_scale)
        assert jac_err < 2e-12 and xg_err < 2e-12 and replay < 2e-12
        E=np.linalg.inv(np.linalg.cholesky((jac0+jac0.T)/2)); sym=float(np.linalg.norm(xg-xg.T)/np.linalg.norm(xg)); pd=float(np.linalg.eigvalsh((xg+xg.T)/2).min())
        last_l1=float(sum(np.linalg.norm(E@q@E.T,2) for q in saved["last_step_projected_change"]))
        assert sym < 1e-10 and pd > 0
        # Only the eight worst saved canonical references are reintegrated at 2x final order.
        quality=np.maximum(saved["canonical_change"],saved["canonical_reciprocity"]); worst=np.argsort(quality)[-8:][::-1]
        impact=[]; ref_rows=[]
        for ci in worst:
            ca,cb=int(saved["canonical_a"][ci]),int(saved["canonical_b"][ci]); final=int(saved["canonical_order"][ci])*2
            hf=tetra_pair(tetra[ca],tetra[cb],final); hr=tetra_pair(tetra[cb],tetra[ca],final)
            q=float(np.linalg.norm(hf-hr.T)/max(np.linalg.norm(hf),1e-300))
            mask=(saved["member_kind"]==saved["canonical_kind"][ci])&(saved["member_group"]==saved["canonical_group"][ci])
            member_impact=np.zeros((5,5))
            for aa,bb in zip(a[mask],b[mask]):
                delta=local[aa].T@(hf-saved["canonical_forward_reverse_h"][ci,0])@local[bb]; member_impact+=delta+delta.T
            impact.append(member_impact); ref_rows.append((int(ci),ca,cb,final,q,int(mask.sum())))
            assert q < 5e-5
        arrays=dict(independent_point_jac27=independent_j, independent_point_xg31=independent_x, rigid_relative=np.asarray(rigid_err), projection_absolute=np.asarray(projection_err),
                    independently_corrected_jac27=jac, independently_corrected_xg31=xg, independently_signed_delta=signed, fixed_jac_scale=E,
                    worst_canonical_index=worst, worst_final_order_records=np.asarray(ref_rows), worst_member_impact=np.asarray(impact))
        metrics=dict(member_count=int(len(a)), old_corrected_overlap=0, rigid_map_max_relative=rigid_max, raw_h_projection_max_absolute=projection_max,
                     point_jac_relative_fullL=point_j_err, point_xg_relative_fullL=point_x_err, jac_matrix_relative_fullL=jac_err, xg_matrix_relative_fullL=xg_err,
                     signed_replay_relative_fullL=replay, fixed_jac_spectral=float(np.linalg.norm(E@signed@E.T,2)), fixed_jac_frobenius=float(np.linalg.norm(E@signed@E.T)),
                     raw_symmetry=sym, pd_min_eigenvalue=pd, last_step_allmember_l1_fixed_jac=last_l1, worst_final_order_records=ref_rows,
                     worst_impact_fixed_jac_spectral=[float(np.linalg.norm(E@q@E.T,2)) for q in impact])
        status,failure="PASS_SAVED_GROUP_CORRECTION02_REVIEW",None
    except Exception:
        metrics,status,failure={},"STOP_SAVED_GROUP_CORRECTION02_REVIEW",traceback.format_exc()
    np.savez_compressed(output/"review.npz",**arrays)
    result=dict(program="SPD Decap PI Evaluator",version="0.23.1",status=status,failure=failure,pins=PINS,metrics=metrics,elapsed_s=monotonic()-start,
                driver_sha256=digest(Path(__file__)),artifact_sha256=digest(output/"review.npz"),budget={"deadline_s":180,"memory_gib":2},
                scope="Saved group-correction02 local vector-pair review. Eight only final-order refinements are diagnostic propagation, not a global exact-near claim; no FMM, scalar, charge, field, port, or board claim.")
    (output/"result.json").write_text(json.dumps(result,indent=2,allow_nan=False)+"\n",encoding="utf-8")
    print(json.dumps(result,allow_nan=False)); return 0 if failure is None else 2

if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--output",required=True,type=Path);raise SystemExit(run(p.parse_args().output.resolve()))
