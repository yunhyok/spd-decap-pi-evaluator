import sys, pickle, json, time, resource
import numpy as np
from scipy.sparse.linalg import splu
sys.argv = sys.argv
import exp2 as E
if len(sys.argv) > 6 and sys.argv[6] == "all":
    E.EXPLICIT = E.EXPLICIT_ALLGND
h, fh, th, spec, dec = float(sys.argv[1]), float(sys.argv[2]), float(sys.argv[3]), sys.argv[4], sys.argv[5] == "1"
ex = pickle.load(open("/home/claude/work/exp1/extract_port18.pkl", "rb"))
gnd = pickle.load(open("/home/claude/work/exp2/extract_gnd.pkl", "rb"))
shapes = pickle.load(open("/home/claude/work/exp1/neighbour_shapes.pkl", "rb"))
need = [L for L, _ in E.EXPLICIT if L not in shapes]
if need:
    from exp1b import load_layer_shapes
    shapes.update(load_layer_shapes(ex["spd_path"], need)); pickle.dump(shapes, open("/home/claude/work/exp1/neighbour_shapes.pkl", "wb"))
fbox = json.load(open("/home/claude/work/exp1/result.json"))["discretisation"]["fine_box_um"]
b = E.Builder(ex, gnd, shapes, h, fh, th, gnd["window"], fbox, verbose=False, decouple_groups=dec, gauge=(sys.argv[7] if len(sys.argv) > 7 else "bottom"))
b.build()
print({k: v for k, v in b.info.items() if k not in ("reference_search_series_layers",)}, flush=True)
S = E.Solver(b)
Y = S.assemble(1e6)
print("N", Y.shape[0], "nnz", Y.nnz, flush=True)
t = time.time()
lu = splu(Y, permc_spec=spec)
print("LU nnz", lu.nnz, "t", time.time() - t, "rss", resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024)
rhs = np.zeros(b.N, complex); rhs[b.P] = 1
V = lu.solve(rhs); Vx = np.append(V, 0)
num = 0; den = 0
for c, (lay, sel) in b.patterns.items():
    Yp = S.colY[c]
    dV = np.stack([Vx[np.where(b.map(b.colA[k, sel]) < 0, b.N, b.map(b.colA[k, sel]))] - Vx[np.where(b.map(b.colB[k, sel]) < 0, b.N, b.map(b.colB[k, sel]))] for k in lay])
    I = (Yp @ dV) / b.colSQ[sel]
    num += np.sum(np.abs(I.sum(0)) ** 2); den += np.sum(np.abs(I) ** 2)
print("net-column-current fraction sum|sumI|^2 / sum|I|^2 =", num / den)
z = lu.solve(rhs)[b.P]; print("Z", z)
ref = np.load("/home/claude/data/S4LB002_260729_Zdiag.npz", allow_pickle=True); zr = ref["Zdiag"][list(ref["freq"]).index(1e6), 17]
print("rel err", abs(z - zr) / abs(zr), "dRe mOhm", (z - zr).real * 1e3, "dL pH", (z - zr).imag / (2 * np.pi * 1e6) * 1e12)
