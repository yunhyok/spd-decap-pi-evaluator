"""EXP-7 step 1: analytic check of the plane-pair grid kernel.

The model's plane edges are Z_edge = (Zs + j w mu0 d) * (squares of the edge), i.e. R and L share one
2-D graph Laplacian.  Between two via contacts in an (effectively) infinite plane pair the analytic
low-frequency results are
   R = Rs/(2 pi) * ln(s^2/(r1 r2)),   L = mu0 d/(2 pi) * ln(s^2/(r1 r2))     (2-D spreading between discs)
so both equal (Rs or mu0 d) * N_sq with N_sq = ln(s^2/(r1 r2))/(2 pi).  We compute N_sq of the grid
(unit edge conductance, current +1/-1 injected) for two contact conventions:
   'node'  : via = the single grid node containing its centre (EXP-3/5 model convention)
   'disc'  : all nodes within radius r shorted (resolved contact)
and compare with the analytic value using r (physical) and r_eq = h*exp(-pi/2) (5-point Laplacian
point-source equivalent radius).  L25: d = 100 um (single side, as in the kernel), t = 32 um.
"""
import json, math, sys
import numpy as np
from scipy import sparse
from scipy.sparse.linalg import spsolve

MU0 = 4e-7 * math.pi
SIG = 5.959e7; T = 32e-6; D = 100e-6
RS = 1 / (SIG * T)


def nsq(h, s, r, contact, dom=16000.0):
    n = int(round(dom / h)) | 1
    c = n // 2
    idx = np.arange(n * n).reshape(n, n)
    a = np.concatenate([idx[:, :-1].ravel(), idx[:-1, :].ravel()]); b = np.concatenate([idx[:, 1:].ravel(), idx[1:, :].ravel()])
    N = n * n
    G = sparse.coo_matrix((np.r_[np.ones(len(a)), np.ones(len(a)), -np.ones(len(a)), -np.ones(len(a))],
                           (np.r_[a, b, a, b], np.r_[a, b, b, a])), shape=(N, N)).tocsr()
    x = (np.arange(n) - c) * h
    X, Y = np.meshgrid(x, x)
    p1 = (-s / 2, 0.0); p2 = (s / 2, 0.0)
    def nodes(p):
        if contact == "node":
            i = int(round(p[0] / h)) + c; j = c
            return [idx[j, i]]
        m = (X - p[0]) ** 2 + (Y - p[1]) ** 2 <= r * r
        if not m.any():
            i = int(round(p[0] / h)) + c; return [idx[c, i]]
        return list(idx[m])
    n1, n2 = nodes(p1), nodes(p2)
    # short contact node sets: map to representative with large conductance
    rows, cols, vals = [], [], []
    for grp in (n1, n2):
        for k in grp[1:]:
            rows += [grp[0], k, grp[0], k]; cols += [grp[0], k, k, grp[0]]; vals += [1e9, 1e9, -1e9, -1e9]
    if rows:
        G = G + sparse.coo_matrix((vals, (rows, cols)), shape=(N, N)).tocsr()
    rhs = np.zeros(N); rhs[n1[0]] = 1.0; rhs[n2[0]] = -1.0
    ref = idx[0, 0]
    keep = np.ones(N, bool); keep[ref] = False
    V = np.zeros(N); V[keep] = spsolve(G[keep][:, keep].tocsc(), rhs[keep])
    return float(V[n1[0]] - V[n2[0]])


rows = []
for s in (1000.0, 3000.0):
    for r in (20.0, 75.0):
        for h in (50.0, 100.0, 200.0, 400.0):
            ana = math.log(s * s / (r * r)) / (2 * math.pi)
            ana_eq = math.log(s * s / (h * math.exp(-math.pi / 2)) ** 2) / (2 * math.pi)
            nn = nsq(h, s, r, "node"); nd = nsq(h, s, r, "disc")
            rows.append(dict(s_um=s, r_um=r, h_um=h, Nsq_node=nn, Nsq_disc=nd, Nsq_analytic_r=ana, Nsq_analytic_req=ana_eq,
                             err_node_vs_analytic_r=nn / ana - 1, err_node_vs_analytic_req=nn / ana_eq - 1, err_disc_vs_analytic_r=nd / ana - 1,
                             L_node_pH=MU0 * D * nn * 1e12, L_analytic_pH=MU0 * D * ana * 1e12, R_node_mOhm=RS * nn * 1e3, R_analytic_mOhm=RS * ana * 1e3))
            print({k: (round(v, 4) if isinstance(v, float) else v) for k, v in rows[-1].items()}, flush=True)
json.dump(rows, open("/home/claude/work/exp7/kernel_check.json", "w"), indent=1)
