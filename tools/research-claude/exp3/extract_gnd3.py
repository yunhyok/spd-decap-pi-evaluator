"""EXP-3 S3: DGND Node/Via/Trace over the whole rail-plane extent (+2 mm), TOP..L28."""
import pickle, sys, os
import numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "exp2"))
from extract_gnd import extract_gnd
ex = pickle.load(open("/home/claude/work/exp1/extract_port18.pkl", "rb"))
pts = np.vstack([p.reshape(-1, 2) for g in ex["rail_geoms"] for p in g["pos_polys"]])
rn = ex["rail_nodes"]
pts = np.vstack([pts, [(v[0], v[1]) for v in ex["gnd_port_nodes"].values()]])
m = 2000.0
window = (float(pts[:, 0].min() - m), float(pts[:, 1].min() - m), float(pts[:, 0].max() + m), float(pts[:, 1].max() + m))
names = [r["name"] for r in ex["stackup"] if r["conductivity"] is not None]
layers = names[: names.index("Signal$L28(DGND)") + 1]
g = extract_gnd(ex["spd_path"], window, layers)
pickle.dump(g, open("/home/claude/work/exp3/extract_gnd3.pkl", "wb"))
print("window", window, len(g["nodes"]), len(g["vias"]), len(g["traces"]))
