# SPD Decap PI Evaluator v0.22.0 — Oracle Reproduction Appendix

> **SPD Decap PI Evaluator v0.22.0 — retry-v7 current boundary:** Seven public
> `primary-h4-p0r` invocations are immutable. In the seventh, native `splu`
> returned for `A_background_II`; attempted/performed are `true/true` and the
> completed-name list contains it, but the obsolete native/exported nnz equality
> gate failed before any certificate or prefix. `A_conductor_II` was not
> attempted and the exact native/exported factor counts were not persisted.
> Inner/outer monitoring retained `174/573` samples and all `31` outer retry
> events without truncation; both resource gates passed, but this is neither
> accuracy evidence nor an 8 GiB-fit claim. Commits `51e5069...` and
> `17414cf...` record consumption and deletion-only retirement; token state is
> absent. Retry-v7 requires `0 < exported <= native`, validates distinct
> native/exported portable-byte formulas and caps both, without schema, retry,
> or factor-order changes. Frozen Python/runner/tests SHA-256 are `7a1dba5e...` /
> `852ce8a0...` / `bd2e3e2c...`; focused `25/25` and full no-cache `341/341`
> passed, the latter in `88.40 s`. No RHS, solve, H4 physics, or PowerSI work
> ran; `next_stage_authorized=false`. Earlier retry-v6 sections are immutable
> history. Exact evidence is in [results](T1_AV_BOUNDARY_SCHUR_RESULTS.md) and
> the [reproduction appendix](ORACLE_REPRODUCTION.md).


최종 갱신: 2026-08-16 (Asia/Seoul)

이 문서는 [`R2_ORACLE_RESULTS.md`](R2_ORACLE_RESULTS.md)의 custom numerical table을 같은 research commit에서 재생성하는 exact commands를 보존한다. 모두 repository root `C:\Users\User\Documents\SPD Decap PI Evaluator-evaluation-research`에서 실행한다. 제품 file과 raw SPD를 수정하지 않는다.

공통 환경:

```powershell
$env:PYTHONPATH=(Resolve-Path src).Path
python --version
# Python 3.12.10
```

wall time은 host load에 따라 달라질 수 있다. 판정에 사용하는 값은 response, convergence와 invariant이며 timing은 참고 계측이다.

## N0 frozen scalar-graph reproduction v1

11 nodes/12 edges에 두 series chain, dangling tree, cycle과 parallel pair를 함께 넣는다. `E`를 gauge/reference로 고정하고 A/D 2-port Z를 raw/reduced/closed form으로 비교한다.

```powershell
@'
import math
import numpy as np
from spd_decap_pi._core.solver.finite_route_reducer import (
    FiniteRouteEdge, FiniteRouteNode, reduce_finite_route_graph,
)

def node(node_id, layer, *roles):
    return FiniteRouteNode(node_id, layer, tuple(roles))

def edge(edge_id, first, second, resistance, inductance):
    return FiniteRouteEdge(
        edge_id, first.node_id, second.node_id, first.layer, second.layer,
        resistance, inductance, 100.0, (f"owner:{edge_id}",),
    )

A = node("A", "L1", "port")
B = node("B", "L2")
D = node("D", "L3", "artwork")
F = node("F", "L4")
G = node("G", "L5")
E = node("E", "L6", "port")
H = node("H", "L7")
I = node("I", "L8")
X = node("X", "L9")
Y = node("Y", "L10")
Z = node("Z", "L11")
nodes = (A, B, D, F, G, E, H, I, X, Y, Z)
edges = (
    edge("ab", A, B, .010, .10e-9), edge("bd", B, D, .020, .20e-9),
    edge("df", D, F, .030, .30e-9), edge("fg", F, G, .040, .40e-9),
    edge("ge", G, E, .050, .50e-9), edge("bh", B, H, .060, .60e-9),
    edge("hi", H, I, .070, .70e-9), edge("dx1", D, X, .080, .80e-9),
    edge("xy", X, Y, .090, .90e-9), edge("yz", Y, Z, .100, 1.00e-9),
    edge("zd", Z, D, .110, 1.10e-9), edge("dx2", D, X, .120, 1.20e-9),
)
reduced = reduce_finite_route_graph(nodes, edges, source_identity="repro-v1")

def solve(node_ids, branches, frequency_hz):
    ground = "E"
    keep = [item for item in node_ids if item != ground]
    position = {item: index for index, item in enumerate(keep)}
    admittance = np.zeros((len(keep), len(keep)), dtype=complex)
    for first, second, resistance, inductance in branches:
        value = 1.0 / (resistance + 1j * 2 * math.pi * frequency_hz * inductance)
        if first != ground:
            admittance[position[first], position[first]] += value
        if second != ground:
            admittance[position[second], position[second]] += value
        if first != ground and second != ground:
            admittance[position[first], position[second]] -= value
            admittance[position[second], position[first]] -= value
    rhs = np.zeros((len(keep), 2), dtype=complex)
    rhs[position["A"], 0] = 1.0
    rhs[position["D"], 1] = 1.0
    voltage = np.linalg.solve(admittance, rhs)
    return voltage[[position["A"], position["D"]], :]

raw_branches = [
    (item.first_node_id, item.second_node_id, item.resistance_ohm, item.inductance_h)
    for item in edges
]
reduced_branches = [
    (item.first_node_id, item.second_node_id, item.resistance_ohm, item.inductance_h)
    for item in reduced.reduced_edges
]
max_relative = max_absolute = max_analytic = 0.0
for frequency in (0.0, 1e5, 1e6, 1e7, 1e8, 5e8, 1e9, 2e9):
    raw_z = solve([item.node_id for item in nodes], raw_branches, frequency)
    reduced_z = solve([item.node_id for item in reduced.retained_nodes], reduced_branches, frequency)
    z_ad = .03 + 1j * 2 * math.pi * frequency * .30e-9
    z_de = .12 + 1j * 2 * math.pi * frequency * 1.20e-9
    analytic = np.array(((z_ad + z_de, z_de), (z_de, z_de)))
    max_relative = max(max_relative, np.linalg.norm(raw_z-reduced_z)/np.linalg.norm(raw_z))
    max_absolute = max(max_absolute, float(np.max(np.abs(raw_z-reduced_z))))
    max_analytic = max(max_analytic, np.linalg.norm(raw_z-analytic)/np.linalg.norm(analytic))
print(reduced.statistics)
print("retained/reduced/owners", len(reduced.retained_nodes), len(reduced.reduced_edges), len(reduced.owner_ledger))
print("max_relative", max_relative)
print("max_abs_ohm", max_absolute)
print("max_analytic_relative", max_analytic)
'@ | python -
```

2026-08-14 frozen output의 핵심은 retained nodes 6, reduced edges 7, owners 12, relative `5.3338e-15`, absolute `1.7790e-14 Ω`, analytic relative `7.7430e-15`다.

## N0 analytic chain과 global MNA

두 shunt termination 사이의 3-edge series chain을 exact reduction한 뒤 raw/reduced 2-port global MNA와 closed form을 41개 주파수에서 비교한다.

```powershell
@'
import json, time
from math import pi
import numpy as np
from spd_decap_pi._core.solver.finite_route_reducer import FiniteRouteEdge, FiniteRouteNode, reduce_finite_route_graph
from spd_decap_pi._core.solver.global_mna import DifferentialPort, SeriesBranchBlock, compile_global_mna

def edge(e,a,b,la,lb,r,l,owner):
    return FiniteRouteEdge(e,a,b,la,lb,r,l,100.0,(owner,))
def one(z): return np.asarray(((z,),),dtype=np.complex128)
def zrl(r,l): return lambda f: one(r+1j*2*pi*f*l)

nodes=(FiniteRouteNode('A','L1',('port',)),FiniteRouteNode('B','L2'),FiniteRouteNode('C','L3'),FiniteRouteNode('D','L4',('port',)))
edges=(edge('e1','A','B','L1','L2',.011,.3e-9,'owner-e1'),edge('e2','B','C','L2','L3',.023,.5e-9,'owner-e2'),edge('e3','C','D','L3','L4',.037,.4e-9,'owner-e3'))
t0=time.perf_counter(); reduction=reduce_finite_route_graph(nodes,edges,source_identity='N0-scalar-series-canonical'); reduction_s=time.perf_counter()-t0
red=reduction.reduced_edges[0]
raw_blocks=tuple(SeriesBranchBlock((e.edge_id,),(e.first_node_id,),(e.second_node_id,),zrl(e.resistance_ohm,e.inductance_h),f'raw-{e.edge_id}',owner_ids=e.owner_ids) for e in edges)
common=(SeriesBranchBlock(('shuntA',),('A',),('G',),lambda f:one(1/(1j*2*pi*f*330e-9)),'shunt-A',owner_ids=('owner-shunt-A',)),SeriesBranchBlock(('shuntD',),('D',),('G',),one(.19),'shunt-D',owner_ids=('owner-shunt-D',)))
ports=(DifferentialPort('PA','A','G'),DifferentialPort('PD','D','G'))
raw=compile_global_mna(('A','B','C','D','G'),branch_blocks=raw_blocks+common,ports=ports)
reduced=compile_global_mna(('A','D','G'),branch_blocks=(SeriesBranchBlock((red.reduced_edge_id,),('A',),('D',),zrl(red.resistance_ohm,red.inductance_h),'reduced-series',owner_ids=red.owner_ids),)+common,ports=ports)
metrics={'parity_relative_frobenius':0.,'parity_absolute_ohm':0.,'analytic_relative_frobenius':0.,'analytic_absolute_ohm':0.,'reciprocity_relative':0.,'min_hermitian_z_eigenvalue_ohm':float('inf'),'backward_residual':0.,'kcl_residual':0.,'scaled_saddle_condition_estimate':0.}
for f in np.geomspace(1e5,2e9,41):
    a=raw.solve(float(f)); b=reduced.solve(float(f)); d=a.impedance_ohm-b.impedance_ohm
    metrics['parity_relative_frobenius']=max(metrics['parity_relative_frobenius'],float(np.linalg.norm(d)/np.linalg.norm(a.impedance_ohm)))
    metrics['parity_absolute_ohm']=max(metrics['parity_absolute_ohm'],float(np.max(np.abs(d))))
    zs=sum(e.resistance_ohm for e in edges)+1j*2*pi*f*sum(e.inductance_h for e in edges); za=1/(1j*2*pi*f*330e-9)
    exact=np.linalg.inv(np.asarray(((1/za+1/zs,-1/zs),(-1/zs,1/.19+1/zs)),complex)); da=a.impedance_ohm-exact
    metrics['analytic_relative_frobenius']=max(metrics['analytic_relative_frobenius'],float(np.linalg.norm(da)/np.linalg.norm(exact)))
    metrics['analytic_absolute_ohm']=max(metrics['analytic_absolute_ohm'],float(np.max(np.abs(da))))
    for result in (a,b):
        x=result.diagnostics
        metrics['reciprocity_relative']=max(metrics['reciprocity_relative'],x.raw_reciprocity_relative_error)
        metrics['min_hermitian_z_eigenvalue_ohm']=min(metrics['min_hermitian_z_eigenvalue_ohm'],x.min_hermitian_impedance_eigenvalue_ohm)
        metrics['backward_residual']=max(metrics['backward_residual'],x.backward_relative_residual)
        metrics['kcl_residual']=max(metrics['kcl_residual'],x.nodal_kcl_relative_residual)
        metrics['scaled_saddle_condition_estimate']=max(metrics['scaled_saddle_condition_estimate'],x.scaled_saddle_condition_estimate)
print(json.dumps({'case':'N0-analytic-chain','frequency_count':41,'frequency_range_hz':[1e5,2e9],'raw_edges':len(edges),'reduced_edges':len(reduction.reduced_edges),'reduced_R_ohm':red.resistance_ohm,'reduced_L_H':red.inductance_h,'owner_dispositions':{k:v.kind for k,v in reduction.owner_ledger.items()},'reduction_seconds_reference_only':reduction_s,**metrics},indent=2))
'@ | python -
```

frozen 핵심은 parity relative `7.0923e-16`, absolute `2.8305e-16 Ω`, closed-form relative `9.1942e-16`, reciprocity `5.9193e-16`, KCL `1.3686e-16`, scaled saddle condition estimate `62.1132`다. 마지막 값은 `onenormest` 기반 scaled 1-norm estimate이며 `κ2`나 엄밀한 forward-error bound가 아니다. reduction wall time은 host-dependent 참고값이다.

## S1 rectangle levels와 circular-annulus failure

```powershell
@'
import math
from shapely.geometry import Point, Polygon
from spd_decap_pi._core.solver.tri_fem_sheet import (
    FiniteSheetContact, TriFemSheetError, compile_tri_fem_sheet,
)

SIGMA = 5.959e7
THICKNESS = 35e-6
island = Polygon(((0, 0), (7, 0), (7, 3), (0, 3)))
contacts = (
    FiniteSheetContact("positive", "owner-positive", Polygon(((.5,.7),(1.5,.7),(1.5,2.3),(.5,2.3)))),
    FiniteSheetContact("negative", "owner-negative", Polygon(((5.5,.7),(6.5,.7),(6.5,2.3),(5.5,2.3)))),
)
previous = None
for level in range(8):
    sheet = compile_tri_fem_sheet(
        "rectangle-repro", island, contacts=contacts,
        conductivity_s_per_m=SIGMA, thickness_m=THICKNESS,
        refinement_levels=level,
    )
    zdc = float((1.0 / sheet.contact_admittance_s(0.0)[0, 0]).real)
    change = None if previous is None else abs(zdc-previous)/abs(previous)
    print(level, len(sheet.mesh.node_xy_m), len(sheet.mesh.triangles), zdc, change)
    previous = zdc

resolution = 30
disk = Point(0, 0).buffer(5.0, resolution=resolution)
inner = Point(0, 0).buffer(.6, resolution=resolution)
outer = Point(0, 0).buffer(4.8, resolution=resolution).difference(
    Point(0, 0).buffer(4.2, resolution=resolution)
)
annular_contacts = (
    FiniteSheetContact("inner", "owner-inner", inner),
    FiniteSheetContact("outer", "owner-outer", outer),
)
coarse = compile_tri_fem_sheet(
    "annulus-repro", disk, contacts=annular_contacts,
    conductivity_s_per_m=SIGMA, thickness_m=THICKNESS,
    refinement_levels=0,
)
observed = float((1.0/coarse.contact_admittance_s(0.0)[0,0]).real)
analytic = math.log(4.2/.6)/(2*math.pi*SIGMA*THICKNESS)
print("annulus", observed, analytic, abs(observed-analytic)/analytic)
try:
    compile_tri_fem_sheet(
        "annulus-repro", disk, contacts=annular_contacts,
        conductivity_s_per_m=SIGMA, thickness_m=THICKNESS,
        refinement_levels=1,
    )
except TriFemSheetError as exc:
    print("annulus_refinement", exc)
'@ | python -
```

rectangle `change`와 [`R2_ORACLE_RESULTS.md`](R2_ORACLE_RESULTS.md)의 표는 모두 `|Zfine−Zcoarse|/|Zcoarse|`다. 이 two-contact case에서는 contact-admittance 기준 `|Kfine−Kcoarse|/|Kfine|`와 대수적으로 같은 값이다.

## V1/V2 frequency sweep

```powershell
@'
import math
import numpy as np
from spd_decap_pi._core.solver.via_peec import (
    FilledMicroviaSegment, MU_0_H_PER_M, compile_via_peec,
    solve_via_peec, straight_wire_external_self_inductance,
)

SIGMA = 5.959e7
def via(name, x, y, group="P", terminal="P", sign=1, length=80.0, radius=12.5):
    return FilledMicroviaSegment(
        name, x*1e-6, y*1e-6, 0.0, length*1e-6, radius*1e-6,
        SIGMA, group, terminal, sign,
    )

single = via("single", 0, 0, length=100.0, radius=10.0)
single_op = compile_via_peec((single,))
print("Rdc", single.length_m/(SIGMA*math.pi*single.radius_m**2))
print("Lext", straight_wire_external_self_inductance(single.length_m, single.radius_m))
print("Lint0", MU_0_H_PER_M*single.length_m/(8*math.pi))
for frequency in (0.0, 1.0, 1e5, 1e6, 1e7, 1e8, 5e8, 1e9, 2e9, 1e15):
    result = solve_via_peec(single_op, frequency)
    z = result.impedance_ohm[0,0]
    inductance = None if frequency == 0 else z.imag/(2*math.pi*frequency)
    print("V1", frequency, z.real, inductance, result.diagnostics)

array = (
    via("P0", -70, 0), via("P1", 0, 0), via("P2", 70, 0),
    via("G0", -35, 100, group="G", terminal="G", sign=-1),
    via("G1", 35, 100, group="G", terminal="G", sign=-1),
)
array_op = compile_via_peec(array)
for frequency in (0.0, 1e5, 1e6, 1e7, 1e8, 5e8, 1e9, 2e9):
    result = solve_via_peec(array_op, frequency)
    mode = np.ones(2)
    loop_z = mode @ result.impedance_ohm @ mode
    currents = result.branch_currents_per_group_amp @ mode
    loop_l = None if frequency == 0 else loop_z.imag/(2*math.pi*frequency)
    print("V2", frequency, loop_z.real, loop_l, currents, result.diagnostics)
'@ | python -
```

## A1 mesh/crop reproduction

```powershell
@'
import numpy as np
from spd_decap_pi.research_axisymmetric_electrostatics import (
    AxisymmetricConductor, DielectricLayer, build_axisymmetric_problem,
    radial_coupling_correction, solve_axisymmetric,
    solve_vertical_column_baseline,
)

def make_problem(h_um, radius_um):
    h = h_um*1e-6
    radius = radius_um*1e-6
    z0, z1 = -125e-6, 125e-6
    conductors = (
        AxisymmetricConductor("G", 0, radius, 75e-6, 95e-6),
        AxisymmetricConductor("P", 127e-6, radius, 25e-6, 45e-6),
        AxisymmetricConductor("OTHER", 0, radius, -25e-6, -5e-6),
        AxisymmetricConductor("G", 0, radius, -75e-6, -55e-6),
        AxisymmetricConductor("OTHER", 0, 30e-6, -25e-6, 45e-6),
        AxisymmetricConductor("OTHER", 0, 50e-6, 25e-6, 45e-6),
    )
    return build_axisymmetric_problem(
        r_max_m=radius, z_min_m=z0, z_max_m=z1, dr_m=h, dz_m=h,
        dielectrics=(DielectricLayer(z0, z1, 3.4),),
        conductors=conductors, outer_terminal="G",
        boundary_semantics="open_r_explicit_convergence_top_bottom_outer_conductor",
    )

def solve(h_um, radius_um):
    problem = make_problem(h_um, radius_um)
    full = solve_axisymmetric(problem, max_cells=120000, max_runtime_seconds=60)
    core = solve_vertical_column_baseline(problem, max_cells=120000, max_runtime_seconds=60)
    correction = radial_coupling_correction(full, core, problem=problem)
    matrix = correction.correction_maxwell_capacitance_f
    p = correction.terminals.index("P")
    print("case", h_um, radius_um, problem.shape, matrix[p,p], full.diagnostics, core.diagnostics)
    return matrix

cases = {(h,r): solve(h,r) for h,r in (
    (10,300), (5,300), (2.5,300), (1.25,300), (5,600), (5,1200)
)}

def delta(first, second):
    floor = 1e-15
    eligible = np.abs(second) >= 10*floor
    rms = np.linalg.norm(first-second)/max(np.linalg.norm(second), np.sqrt(second.size)*floor)
    maximum = np.max(np.abs(first-second)[eligible]/np.maximum(np.abs(second)[eligible], floor))
    return float(rms), float(maximum), float(np.max(np.abs(first-second)))

print("mesh10-5", delta(cases[(10,300)], cases[(5,300)]))
print("mesh5-2.5", delta(cases[(5,300)], cases[(2.5,300)]))
print("mesh2.5-1.25", delta(cases[(2.5,300)], cases[(1.25,300)]))
print("crop300-600", delta(cases[(5,300)], cases[(5,600)]))
print("crop600-1200", delta(cases[(5,600)], cases[(5,1200)]))
'@ | python -
```

## C1 scalar disk BEM과 finite-footprint projection

```powershell
@'
import math
import time
import numpy as np
from shapely.geometry import Point, box
from spd_decap_pi.fft_bem_capacitance import (
    EPSILON_0_F_PER_M, FFTPulseBEM, SurfaceConductor, UniformXYGrid,
)
from spd_decap_pi._core.solver.surface_patch_plane import (
    SurfacePatchArtwork, SurfacePatchDielectric, SurfacePatchFinitePort,
    SurfacePatchMesh, compile_surface_patch_plane,
)

radius_um, gap_um, dk = 5.0, .30, 3.3
values = []
for pitch_um in (.5, .25, .125, .0625):
    count = round(12/pitch_um)
    axis = -6 + (np.arange(count)+.5)*pitch_um
    xx, yy = np.meshgrid(axis, axis)
    mask = xx*xx + yy*yy <= radius_um*radius_um
    model = FFTPulseBEM(
        UniformXYGrid(-6e-6, -6e-6, pitch_um*1e-6, (count,count)),
        (SurfaceConductor("upper", "P", gap_um*1e-6, mask),
         SurfaceConductor("lower", "DGND", 0.0, mask)),
        dk, max_unknowns=50000,
    )
    started = time.perf_counter()
    result = model.maxwell_capacitance()
    elapsed = time.perf_counter()-started
    capacitance = .5*(result.maxwell_capacitance_f[0,0]-result.maxwell_capacitance_f[0,1])
    eps = EPSILON_0_F_PER_M*dk
    radius, gap = radius_um*1e-6, gap_um*1e-6
    love = eps*math.pi*radius*radius/gap + eps*radius*(math.log(16*math.pi*radius/gap)-1)
    values.append((pitch_um, capacitance))
    print("BEM", pitch_um, model.unknown_count, capacitance,
          abs(capacitance-love)/love, max(result.diagnostics.relative_residuals),
          elapsed, result.diagnostics.estimated_fft_workspace_bytes)
for first, second in zip(values, values[1:]):
    print("change", first[0], second[0], abs(first[1]-second[1])/abs(second[1]))

artwork = box(0,0,1000,1000)
footprint = Point(500,500).buffer(200, resolution=128)
mesh = SurfacePatchMesh.uniform(
    layer_order=("TOP","BOT"),
    artwork=(SurfacePatchArtwork("TOP","P",artwork), SurfacePatchArtwork("BOT","G",artwork)),
    cell_um=25,
)
operator = compile_surface_patch_plane(
    mesh, dielectrics=(SurfacePatchDielectric("TOP","BOT",100e-6,4.0),),
    synthetic_material_defaults=True,
)
projection = operator.finite_port_projection((
    SurfacePatchFinitePort("P","TOP","P",footprint,"reproduction fixture"),
))
expected = footprint.area*1e-12
print("footprint", projection.port_areas_m2[0], expected,
      abs(projection.port_areas_m2[0]-expected)/expected,
      projection.node_weights.shape, projection.node_weights.nnz)
'@ | python -
```

기존 FFT-BEM `relative_residuals`는 `||Ax-b||/max(||b||,1)`이다. preregistered backward residual과 정의가 다르므로 값만 변환해 재사용하지 않는다.

## T1-E0 body-fitted Cohn stripline

zero-thickness homogeneous centered stripline의 `C'`를 Cohn exact 식과 비교한다. strip edge `x=±w/2`를 node에 정확히 넣고, table의 padding은 strip edge부터 `8h`로 잰다. 이어지는 crop sweep은 h/128에서 `4h`를 추가 계산해 같은 `8h` 결과와 비교한다. sparse `A`를 dense로 만들지 않는다. 아래 peak working set은 six 8h table solves와 three h/128 4h crop solves를 한 Windows process에서 순차 실행한 high-water mark이며 개별 solve나 production memory benchmark가 아니다.

```powershell
@'
import ctypes as ct
from time import perf_counter
import numpy as np
from scipy import sparse
from scipy.sparse.linalg import spsolve, norm as sparse_norm
from scipy.special import ellipk

EPS = 8.854_187_812_8e-12

def cohn(width, height):
    k = np.tanh(np.pi*width/(4*height))
    return 4*EPS*ellipk(k*k)/ellipk(1-k*k)

def peak_mib():
    if not hasattr(ct, "WinDLL"):
        return None
    class PMC(ct.Structure):
        _fields_ = [
            ("cb",ct.c_ulong), ("pf",ct.c_ulong),
            ("peak",ct.c_size_t), ("ws",ct.c_size_t),
            ("qpp",ct.c_size_t), ("qp",ct.c_size_t),
            ("qnp",ct.c_size_t), ("qn",ct.c_size_t),
            ("page",ct.c_size_t), ("peakpage",ct.c_size_t),
            ("private",ct.c_size_t),
        ]
    query = ct.WinDLL("Psapi.dll").GetProcessMemoryInfo
    query.argtypes = (ct.c_void_p, ct.POINTER(PMC), ct.c_ulong)
    query.restype = ct.c_int
    counters = PMC(); counters.cb = ct.sizeof(counters)
    query(
        ct.WinDLL("Kernel32.dll").GetCurrentProcess(),
        ct.byref(counters), counters.cb,
    )
    return counters.peak/2**20

def solve(width, height, divisions, padding):
    spacing = height/divisions
    n_side = round(padding/spacing)
    n_center = int(np.ceil(width/spacing))
    x = np.r_[
        np.linspace(-width/2-padding, -width/2, n_side+1),
        np.linspace(-width/2, width/2, n_center+1)[1:],
        np.linspace(width/2, width/2+padding, n_side+1)[1:],
    ]
    nx, ny = x.size, 2*divisions+1
    dx, dy = np.diff(x), np.full(ny-1, spacing)
    wx = np.r_[dx[0]/2, (dx[:-1]+dx[1:])/2, dx[-1]/2]
    wy = np.r_[dy[0]/2, (dy[:-1]+dy[1:])/2, dy[-1]/2]
    strip = np.abs(x) <= width/2+1e-18
    conductor = np.zeros((ny,nx), bool)
    conductor[0] = conductor[-1] = True
    conductor[divisions,strip] = True
    ids = -np.ones((ny,nx), int)
    ids[~conductor] = np.arange((~conductor).sum())
    unknowns = int(ids.max()+1)
    rows, columns, data = [], [], []
    rhs = np.zeros(unknowns)

    def edge(first, second, conductance):
        first_fixed, second_fixed = conductor.flat[first], conductor.flat[second]
        first_v = float(first_fixed and first//nx == divisions)
        second_v = float(second_fixed and second//nx == divisions)
        if not first_fixed and not second_fixed:
            a, b = ids.flat[first], ids.flat[second]
            rows.extend((a,b,a,b)); columns.extend((a,b,b,a))
            data.extend((conductance,conductance,-conductance,-conductance))
        elif not first_fixed:
            a = ids.flat[first]
            rows.append(a); columns.append(a); data.append(conductance)
            rhs[a] += conductance*second_v
        elif not second_fixed:
            b = ids.flat[second]
            rows.append(b); columns.append(b); data.append(conductance)
            rhs[b] += conductance*first_v

    for j in range(ny):
        for i in range(nx-1):
            edge(j*nx+i, j*nx+i+1, EPS*wy[j]/dx[i])
    for j in range(ny-1):
        for i in range(nx):
            edge(j*nx+i, (j+1)*nx+i, EPS*wx[i]/dy[j])

    matrix = sparse.coo_matrix((data,(rows,columns)), shape=(unknowns,unknowns)).tocsc()
    unknown_v = spsolve(matrix,rhs)
    potential = np.zeros((ny,nx))
    potential[divisions,strip] = 1.0
    potential[~conductor] = unknown_v
    energy = charge = 0.0
    for j in range(ny):
        for i in range(nx-1):
            conductance = EPS*wy[j]/dx[i]
            dv = potential[j,i]-potential[j,i+1]
            energy += conductance*dv*dv
            if j == divisions and strip[i]:
                charge += conductance*(potential[j,i]-potential[j,i+1])
            if j == divisions and strip[i+1]:
                charge += conductance*(potential[j,i+1]-potential[j,i])
    for j in range(ny-1):
        for i in range(nx):
            conductance = EPS*wx[i]/dy[j]
            dv = potential[j,i]-potential[j+1,i]
            energy += conductance*dv*dv
            if j == divisions and strip[i]:
                charge += conductance*(potential[j,i]-potential[j+1,i])
            if j+1 == divisions and strip[i]:
                charge += conductance*(potential[j+1,i]-potential[j,i])
    backward = np.linalg.norm(matrix@unknown_v-rhs) / (
        sparse_norm(matrix)*np.linalg.norm(unknown_v) + np.linalg.norm(rhs)
    )
    symmetry = float(np.max(np.abs((matrix-matrix.T).data), initial=0.0))
    return energy, abs(energy-charge)/energy, backward, symmetry, (nx,ny,unknowns,matrix.nnz)

height, results, started = 100e-6, [], perf_counter()
for width_um in (120.,500.,914.4):
    width = width_um*1e-6
    exact = cohn(width,height)
    runs = [solve(width,height,n,8*height) for n in (64,128)]
    c64, c128 = runs[0][0], runs[1][0]
    richardson = 2*c128-c64
    results.append((width_um,exact,c64,c128,richardson,*runs))
    print(
        f"{width_um:6.1f} C0={exact*1e12:12.7f} "
        f"C64={c64*1e12:12.7f} C128={c128*1e12:12.7f} "
        f"Rich={richardson*1e12:12.7f} pF/m rel={(richardson-exact)/exact:+.3e}"
    )
print("all C64>C128>C0:", all(item[2]>item[3]>item[1] for item in results))
print("max energy-charge rel:", max(max(item[5][1],item[6][1]) for item in results))
print("max backward residual:", max(max(item[5][2],item[6][2]) for item in results))
print("max A-A.T abs:", max(max(item[5][3],item[6][3]) for item in results))
print("grids:", [(item[0],item[5][4],item[6][4]) for item in results])
crop_changes = []
for item in results:
    width = item[0]*1e-6
    c4 = solve(width,height,128,4*height)[0]
    c8 = item[3]
    crop_changes.append(abs(c8-c4)/abs(c8))
print("h128 crop 4h->8h:", crop_changes, max(crop_changes))
print("wall_s=", perf_counter()-started, "peak_working_set_MiB=", peak_mib())
'@ | python -
```

frozen table의 `C0/C64/C128/Richardson` pF/m는 폭 120 µm에서 `36.8128510/37.0112662/36.9119306/36.8125951`, 500 µm에서 `104.1702700/104.3664877/104.2682374/104.1699871`, 914.4 µm에서 `177.5537791/177.7499747/177.6517423/177.5535098`이다. 최대 energy-charge mismatch `1.50e-12`, backward residual `4.13e-19`, matrix asymmetry exact `0`이다. h/128 crop 4h→8h 변화는 최대 `2.12459e-6`이다. 한 process peak working set은 약 `1,856 MiB`였으며 현 starting commit에는 이 body-fitted solver module이 없으므로 standalone canonical oracle로만 사용한다.

## T1-M0 periodic plate-pair identity와 finite-length screen

현행 1-D finite-thickness copper helper를 periodic two-plate analytic identity에만 사용한다. through-thickness broadside redistribution/proximity는 포함하지만 finite-width lateral edge/proximity current crowding은 검증하지 않는다.

```powershell
@'
import math
import numpy as np
from spd_decap_pi._core.solver.mfdm import MU_0_H_PER_M, copper_surface_impedance

EPS0 = 8.8541878128e-12
sigma, width, thickness = 59.6e6, 5e-3, 35e-6
gap, length = 50e-6, 10e-3
frequencies = np.asarray((0., 1e5, 1e6, 1e7, 1e8, 5e8, 1e9, 2e9))
surface = np.asarray(copper_surface_impedance(frequencies, sigma, thickness), complex)
impedance = length * (
    2 * surface / width
    + 1j * 2 * np.pi * frequencies * MU_0_H_PER_M * gap / width
)
rdc = 2 * length / (sigma * width * thickness)
ldc = MU_0_H_PER_M * length * (gap + 2 * thickness / 3) / width
lhf = MU_0_H_PER_M * length * gap / width
print("limits_Rdc_Ldc_Lhf", rdc, ldc, lhf)
for frequency, value in zip(frequencies, impedance, strict=True):
    inductance = ldc if frequency == 0 else value.imag / (2 * np.pi * frequency)
    print("RL", frequency, value.real, inductance)
for first, second, r_first, r_second in zip(
    frequencies[4:-1], frequencies[5:], impedance.real[4:-1], impedance.real[5:], strict=True
):
    print("skin_slope", first, second, math.log(r_second/r_first)/math.log(second/first))

for physical_length in (0.889e-3, 0.900e-3, 4.2e-3, 10e-3):
    theta = 2 * np.pi * 2e9 * physical_length * math.sqrt(MU_0_H_PER_M * EPS0 * 4.0)
    exact = np.asarray((theta/math.tan(theta), theta/math.sin(theta)))
    nominal_pi = np.asarray((1-theta*theta/2, 1.0))
    series_only = np.ones(2)
    print(
        "length_screen", physical_length, theta,
        np.max(np.abs(nominal_pi-exact)/np.abs(exact)),
        np.max(np.abs(series_only-exact)/np.abs(exact)),
    )
'@ | python -
```

2026-08-14 frozen output은 `Rdc=1.9175455417066e-3 Ω`, `Ldc=1.8430676901060e-10 H`, `Lhf=1.2566370614359e-10 H`다. 2 GHz는 `R=4.60396197072075e-2 Ω`, `L=1.29327422670828e-10 H`다. 100 MHz→500 MHz→1 GHz→2 GHz resistance slope는 `0.500032982/0.50000000005/0.50000000000`이다. 0.889/0.900/4.2/10 mm의 `|βl|`은 `0.074528249/0.075450421/0.352101964/0.838338009`; nominal-π 최대 coefficient error는 `0.0927/0.0950/2.120/13.975%`, series-only는 `0.1856/0.1902/4.348/32.633%`다.

## T1-M0 independent normalized slab FEM

아래 block은 제품 helper를 import하지 않고 periodic `m=0` conductor slab을 1-D linear FEM으로 재구성한다. canonical 12 frequencies와 실행 전에 고정한 W0 material/thickness withheld를 모두 재현한다. exact two-face map의 `Ho=0, Hi=1`인 `Zs` 열만 검증하며 `Zx=Zc csch(γt)` 또는 임의 two-face excitation을 회복하지 않는다. 결과의 balanced scalar를 인공 absolute-node operator나 GlobalMNA stamp로 승격하지 않는다. finite/open rectangular SAO 또는 general exterior solve도 아니다.

```powershell
@'
import hashlib, json, math
import numpy as np
from scipy.linalg import get_lapack_funcs, lu_factor, lu_solve

MU0 = 4e-7*math.pi
UROUND = 2.0**-53
SIGMA = 59.6e6
THICKNESS = 35e-6
WIDTH = 5e-3
GAP = 50e-6
LENGTH = 10e-3

def exact_surface(frequency, sigma, thickness):
    x = thickness*np.sqrt(1j*2*math.pi*frequency*MU0*sigma)
    return complex(x/np.tanh(x)/(sigma*thickness))

def fem_surface(frequency, sigma, thickness, elements):
    x2 = 1j*2*math.pi*frequency*MU0*sigma*thickness**2
    step = 1/elements
    size = elements+1
    stiffness = np.zeros((size,size), complex)
    mass = np.zeros_like(stiffness)
    ke = np.asarray(((1,-1),(-1,1)), complex)/step
    me = step*np.asarray(((2,1),(1,2)), complex)/6
    for index in range(elements):
        stiffness[index:index+2,index:index+2] += ke
        mass[index:index+2,index:index+2] += me
    matrix = stiffness+x2*mass
    rhs = np.zeros(size, complex)
    rhs[0] = x2
    row = np.max(np.abs(matrix), axis=1)
    row_scaled = matrix/row[:,None]
    column = np.max(np.abs(row_scaled), axis=0)
    equilibrated = row_scaled/column[None,:]
    lu, piv = lu_factor(equilibrated, check_finite=True)
    solution = lu_solve((lu,piv), rhs/row, check_finite=True)/column
    backward = np.linalg.norm(matrix@solution-rhs,np.inf)/max(
        np.linalg.norm(matrix,np.inf)*np.linalg.norm(solution,np.inf)
        +np.linalg.norm(rhs,np.inf), np.finfo(float).tiny,
    )
    gecon = get_lapack_funcs('gecon',(equilibrated,))
    rcond, info = gecon(lu,float(np.linalg.norm(equilibrated,1)),norm='1')
    if info or not np.isfinite(rcond) or rcond <= 0:
        raise RuntimeError(('gecon',info,rcond))
    impedance = complex(solution[0]/(sigma*thickness))
    current = abs(np.ones(size)@mass@solution-1)
    loss = np.vdot(solution,mass@solution).real/(sigma*thickness)
    power = abs(loss-impedance.real)/max(abs(impedance.real),np.finfo(float).tiny)
    return impedance, {
        'backward':float(backward), 'kappa1u':float(UROUND/rcond),
        'current':float(current), 'power':float(power),
    }

def log_rms(frequencies, values):
    x = np.log(np.asarray(frequencies,float))
    weights = np.empty_like(x)
    weights[0] = (x[1]-x[0])/2
    weights[-1] = (x[-1]-x[-2])/2
    weights[1:-1] = (x[2:]-x[:-2])/2
    return float(np.sqrt(np.sum(weights*np.asarray(values)**2)/np.sum(weights)))

anchors = (1e5,1e6,1e7,1e8,5e8,1e9,2e9)
delta_ratios = (4.0,2.0,1.0,0.5,0.25)
crossovers = tuple(
    1/(math.pi*MU0*SIGMA*(ratio*THICKNESS)**2)
    for ratio in delta_ratios
)
frequencies = tuple(sorted(set(anchors+crossovers)))
canonical = []
for frequency in frequencies:
    exact = exact_surface(frequency,SIGMA,THICKNESS)
    solved = [fem_surface(frequency,SIGMA,THICKNESS,n) for n in (64,128,256)]
    medium, fine = solved[-2][0], solved[-1][0]
    exact_loop = LENGTH*(2*exact/WIDTH+1j*2*math.pi*frequency*MU0*GAP/WIDTH)
    fine_loop = LENGTH*(2*fine/WIDTH+1j*2*math.pi*frequency*MU0*GAP/WIDTH)
    canonical.append({
        'frequency':frequency, 'fine':fine,
        'error':abs(fine-exact)/abs(exact),
        'mesh':abs(fine-medium)/abs(fine),
        'phase':abs(float(np.angle(fine/exact,deg=True))),
        'loop_error':abs(fine_loop-exact_loop)/abs(exact_loop),
        'loop_R':fine_loop.real,
        **solved[-1][1],
    })

summary = {
    'fine_log_rms':log_rms(frequencies,[row['error'] for row in canonical]),
    'mesh_log_rms':log_rms(frequencies,[row['mesh'] for row in canonical]),
}
for key in ('error','mesh','phase','loop_error','backward','kappa1u','current','power'):
    summary['max_'+key] = max(row[key] for row in canonical)
slopes = []
for first, second in zip(anchors[3:-1],anchors[4:]):
    r0 = next(row['loop_R'] for row in canonical if row['frequency']==first)
    r1 = next(row['loop_R'] for row in canonical if row['frequency']==second)
    slopes.append(math.log(r1/r0)/math.log(second/first))
summary['skin_slopes'] = slopes
serial = '\n'.join(','.join(
    f'{row[key]:.17e}' if not isinstance(row[key],complex)
    else f'{row[key].real:.17e},{row[key].imag:.17e}'
    for key in ('frequency','fine','error','mesh','phase','backward','kappa1u','current','power')
) for row in canonical)
summary['sha256'] = hashlib.sha256(serial.encode()).hexdigest()
print('canonical',json.dumps(summary,indent=2))

def next_power_of_two(value):
    return 1 if value <= 1 else 2**math.ceil(math.log2(value))

withheld = []
for thickness in (17.5e-6,70e-6):
    for sigma in (29.8e6,119.2e6):
        for frequency in (173e3,17.3e6,1.73e9):
            delta = math.sqrt(1/(math.pi*frequency*MU0*sigma))
            fine_n = min(512,next_power_of_two(max(64,math.ceil(8*thickness/delta))))
            sequence = (fine_n//4,fine_n//2,fine_n)
            solved = [fem_surface(frequency,sigma,thickness,n) for n in sequence]
            exact = exact_surface(frequency,sigma,thickness)
            medium, fine = solved[-2][0], solved[-1][0]
            withheld.append({
                'error':abs(fine-exact)/abs(exact),
                'mesh':abs(fine-medium)/abs(fine),
                'phase':abs(float(np.angle(fine/exact,deg=True))),
                **solved[-1][1],
            })
print('withheld',json.dumps({
    'case_count':len(withheld),
    **{'max_'+key:max(row[key] for row in withheld)
       for key in ('error','mesh','phase','backward','kappa1u','current','power')},
},indent=2))
'@ | python -
```

canonical 핵심 재현값은 fine log-RMS/max error `0.017973%/0.073301%`, mesh log-RMS/max `0.053917%/0.219901%`, phase `0.041998°`, backward residual `2.220e-16`, `κ1u=6.336e-10`, current `1.005e-11`, power `7.574e-15`다. FEM resistance slope는 `0.500124/0.500264/0.500528`, canonical checksum은 `d59a770e999fc53c90ca7043cc772badd13220e16ce84912590360fa7e5da5e6`이다. W0 12 cases는 max error/mesh/phase `0.126811%/0.380419%/0.072657°`, backward `2.220e-16`, `κ1u=1.852e-10`, current `7.304e-12`, power `2.147e-15`를 출력한다. timing/RSS는 host-dependent이며 [`T1_M0_SLAB_RESULTS.md`](T1_M0_SLAB_RESULTS.md)에 process-only 참고값으로 기록한다.

## T1 reduced differential lift의 expected failure

exact reduced differential two-port를 네 absolute terminal로 lift하면 global gauge 외에 terminal-plane common-mode null이 남는다. 아래 명령의 solve failure가 예상 결과다. 임의의 conductance로 null을 숨기지 않는다.

```powershell
@'
import numpy as np
from math import pi
from spd_decap_pi._core.solver.mfdm import MU_0_H_PER_M, copper_surface_impedance
from spd_decap_pi._core.solver.global_mna import (
    DifferentialPort, GlobalMnaError, NodalAdmittanceBlock, compile_global_mna,
)

EPS0 = 8.8541878128e-12
frequency = 1e9
sigma, width, thickness = 59.6e6, 5e-3, 35e-6
gap, length, er = 50e-6, 10e-3, 4.0
z = (
    2*copper_surface_impedance(frequency, sigma, thickness)/width
    + 1j*2*pi*frequency*MU_0_H_PER_M*gap/width
)
y = 1j*2*pi*frequency*EPS0*er*width/gap
x = length*np.sqrt(z*y)
yc = np.sqrt(y/z)
y2 = yc*np.asarray(
    ((1/np.tanh(x), -1/np.sinh(x)), (-1/np.sinh(x), 1/np.tanh(x))), complex,
)
incidence = np.asarray(((1., -1., 0., 0.), (0., 0., 1., -1.)))
y4 = incidence.T @ y2 @ incidence
print("rank", np.linalg.matrix_rank(y4, tol=1e-12))
print("null_global", np.linalg.norm(y4 @ np.ones(4)))
print("null_plane", np.linalg.norm(y4 @ np.asarray((1., 1., -1., -1.))))
print("hermitian_eigs", np.linalg.eigvalsh((y4+y4.conj().T)/2))
operator = compile_global_mna(
    ("S0", "R0", "S1", "R1"),
    nodal_admittances=(NodalAdmittanceBlock(
        ("S0", "R0", "S1", "R1"), y4, "T1-lift",
        owner_ids=("T1-reduced-differential",),
    ),),
    ports=(DifferentialPort("P0", "S0", "R0"), DifferentialPort("P1", "S1", "R1")),
)
try:
    operator.solve(frequency)
except GlobalMnaError as exc:
    print(type(exc).__name__ + ":", exc)
else:
    raise SystemExit("ERROR: singular lifted operator unexpectedly solved")
'@ | python -
```

frozen 핵심은 rank `2`, 두 null residual exact `0`, Hermitian eigenvalues 약 `[6.77e-21, 6.86e-18, 1.394e-4, 1.924e-1] S`, 그리고 `GlobalMnaError: saddle system is singular; topology has an unresolved island`다. 이는 expected fail-closed 증거이며 T1 global-composition pass가 아니다.

## T1-M1 preregistered analytic screen

아래 명령은 [`T1_M1_REFERENCE_SPEC.md`](T1_M1_REFERENCE_SPEC.md)의 DC resistance, 2 GHz skin depth와 quasi-TM extent gate를 재현한다. SAO–CIM/A–v solve가 아니며 `eligible`은 실행 가능한 frequency 범위만 뜻한다.

```powershell
@'
from math import pi, sqrt
import numpy as np

c0 = 299_792_458.0
mu0 = 4e-7*pi
sigma = 59.6e6
h = 50e-6
t = 35e-6
f = 2e9
delta = sqrt(2/(2*pi*f*mu0*sigma))
print(f"delta_2GHz_um={delta*1e6:.12f}")
print("w_h Wr_w w_um Wr_um Deff_um kbDeff_2GHz fmax_MHz Rdc_loop_mOhm_per_10mm")
for wh in (5, 10, 20, 50):
    w = wh*h
    for ratio in (1, 5, 20):
        wr = ratio*w
        deff = max(wr, w, h+2*t)
        eta = 2*pi*f*deff/c0
        fmax = 0.3*c0/(2*pi*deff)
        rprime = 1/(sigma*w*t) + 1/(sigma*wr*t)
        print(
            wh, ratio, f"{w*1e6:.1f}", f"{wr*1e6:.1f}", f"{deff*1e6:.1f}",
            f"{eta:.9f}", f"{fmax/1e6:.6f}", f"{rprime*.01*1e3:.9f}",
        )

w, t, wr, length = 120e-6, 17.5e-6, 1.2e-3, 4.2e-3
rs = length/(sigma*w*t)
rr = length/(sigma*wr*t)
eta = 2*pi*f*max(wr, 75e-6+t+104e-6+t)/c0
print("P2_Rsignal_mOhm", rs*1e3)
print("P2_Rreturn_each_mOhm", rr*1e3)
print("P2_Rreturn_parallel_mOhm", rr*.5*1e3)
print("P2_Rloop_mOhm", (rs+rr/2)*1e3)
print("P2_kbDeff_2GHz_vacuum", eta)

Zp = np.diag((rs, rr, rr))
Hg = np.asarray(((1.,0.),(0.,1.),(0.,1.)))
Bg = np.asarray(((1.,),(-1.,)))
saddle = np.block([[Zp,-Hg],[Hg.T,np.zeros((2,2))]])
rhs = np.concatenate((np.zeros(3), Bg[:,0]))
solution = np.linalg.solve(saddle,rhs)
Iabs,vg = solution[:3],solution[3:]
print("P2_DC_saddle_Iabs_A", Iabs.tolist())
print("P2_DC_saddle_ZBg_mOhm", float((Bg[:,0]@vg)*1e3))
print("P2_DC_saddle_residual", float(np.linalg.norm(saddle@solution-rhs)))
'@ | python -
```

frozen 핵심은 `δ2GHz=1.457746488493 µm`이다. 12개 M1 geometry 중 2 GHz `|kb|Deff<=0.3`을 통과하는 것은 8개이며, `(w/h,Wr/w)=(10,20),(20,20),(50,5),(50,20)`은 각각 `0.419169/0.838338/0.523961/2.095845`로 차단된다. P2 artificial two-return DC는 signal `33.557046980 mΩ`, bundled return `1.677852349 mΩ`, loop `35.234899329 mΩ`; vacuum 2 GHz extent는 `0.0503003`이다. equipotential saddle은 `Iabs=[1,-0.5,-0.5] A`, `Z'Bg·l=35.234899329 mΩ`, residual은 binary64 출력에서 exact zero를 재현한다. 이는 동일 단면·전도도의 두 return에 대한 DC 결과이며 75/104 µm gap의 AC equal split을 가정하지 않는다.

## T1-M1-EQ0 exact panel manifest

아래 block은 result solve 전에 고정한 EQ0 full-contour `N={144,288,576}` endpoint와 SHA-256을 exact rational arithmetic으로 재현한다. hash payload는 UTF-8/LF/no trailing newline이고 좌표 단위는 µm다. fine `4N`에서만 `δ/4`와 `h/8` 물리 panel-size gate를 판정한다.

```powershell
@'
from fractions import Fraction as F
from math import pi, sqrt
import hashlib, json

G = F(3,2)

def interval(a,b,m):
    a,b = F(a),F(b)
    sign = 1 if b > a else -1
    half = abs(b-a)/2
    widths = [half*(G-1)*G**i/(G**m-1) for i in range(m)]
    left = [a]
    for width in widths:
        left.append(left[-1]+sign*width)
    right = [b]
    for width in widths:
        right.append(right[-1]-sign*width)
    points = left+list(reversed(right[:-1]))
    assert points[m] == (a+b)/2 and len(points) == 2*m+1
    return points

def add(panels,loop,edge,axis,fixed,anchors,m):
    for anchor,(a,b) in enumerate(zip(anchors,anchors[1:])):
        points = interval(a,b,m)
        for ordinal,(u,v) in enumerate(zip(points,points[1:])):
            if axis == 'x':
                p0,p1 = (u,F(fixed)),(v,F(fixed))
            else:
                p0,p1 = (F(fixed),u),(F(fixed),v)
            panels.append((loop,edge,anchor,ordinal,p0,p1))

def seed_panels():
    panels = []
    add(panels,'signal','facing','x',50,[-125,0,125],8)
    add(panels,'signal','right','y',125,[50,85],5)
    add(panels,'signal','outer','x',85,[125,-125],10)
    add(panels,'signal','left','y',-125,[85,50],5)
    add(panels,'return','outer','x',-35,[-125,125],10)
    add(panels,'return','right','y',125,[-35,0],5)
    add(panels,'return','facing','x',0,[125,0,-125],8)
    add(panels,'return','left','y',-125,[0,-35],5)
    return panels

def refine(panels):
    result = []
    for loop,edge,anchor,ordinal,p0,p1 in panels:
        midpoint = ((p0[0]+p1[0])/2,(p0[1]+p1[1])/2)
        result.append((loop,edge,anchor,2*ordinal,p0,midpoint))
        result.append((loop,edge,anchor,2*ordinal+1,midpoint,p1))
    return result

def length(panel):
    p0,p1 = panel[4],panel[5]
    return abs(p1[0]-p0[0])+abs(p1[1]-p0[1])

def fraction_text(value):
    return f'{value.numerator}/{value.denominator}'

def payload(level,subdivide,panels):
    lines = [
        f'M1-EQ0|manifest=v1|level={level}|g=3/2|units=um|'
        f'subdivide={subdivide}|loops=signal,return|ordering=ccw'
    ]
    for index,(loop,edge,anchor,ordinal,p0,p1) in enumerate(panels):
        fields = (
            index,loop,edge,anchor,ordinal,
            fraction_text(p0[0]),fraction_text(p0[1]),
            fraction_text(p1[0]),fraction_text(p1[1]),
        )
        lines.append('|'.join(map(str,fields)))
    for loop in ('signal','return'):
        selected = [panel for panel in panels if panel[0] == loop]
        assert selected[-1][5] == selected[0][4]
        start = selected[0][4]
        lines.append(
            f'closure|{loop}|1|{fraction_text(start[0])}|{fraction_text(start[1])}'
        )
    data = '\n'.join(lines).encode('utf-8')
    return hashlib.sha256(data).hexdigest()

panels = seed_panels()
levels = []
for level,subdivide in (('seed',1),('medium',2),('fine',4)):
    for loop in ('signal','return'):
        selected = [panel for panel in panels if panel[0] == loop]
        lengths = [length(panel) for panel in selected]
        ratios = [
            max(a/b,b/a) for a,b in zip(lengths,lengths[1:]+lengths[:1])
        ]
        assert max(ratios) <= G
        twice_area = sum(
            panel[4][0]*panel[5][1]-panel[5][0]*panel[4][1]
            for panel in selected
        )
        assert twice_area/2 == F(8750)
    lengths = [length(panel) for panel in panels]
    facing = [length(panel) for panel in panels if panel[1] == 'facing']
    anchor_incident = []
    keys = sorted({(panel[0],panel[1],panel[2]) for panel in panels})
    for key in keys:
        selected = sorted(
            (panel for panel in panels if panel[:3] == key),
            key=lambda panel:panel[3],
        )
        anchor_incident.extend((length(selected[0]),length(selected[-1])))
    levels.append({
        'level':level,'N':len(panels),
        'min_um':float(min(lengths)),
        'max_um':float(max(lengths)),
        'max_anchor_start_um':float(max(anchor_incident)),
        'max_facing_um':float(max(facing)),
        'sha256':payload(level,subdivide,panels),
    })
    panels = refine(panels)

delta_um = sqrt(2/(2*pi*2e9*(4e-7*pi)*59.6e6))*1e6
assert levels[-1]['max_anchor_start_um'] <= delta_um/4
assert levels[-1]['max_facing_um'] <= 50/8
print(json.dumps({
    'delta_over_4_um':delta_um/4,
    'h_over_8_um':50/8,
    'levels':levels,
},indent=2))
'@ | python -
```

frozen hash는 seed `f65cddcf5d45187006ffc5e9eaf1c5624fa14e3849ce435c2601825da579743c`, medium `35d1f81c87cb97372c543db98e2063102b8823d5af0e70b8e5c4432966b77b02`, fine `5b964069b3bae0965ff3bfcc95348b98656fca9a48da3a998a0510892cd17d0e`다. fine의 모든 anchor-incident panel 중 최대는 `0.331753555 µm <= δ/4=0.364436622 µm`, max facing은 `5.419805710 µm <= h/8=6.25 µm`이고 full-contour adjacent growth는 exact Fraction에서 `<=3/2`다.

## T1 circle DtN `C0-A0` failure와 `C0-A1` canonical gate

아래 standalone block은 제품 module을 import하지 않고 [`T1_CIRCLE_DTN_RESULTS.md`](T1_CIRCLE_DTN_RESULTS.md)의 frozen A0 failure, A1 canonical spectral gate와 네 W1 full-dense spot을 재현한다. Python 3.12.10, NumPy 2.4.4, SciPy 1.18.0에서 실행했다. A1 kernel은 `hankel2e·exp(-jz)`를 사용하고, `C0=1` small self만 preregistered complex-log anchor를 사용한다. A0의 큰 `C0 J0` 항에는 그 asymptotic을 잘못 적용하지 않고 regularized integral을 그대로 계산한다.

```powershell
@'
import numpy as np
from math import pi
from numpy.polynomial.legendre import leggauss
from scipy.linalg import circulant, get_lapack_funcs, lu_factor, lu_solve
from scipy.special import hankel2e, jv, jve, yv

MU0 = 4e-7*pi
EPS0 = 8.8541878128e-12
SIGMA = 59.6e6
EULER = 0.5772156649015329
UROUND = 2.0**-53
LOG_MATERIALIZE = np.log(np.finfo(float).tiny)+4.0
DROP_RELATIVE_LIMIT = 1e-30
DROP_CERTIFICATES_LOG10 = []
DROP_SAMPLE_COUNT = 0

def scaled_h2(order, z):
    z = np.asarray(z, complex)
    scaled = hankel2e(order, z)
    if np.any(~np.isfinite(scaled)) or np.any(np.abs(scaled) == 0):
        raise RuntimeError('BLOCKED_HANKEL_RANGE: scaled value unavailable')
    logabs = np.log(np.abs(scaled))+np.imag(z)
    materialize = logabs >= LOG_MATERIALIZE
    value = np.zeros_like(z)
    value[materialize] = (
        np.exp(logabs[materialize])
        * np.exp(1j*(np.angle(scaled[materialize])-np.real(z[materialize])))
    )
    return value, logabs, materialize

def kernel(order, z, c0):
    if c0 == 1.0:
        return scaled_h2(order, z)[0]
    # Frozen A0 point is within the safe unscaled range.
    return c0*jv(order, z) - 1j*yv(order, z)

def dropped_relative_log10(log_terms, retained_norm):
    if not log_terms:
        return -np.inf
    log_bound = np.logaddexp.reduce(np.asarray(log_terms))
    return float((log_bound-np.log(retained_norm))/np.log(10.0))

def first_row(a, f, n, k, c0=1.0, q=20):
    global DROP_SAMPLE_COUNT
    omega = 2*pi*f
    dtheta = 2*pi/n
    half = a*np.sin(dtheta/2)
    rho = a*np.cos(dtheta/2)
    x, w = leggauss(q)
    U = np.empty(n, complex)
    P = np.empty(n, complex)
    U[0] = 1.0
    z = k*half
    if c0 == 1.0 and abs(z) <= 1e-3:
        P[0] = omega*MU0*half*(1-(2j/pi)*(np.log(z/2)+EULER-1))
    else:
        s = half*(x+1)/2
        ww = half*w/2
        if c0 == 1.0:
            h0, _, materialize = scaled_h2(0, k*s)
            if np.any(~materialize):
                raise RuntimeError('BLOCKED_HANKEL_RANGE: self term not materializable')
            regular = h0 + (2j/pi)*np.log(s/half)
        else:
            regular = kernel(0, k*s, c0) + (2j/pi)*np.log(s/half)
        P[0] = omega*MU0*(np.sum(ww*regular)+2j*half/pi)
    rm = np.array((rho, 0.0))
    dropped_p = []
    dropped_u = []
    for idx in range(1, n):
        theta = idx*dtheta
        midpoint = rho*np.array((np.cos(theta), np.sin(theta)))
        tangent = np.array((-np.sin(theta), np.cos(theta)))
        normal = np.array((np.cos(theta), np.sin(theta)))
        r = midpoint[:, None] + tangent[:, None]*(half*x)
        dr = r-rm[:, None]
        distance = np.sqrt(np.sum(dr*dr, axis=0))
        z = k*distance
        geometry = (dr.T@normal)/distance
        if c0 == 1.0:
            h0, log0, keep0 = scaled_h2(0, z)
            h1, log1, keep1 = scaled_h2(1, z)
            pcoef = np.abs(omega*MU0*half*w/2)
            ucoef = np.abs(k*half*w*geometry/2)
            dropped_p.extend((np.log(pcoef[~keep0])+log0[~keep0]).tolist())
            valid_u = (~keep1) & (ucoef > 0)
            dropped_u.extend((np.log(ucoef[valid_u])+log1[valid_u]).tolist())
            DROP_SAMPLE_COUNT += int(np.count_nonzero(~keep0)+np.count_nonzero(~keep1))
        else:
            h0 = kernel(0, z, c0)
            h1 = kernel(1, z, c0)
        P[idx] = omega*MU0*half/2*np.sum(w*h0)
        U[idx] = 1j*k*half/2*np.sum(w*geometry*h1)
    for logs, retained_norm in ((dropped_p, np.sum(np.abs(P))), (dropped_u, np.sum(np.abs(U)))):
        relative_log10 = dropped_relative_log10(logs, retained_norm)
        if np.isfinite(relative_log10):
            DROP_CERTIFICATES_LOG10.append(relative_log10)
            if relative_log10 > np.log10(DROP_RELATIVE_LIMIT):
                raise RuntimeError(('BLOCKED_HANKEL_RANGE', relative_log10))
    return U, P

def symbol(row, mode):
    n = len(row)
    return np.sum(row*np.exp(1j*2*pi*mode*np.arange(n)/n))

def jratio(mode, z):
    if mode == 0:
        return -jve(1, z)/jve(0, z)
    return mode/z-jve(mode+1, z)/jve(mode, z)

def setup(a, f, n, c0=1.0, q=20):
    omega = 2*pi*f
    kp = np.sqrt(omega*MU0*(omega*EPS0-1j*SIGMA))
    kb = omega*np.sqrt(MU0*EPS0)
    up, pp = first_row(a, f, n, kp, c0, q)
    ub, pb = first_row(a, f, n, kb, c0, q)
    return omega, kp, kb, up, pp, ub, pb

def values(a, f, n, modes, c0=1.0, q=20):
    omega, kp, kb, up, pp, ub, pb = setup(a, f, n, c0, q)
    out = []
    for mode in modes:
        m = abs(mode)
        exact = (
            kp/(1j*omega*MU0)*jratio(m, kp*a)
            - kb/(1j*omega*MU0)*jratio(m, kb*a)
        )
        numeric = symbol(up, mode)/symbol(pp, mode) - symbol(ub, mode)/symbol(pb, mode)
        out.append((exact, numeric))
    return out

def equilibrated_kappa1u(matrix):
    row_scale = 1/np.max(np.abs(matrix), axis=1)
    scaled = row_scale[:, None]*matrix
    col_scale = 1/np.max(np.abs(scaled), axis=0)
    equilibrated = scaled*col_scale[None, :]
    lu, _ = lu_factor(equilibrated, check_finite=False)
    gecon = get_lapack_funcs('gecon', (equilibrated,))
    rcond, info = gecon(lu, np.linalg.norm(equilibrated, 1), norm='1')
    if info != 0 or not np.isfinite(rcond) or rcond <= 0:
        raise RuntimeError(('gecon', info, rcond))
    return UROUND/rcond

def exact_circulant_kappa1u(row):
    eigenvalues = len(row)*np.fft.ifft(row)
    inverse_row = np.fft.fft(1/eigenvalues)/len(row)
    return float(np.sum(np.abs(row))*np.sum(np.abs(inverse_row))*UROUND)

def dense_spot(a, f, n=512, modes=range(9)):
    _, _, _, up, pp, ub, pb = setup(a, f, n, 1.0, 20)
    modes = np.asarray(tuple(modes))
    theta = 2*pi*np.arange(n)/n
    E = np.exp(1j*np.outer(theta, modes))
    backward = 0.0
    kappa_u = 0.0
    H = []
    for urow, prow in ((up, pp), (ub, pb)):
        U = circulant(urow).T
        P = circulant(prow).T
        rhs = U@E
        lu, piv = lu_factor(P, check_finite=False)
        h = lu_solve((lu, piv), rhs, check_finite=False)
        residual = P@h-rhs
        pnorm = np.linalg.norm(P, np.inf)
        for column in range(h.shape[1]):
            backward = max(
                backward,
                float(np.linalg.norm(residual[:, column], np.inf)/(
                    pnorm*np.linalg.norm(h[:, column], np.inf)
                    + np.linalg.norm(rhs[:, column], np.inf)
                )),
            )
        kappa_u = max(kappa_u, equilibrated_kappa1u(P))
        H.append(h)
    Y_on_modes = H[0]-H[1]
    modal = np.diag(E.conj().T@Y_on_modes)/np.diag(E.conj().T@E)
    spectral = np.asarray([
        symbol(up, int(m))/symbol(pp, int(m))
        - symbol(ub, int(m))/symbol(pb, int(m))
        for m in modes
    ])
    discrepancy = float(np.max(np.abs(modal-spectral)/np.abs(spectral)))
    return backward, kappa_u, discrepancy

# Immutable C0-A0 failure.
a0_medium = values(17.5e-6, 1e5, 256, (2,), 1e6)[0]
a0_fine = values(17.5e-6, 1e5, 512, (2,), 1e6)[0]
print('A0 exact', a0_fine[0])
print('A0 N512', a0_fine[1])
print('A0 fine_error_pct', 100*abs(a0_fine[1]-a0_fine[0])/abs(a0_fine[0]))
print('A0 mesh_pct', 100*abs(a0_fine[1]-a0_medium[1])/abs(a0_fine[1]))
print('A0 phase_deg', abs(np.angle(a0_fine[1]/a0_fine[0], deg=True)))
_, _, _, _, a0_p, _, a0_pb = setup(17.5e-6, 1e5, 512, 1e6, 20)
print('A0 max_exact_circulant_kappa1u', max(
    exact_circulant_kappa1u(a0_p), exact_circulant_kappa1u(a0_pb)
))

# C0-A1 canonical spectral gate.
errors, changes, phases, qchanges, canonical_kappa = [], [], [], [], []
for a in (17.5e-6, 500e-6):
    for f in (1e5, 1e6, 1e7, 1e8, 5e8, 1e9, 2e9):
        v256 = values(a, f, 256, range(5))
        v512 = values(a, f, 512, range(5))
        vq10 = values(a, f, 512, range(5), q=10)
        _, _, _, _, pp, _, pb = setup(a, f, 512, 1.0, 20)
        canonical_kappa.extend((exact_circulant_kappa1u(pp), exact_circulant_kappa1u(pb)))
        for (_, z256), (exact, z512), (_, z10) in zip(v256, v512, vq10):
            errors.append(abs(z512-exact)/abs(exact))
            changes.append(abs(z512-z256)/abs(z512))
            phases.append(abs(np.angle(z512/exact, deg=True)))
            qchanges.append(abs(z512-z10)/abs(z512))
print('A1 max_fine_error_pct', 100*max(errors))
print('A1 max_mesh_pct', 100*max(changes))
print('A1 max_phase_deg', max(phases))
print('A1 max_q_relative', max(qchanges))
print('A1 max_exact_circulant_kappa1u', max(canonical_kappa))

canonical_dense = [
    dense_spot(a, f, modes=range(5))
    for a in (17.5e-6, 500e-6)
    for f in (1e5, 1e6, 1e7, 1e8, 5e8, 1e9, 2e9)
]
print('A1 canonical max_dense_backward', max(item[0] for item in canonical_dense))

# W1 full-dense spots; wall/memory remain host-dependent.
spots = ((5e-6, 173e3), (1e-3, 173e6), (1e-3, 730e6), (1e-3, 1.73e9))
dense = [dense_spot(a, f) for a, f in spots]
print('W1 dense max_backward', max(item[0] for item in dense))
print('W1 dense max_kappa1u', max(item[1] for item in dense))
print('W1 dense max_symbol_discrepancy', max(item[2] for item in dense))

# Independent W3 geometry, now replayed through the same range guard.
w3_errors, w3_changes, w3_phases = [], [], []
w3_cases = [
    (a, f)
    for a in np.asarray((25, 75, 250, 750))*1e-6
    for f in np.asarray((0.2, 2, 20, 200, 800, 1400))*1e6
]
for a, f in w3_cases:
    v256 = values(a, f, 256, range(5, 9))
    v512 = values(a, f, 512, range(5, 9))
    for (_, z256), (exact, z512) in zip(v256, v512):
        w3_errors.append(abs(z512-exact)/abs(exact))
        w3_changes.append(abs(z512-z256)/abs(z512))
        w3_phases.append(abs(np.angle(z512/exact, deg=True)))
w3_dense = [dense_spot(a, f, modes=range(5, 9)) for a, f in w3_cases]
print('W3 max_fine_error_pct', 100*max(w3_errors))
print('W3 max_mesh_pct', 100*max(w3_changes))
print('W3 max_phase_deg', max(w3_phases))
print('W3 dense max_backward', max(item[0] for item in w3_dense))
print('W3 dense max_kappa1u', max(item[1] for item in w3_dense))
print('W3 dense max_symbol_discrepancy', max(item[2] for item in w3_dense))
print('range_guard_dropped_samples', DROP_SAMPLE_COUNT)
print('range_guard_max_relative_log10', max(DROP_CERTIFICATES_LOG10, default=-np.inf))
'@ | python -
```

핵심 재현값은 A0 exact `173.833308261-j0.052191983 S`, N512 `173.564355046+j5.364668010 S`, fine error `3.119961651%`, mesh change `9.323458492%`, phase `1.787583411°`, exact-circulant `κ1u=8.914401e-8`이다. A1 canonical의 max fine error `0.118541%`, mesh change `0.158276%`, phase `0.019939°`, q10→20 change `1.05602e-6`, exact-circulant `κ1u=9.753904e-13`, per-column normwise-infinity dense backward residual `1.2030e-15`도 재현된다. W1 range-guarded dense block은 residual `9.5414e-16`, `κ1u=1.014945e-12`; W3는 accuracy/mesh/phase `0.113044%/0.150516%/0.028220°`, dense residual `1.1541e-15`, `κ1u=9.164853e-13`을 출력한다. `LOG_MATERIALIZE=ln(tiny)+4` 아래의 H2 sample은 각 U/P first-row에 대해 dropped absolute quadrature bound를 log-sum하고 retained row 1-norm의 `1e-30` 이하일 때만 zero로 둔다. 최종 replay의 40,448 dropped order-sample contribution 중 worst relative log10 bound는 `-305.50`; 이를 넘으면 `BLOCKED_HANKEL_RANGE`다. BLAS별 반올림 차이를 허용하되 backward residual `<=1e-10`, `κ1u<=1e-8`과 dense/symbol discrepancy gate를 검사한다. archived checksums는 더 큰 case metadata serialization을 묶으므로 이 compact block의 stdout hash로 대체하지 않는다. `onenormest` warning은 prior inline run의 anomaly이며 이 exact block은 equilibrated LU의 LAPACK `gecon`을 사용해 explicit inverse 없이 condition certificate를 재현한다.

## T1-M1-EQ0 collocation SAO negative-result replay

먼저 위 exact panel manifest block에서 세 SHA-256을 확인한 뒤 아래 block을 실행한다. 이 block은 제품 module을 import하지 않으며 frozen collocation operator의 raw `Z'`, power failure, q10/q20과 `r0` invariance를 독립 재현한다. standalone block은 모든 solve를 deterministic equilibration 뒤 수행하고 matrix-wide residual을 보고하므로 최초 frozen 실행의 per-column/원 solve auxiliary metric과 숫자가 완전히 같지는 않다. archived gate 값과 standalone replay 값을 아래에서 분리한다. process counter는 host-dependent 참고값이며 결과 판정은 [`T1_M1_EQ0_RESULTS.md`](T1_M1_EQ0_RESULTS.md)를 따른다.

```powershell
$probe = @'
import ctypes as ct, hashlib, json, math, os, time
from fractions import Fraction as F
import numpy as np
from numpy.polynomial.legendre import leggauss
from scipy.linalg import block_diag, get_lapack_funcs, lu_factor, lu_solve
from scipy.special import hankel2e

MU0=4e-7*math.pi
EPS0=8.8541878128e-12
SIGMA=59.6e6
UROUND=2.0**-53
EULER=0.5772156649015329
LOGMAT=np.log(np.finfo(float).tiny)+4.0
GROWTH=F(3,2)

def memory_mib():
    if not hasattr(ct,'WinDLL'):
        return {}
    class PMC(ct.Structure):
        _fields_=[
          ('cb',ct.c_ulong),('pf',ct.c_ulong),('peak_ws',ct.c_size_t),
          ('ws',ct.c_size_t),('qpp',ct.c_size_t),('qp',ct.c_size_t),
          ('qnp',ct.c_size_t),('qn',ct.c_size_t),('page',ct.c_size_t),
          ('peak_page',ct.c_size_t),('private',ct.c_size_t),
        ]
    c=PMC(); c.cb=ct.sizeof(c)
    ps=ct.WinDLL('Psapi.dll'); k=ct.WinDLL('Kernel32.dll')
    k.GetCurrentProcess.restype=ct.c_void_p
    ps.GetProcessMemoryInfo.argtypes=(ct.c_void_p,ct.POINTER(PMC),ct.c_ulong)
    ps.GetProcessMemoryInfo.restype=ct.c_int
    ok=ps.GetProcessMemoryInfo(k.GetCurrentProcess(),ct.byref(c),c.cb)
    if not ok: return {}
    return {'working_set':c.ws/2**20,'peak_working_set':c.peak_ws/2**20,'private':c.private/2**20}

def interval(a,b,m):
    a,b=F(a),F(b); sign=1 if b>a else -1; half=abs(b-a)/2
    widths=[half*(GROWTH-1)*GROWTH**i/(GROWTH**m-1) for i in range(m)]
    left=[a]
    for width in widths: left.append(left[-1]+sign*width)
    right=[b]
    for width in widths: right.append(right[-1]-sign*width)
    points=left+list(reversed(right[:-1]))
    assert points[m]==(a+b)/2 and len(points)==2*m+1
    return points

def add(panels,conductor,edge,axis,fixed,anchors,m):
    for anchor,(a,b) in enumerate(zip(anchors,anchors[1:])):
        points=interval(a,b,m)
        for ordinal,(u,v) in enumerate(zip(points,points[1:])):
            p0,p1=((u,F(fixed)),(v,F(fixed))) if axis=='x' else ((F(fixed),u),(F(fixed),v))
            panels.append((conductor,edge,anchor,ordinal,p0,p1))

def seed_panels():
    p=[]
    add(p,0,'facing','x',50,[-125,0,125],8)
    add(p,0,'right','y',125,[50,85],5)
    add(p,0,'outer','x',85,[125,-125],10)
    add(p,0,'left','y',-125,[85,50],5)
    add(p,1,'outer','x',-35,[-125,125],10)
    add(p,1,'right','y',125,[-35,0],5)
    add(p,1,'facing','x',0,[125,0,-125],8)
    add(p,1,'left','y',-125,[0,-35],5)
    return p

def refine(p):
    out=[]
    for conductor,edge,anchor,ordinal,p0,p1 in p:
        mid=((p0[0]+p1[0])/2,(p0[1]+p1[1])/2)
        out += [(conductor,edge,anchor,2*ordinal,p0,mid),(conductor,edge,anchor,2*ordinal+1,mid,p1)]
    return out

def level_panels(level):
    p=seed_panels()
    for _ in range(level): p=refine(p)
    return p

def panel_arrays(p):
    endpoints=np.asarray([[[float(z)*1e-6 for z in x[4]],[float(z)*1e-6 for z in x[5]]] for x in p])
    delta=endpoints[:,1]-endpoints[:,0]
    lengths=np.linalg.norm(delta,axis=1)
    tangents=delta/lengths[:,None]
    normals=np.column_stack((tangents[:,1],-tangents[:,0]))
    midpoints=(endpoints[:,0]+endpoints[:,1])/2
    conductors=np.asarray([x[0] for x in p])
    return midpoints,tangents,normals,lengths,conductors

def scaled_h2(order,z):
    z=np.asarray(z,complex)
    scaled=hankel2e(order,z)
    if np.any(~np.isfinite(scaled)) or np.any(np.abs(scaled)==0):
        raise RuntimeError(('BLOCKED_HANKEL_RANGE',order))
    logabs=np.log(np.abs(scaled))+np.imag(z)
    if np.any(logabs<LOGMAT):
        raise RuntimeError(('BLOCKED_HANKEL_RANGE_NO_FLOOR',order,float(np.min(logabs))))
    return np.exp(logabs)*np.exp(1j*(np.angle(scaled)-np.real(z)))

def assemble_pu(mid,tangent,normal,length,omega,k,qorder):
    count=len(length)
    gx,gw=leggauss(qorder)
    P=np.empty((count,count),complex)
    U=np.empty((count,count),complex)
    for source in range(count):
        half=length[source]/2
        points=mid[source]+(half*gx)[:,None]*tangent[source]
        dr=points[None,:,:]-mid[:,None,:]
        distance=np.linalg.norm(dr,axis=2)
        rows=np.arange(count)!=source
        z=k*distance[rows]
        geometry=np.einsum('mqk,k->mq',dr[rows],normal[source])/distance[rows]
        P[rows,source]=omega*MU0*half/2*(scaled_h2(0,z)@gw)
        U[rows,source]=1j*k*half/2*((geometry*scaled_h2(1,z))@gw)
        ka=k*half
        if abs(ka)<=1e-3:
            P[source,source]=omega*MU0*half*(1-(2j/math.pi)*(np.log(ka/2)+EULER-1))
        else:
            s=half*(gx+1)/2
            weights=half*gw/2
            regular=scaled_h2(0,k*s)+(2j/math.pi)*np.log(s/half)
            P[source,source]=omega*MU0*(weights@regular+2j*half/math.pi)
        U[source,source]=1.0
    return P,U

def solve_cert(A,B):
    A=np.asarray(A,complex); B=np.asarray(B,complex)
    vector=B.ndim==1
    if vector: B=B[:,None]
    row=1/np.max(np.abs(A),axis=1)
    scaled=row[:,None]*A
    column=1/np.max(np.abs(scaled),axis=0)
    equilibrated=scaled*column[None,:]
    lu,piv=lu_factor(equilibrated,check_finite=False)
    y=lu_solve((lu,piv),row[:,None]*B,check_finite=False)
    x=column[:,None]*y
    residual=A@x-B
    denominator=np.linalg.norm(A,np.inf)*np.linalg.norm(x,np.inf)+np.linalg.norm(B,np.inf)
    backward=float(np.linalg.norm(residual,np.inf)/denominator)
    gecon=get_lapack_funcs('gecon',(equilibrated,))
    rcond,info=gecon(lu,np.linalg.norm(equilibrated,1),norm='1')
    if info or not np.isfinite(rcond) or rcond<=0: raise RuntimeError(('GECON',info,rcond))
    return (x[:,0] if vector else x),backward,float(UROUND/rcond)

def phi_log(u,c,log_r0):
    value=np.empty_like(u)
    mask=c>1e-18
    value[mask]=u[mask]*(.5*np.log(u[mask]**2+c[mask]**2)-log_r0)-u[mask]+c[mask]*np.arctan(u[mask]/c[mask])
    us=u[~mask]
    value[~mask]=np.where(np.abs(us)>0,us*(np.log(np.abs(us))-log_r0-1),0)
    return value

def assemble_g0(mid,tangent,length,r0):
    count=len(length)
    G0=np.empty((count,count),float)
    log_r0=math.log(r0)
    for source in range(count):
        d=mid-mid[source]
        projection=d@tangent[source]
        perpendicular=d-projection[:,None]*tangent[source]
        distance=np.linalg.norm(perpendicular,axis=1)
        half=length[source]/2
        u0=-half-projection
        u1=half-projection
        G0[:,source]=(phi_log(u1,distance,log_r0)-phi_log(u0,distance,log_r0))/(2*math.pi)
        G0[source,source]=length[source]/(2*math.pi)*(math.log(length[source]/(2*r0))-1)
    return G0

def weighted_symmetry(matrix,length):
    weighted=length[:,None]*matrix
    return float(np.linalg.norm(weighted-weighted.T)/max(np.linalg.norm(weighted),1e-300))

def interior_operator(mid,tangent,normal,length,omega,kp,kb,qorder):
    pp,up=assemble_pu(mid,tangent,normal,length,omega,kp,qorder)
    pb,ub=assemble_pu(mid,tangent,normal,length,omega,kb,qorder)
    dp,rpp,kpp=solve_cert(pp,up)
    db,rpb,kpb=solve_cert(pb,ub)
    ys=dp-db
    return ys,{
      'P_backward':rpp,'Pout_backward':rpb,
      'P_kappa_u':kpp,'Pout_kappa_u':kpb,
      'Ys_weighted_reciprocity':weighted_symmetry(ys,length),
    }

def run_case(level,frequency,qorder,r0s):
    start=time.perf_counter()
    p=level_panels(level)
    mid,tangent,normal,length,conductor=panel_arrays(p)
    omega=2*math.pi*frequency
    kp=np.sqrt(omega*MU0*(omega*EPS0-1j*SIGMA))
    kb=omega*math.sqrt(MU0*EPS0)
    blocks=[]; certificates=[]
    for c in (0,1):
        ix=np.flatnonzero(conductor==c)
        ys,cert=interior_operator(mid[ix],tangent[ix],normal[ix],length[ix],omega,kp,kb,qorder)
        blocks.append(ys); certificates.append(cert)
    Ys=block_diag(*blocks)
    Q=np.column_stack((conductor==0,conductor==1)).astype(float)
    b=np.asarray((1.,-1.))
    results={}
    for r0 in r0s:
        G0=assemble_g0(mid,tangent,length,r0)
        exterior_weighted_reciprocity=weighted_symmetry(G0,length)
        A=np.eye(len(length))-1j*omega*MU0*(Ys@G0)
        X,Aback,Akappa=solve_cert(A,Ys@Q)
        K=Q.T@(length[:,None]*X)
        Z,Kback,Kkappa=solve_cert(K,np.eye(2))
        V=Z@b
        J=X@V
        E=Q@V+1j*omega*MU0*(G0@J)
        integrated=Q.T@(length*J)
        terminal=.5*np.vdot(b,V)
        boundary=.5*np.vdot(E,length*J)
        loop=complex(b@Z@b)
        results[str(r0)]={
          'Zloop_ohm_per_m':[loop.real,loop.imag],
          'R_ohm_per_m':loop.real,
          'L_h_per_m':loop.imag/omega,
          'terminal_reciprocity':float(np.linalg.norm(Z-Z.T)/max(np.linalg.norm(Z),1e-300)),
          'current_residual':float(np.linalg.norm(integrated-b)/np.linalg.norm(b)),
          'zero_sum_residual':float(abs(np.sum(integrated))/np.linalg.norm(b)),
          'dissipative_power_residual':float(abs(terminal.real-boundary.real)/max(abs(terminal.real),abs(boundary.real),1e-18)),
          'terminal_power':[terminal.real,terminal.imag],
          'boundary_power':[boundary.real,boundary.imag],
          'A_backward':Aback,'A_kappa_u':Akappa,
          'K_backward':Kback,'K_kappa_u':Kkappa,
          'G0_weighted_reciprocity':exterior_weighted_reciprocity,
          'passivity_margin':loop.real,
        }
    allcert=[v for c in certificates for k,v in c.items() if k.endswith(('backward','kappa_u'))]
    return {
      'level':level,'N':len(length),'frequency_hz':frequency,'q':qorder,
      'kp':[kp.real,kp.imag],'kb':kb,
      'interior':certificates,'r0':results,
      'max_linear_certificate':max(allcert),
      'seconds':time.perf_counter()-start,'memory_mib':memory_mib(),
    }

levels=[int(x) for x in os.environ.get('M1_LEVELS','0').split(',') if x]
frequencies=[float(x) for x in os.environ.get('M1_FREQS','1e8').split(',') if x]
qorders=[int(x) for x in os.environ.get('M1_QS','20').split(',') if x]
r0s=[float(x) for x in os.environ.get('M1_R0S','0.1,1,10').split(',') if x]
for level in levels:
    for frequency in frequencies:
        for qorder in qorders:
            print(json.dumps(run_case(level,frequency,qorder,r0s),separators=(',',':'),allow_nan=False))
'@

$env:M1_LEVELS='0,1,2'
$env:M1_FREQS='1e5,1e6,1e7,1e8,5e8,1e9,2e9'
$env:M1_QS='20'
$env:M1_R0S='1'
$probe | python -

# Fine q10 parity and q20 reference-radius invariance.
$env:M1_LEVELS='2'
$env:M1_QS='10,20'
$env:M1_R0S='0.1,1,10'
$probe | python -
```

첫 실행은 3×7 raw q20 table을 출력한다. frozen run의 medium→fine log-RMS/max/phase는 `0.00703814%/0.0130658%/0.00159923°`, standalone fine q10→q20 max는 `9.719097658e-9`, `κ1u=9.213551638e-13`, `r0` invariance `9.59635e-16`으로 재현된다. standalone의 auxiliary max backward/terminal reciprocity와 fine 2 GHz current residual은 각각 `5.897094807e-16`, `7.661105753e-16`, `2.674969819e-15`; 최초 frozen gate table의 다른 집계 정의값은 [`T1_M1_EQ0_RESULTS.md`](T1_M1_EQ0_RESULTS.md)에 그대로 보존한다. 판정에 결정적인 raw `Z'`와 fine dissipative-power mismatch `3.719e-8`에서 `1.585e-4`는 일치하며 mandatory `1e-8` gate를 실패한다. 이 stdout은 negative evidence이며 matrix 대칭화나 C0 tuning을 적용하지 않는다.

## T1-M1-EQ0-G1 direct Galerkin exterior preregistration (historical frozen block)

아래 standalone block은 collocation negative result를 보존한 뒤, **G1 결과를 보기 전에** direct double-panel exterior의 exact self, independently evaluated pair, analytic-radial Duffy, q10/q20 normalization, `r0` rank-one identity, weak equation과 prospective interior metric을 실행 가능한 형태로 고정한 것이다. 제품 module을 import하지 않으며 `GG`나 `Yw`를 사후 평균하지 않는다. 첫 full run은 process-tree 4 GiB 목표, private/commit 5 GiB stop과 system headroom floor를 외부 monitor로 함께 적용한다.

```powershell
$g1 = @'
import ctypes as ct, hashlib, json, math, os, time
from fractions import Fraction as F
import numpy as np
from numpy.polynomial.legendre import leggauss
from scipy.linalg import block_diag, get_lapack_funcs, lu_factor, lu_solve
from scipy.special import hankel2e

MU0=4e-7*math.pi
EPS0=8.8541878128e-12
SIGMA=59.6e6
UROUND=2.0**-53
EULER=0.5772156649015329
LOGMAT=np.log(np.finfo(float).tiny)+4.0
GROWTH=F(3,2)

def memory_mib():
    if not hasattr(ct,'WinDLL'):
        return {}
    class PMC(ct.Structure):
        _fields_=[
          ('cb',ct.c_ulong),('pf',ct.c_ulong),('peak_ws',ct.c_size_t),
          ('ws',ct.c_size_t),('qpp',ct.c_size_t),('qp',ct.c_size_t),
          ('qnp',ct.c_size_t),('qn',ct.c_size_t),('page',ct.c_size_t),
          ('peak_page',ct.c_size_t),('private',ct.c_size_t),
        ]
    c=PMC(); c.cb=ct.sizeof(c)
    ps=ct.WinDLL('Psapi.dll'); k=ct.WinDLL('Kernel32.dll')
    k.GetCurrentProcess.restype=ct.c_void_p
    ps.GetProcessMemoryInfo.argtypes=(ct.c_void_p,ct.POINTER(PMC),ct.c_ulong)
    ps.GetProcessMemoryInfo.restype=ct.c_int
    ok=ps.GetProcessMemoryInfo(k.GetCurrentProcess(),ct.byref(c),c.cb)
    if not ok: return {}
    return {'working_set':c.ws/2**20,'peak_working_set':c.peak_ws/2**20,'private':c.private/2**20}

def interval(a,b,m):
    a,b=F(a),F(b); sign=1 if b>a else -1; half=abs(b-a)/2
    widths=[half*(GROWTH-1)*GROWTH**i/(GROWTH**m-1) for i in range(m)]
    left=[a]
    for width in widths: left.append(left[-1]+sign*width)
    right=[b]
    for width in widths: right.append(right[-1]-sign*width)
    points=left+list(reversed(right[:-1]))
    assert points[m]==(a+b)/2 and len(points)==2*m+1
    return points

def add(panels,conductor,edge,axis,fixed,anchors,m):
    for anchor,(a,b) in enumerate(zip(anchors,anchors[1:])):
        points=interval(a,b,m)
        for ordinal,(u,v) in enumerate(zip(points,points[1:])):
            p0,p1=((u,F(fixed)),(v,F(fixed))) if axis=='x' else ((F(fixed),u),(F(fixed),v))
            panels.append((conductor,edge,anchor,ordinal,p0,p1))

def seed_panels():
    p=[]
    add(p,0,'facing','x',50,[-125,0,125],8)
    add(p,0,'right','y',125,[50,85],5)
    add(p,0,'outer','x',85,[125,-125],10)
    add(p,0,'left','y',-125,[85,50],5)
    add(p,1,'outer','x',-35,[-125,125],10)
    add(p,1,'right','y',125,[-35,0],5)
    add(p,1,'facing','x',0,[125,0,-125],8)
    add(p,1,'left','y',-125,[0,-35],5)
    return p

def refine(p):
    out=[]
    for conductor,edge,anchor,ordinal,p0,p1 in p:
        mid=((p0[0]+p1[0])/2,(p0[1]+p1[1])/2)
        out += [(conductor,edge,anchor,2*ordinal,p0,mid),(conductor,edge,anchor,2*ordinal+1,mid,p1)]
    return out

def level_panels(level):
    p=seed_panels()
    for _ in range(level): p=refine(p)
    return p

def panel_arrays(p):
    endpoints=np.asarray([[[float(z)*1e-6 for z in x[4]],[float(z)*1e-6 for z in x[5]]] for x in p])
    delta=endpoints[:,1]-endpoints[:,0]
    lengths=np.linalg.norm(delta,axis=1)
    tangents=delta/lengths[:,None]
    normals=np.column_stack((tangents[:,1],-tangents[:,0]))
    midpoints=(endpoints[:,0]+endpoints[:,1])/2
    conductors=np.asarray([x[0] for x in p])
    return midpoints,tangents,normals,lengths,conductors

def scaled_h2(order,z):
    z=np.asarray(z,complex)
    scaled=hankel2e(order,z)
    if np.any(~np.isfinite(scaled)) or np.any(np.abs(scaled)==0):
        raise RuntimeError(('BLOCKED_HANKEL_RANGE',order))
    logabs=np.log(np.abs(scaled))+np.imag(z)
    if np.any(logabs<LOGMAT):
        raise RuntimeError(('BLOCKED_HANKEL_RANGE_NO_FLOOR',order,float(np.min(logabs))))
    return np.exp(logabs)*np.exp(1j*(np.angle(scaled)-np.real(z)))

def assemble_pu(mid,tangent,normal,length,omega,k,qorder):
    count=len(length)
    gx,gw=leggauss(qorder)
    P=np.empty((count,count),complex)
    U=np.empty((count,count),complex)
    for source in range(count):
        half=length[source]/2
        points=mid[source]+(half*gx)[:,None]*tangent[source]
        dr=points[None,:,:]-mid[:,None,:]
        distance=np.linalg.norm(dr,axis=2)
        rows=np.arange(count)!=source
        z=k*distance[rows]
        geometry=np.einsum('mqk,k->mq',dr[rows],normal[source])/distance[rows]
        P[rows,source]=omega*MU0*half/2*(scaled_h2(0,z)@gw)
        U[rows,source]=1j*k*half/2*((geometry*scaled_h2(1,z))@gw)
        ka=k*half
        if abs(ka)<=1e-3:
            P[source,source]=omega*MU0*half*(1-(2j/math.pi)*(np.log(ka/2)+EULER-1))
        else:
            s=half*(gx+1)/2
            weights=half*gw/2
            regular=scaled_h2(0,k*s)+(2j/math.pi)*np.log(s/half)
            P[source,source]=omega*MU0*(weights@regular+2j*half/math.pi)
        U[source,source]=1.0
    return P,U

def solve_cert(A,B):
    A=np.asarray(A,complex); B=np.asarray(B,complex)
    vector=B.ndim==1
    if vector: B=B[:,None]
    row=1/np.max(np.abs(A),axis=1)
    scaled=row[:,None]*A
    column=1/np.max(np.abs(scaled),axis=0)
    equilibrated=scaled*column[None,:]
    lu,piv=lu_factor(equilibrated,check_finite=False)
    y=lu_solve((lu,piv),row[:,None]*B,check_finite=False)
    x=column[:,None]*y
    residual=A@x-B
    denominator=np.linalg.norm(A,np.inf)*np.linalg.norm(x,np.inf)+np.linalg.norm(B,np.inf)
    backward=float(np.linalg.norm(residual,np.inf)/denominator)
    gecon=get_lapack_funcs('gecon',(equilibrated,))
    rcond,info=gecon(lu,np.linalg.norm(equilibrated,1),norm='1')
    if info or not np.isfinite(rcond) or rcond<=0: raise RuntimeError(('GECON',info,rcond))
    return (x[:,0] if vector else x),backward,float(UROUND/rcond)

def phi_log(u,c,log_r0):
    value=np.empty_like(u)
    mask=c>1e-18
    value[mask]=u[mask]*(.5*np.log(u[mask]**2+c[mask]**2)-log_r0)-u[mask]+c[mask]*np.arctan(u[mask]/c[mask])
    us=u[~mask]
    value[~mask]=np.where(np.abs(us)>0,us*(np.log(np.abs(us))-log_r0-1),0)
    return value

def assemble_g0(mid,tangent,length,r0):
    count=len(length)
    G0=np.empty((count,count),float)
    log_r0=math.log(r0)
    for source in range(count):
        d=mid-mid[source]
        projection=d@tangent[source]
        perpendicular=d-projection[:,None]*tangent[source]
        distance=np.linalg.norm(perpendicular,axis=1)
        half=length[source]/2
        u0=-half-projection
        u1=half-projection
        G0[:,source]=(phi_log(u1,distance,log_r0)-phi_log(u0,distance,log_r0))/(2*math.pi)
        G0[source,source]=length[source]/(2*math.pi)*(math.log(length[source]/(2*r0))-1)
    return G0

def weighted_symmetry(matrix,length):
    weighted=length[:,None]*matrix
    return float(np.linalg.norm(weighted-weighted.T)/max(np.linalg.norm(weighted),1e-300))

def interior_operator(mid,tangent,normal,length,omega,kp,kb,qorder):
    pp,up=assemble_pu(mid,tangent,normal,length,omega,kp,qorder)
    pb,ub=assemble_pu(mid,tangent,normal,length,omega,kb,qorder)
    dp,rpp,kpp=solve_cert(pp,up)
    db,rpb,kpb=solve_cert(pb,ub)
    ys=dp-db
    return ys,{
      'P_backward':rpp,'Pout_backward':rpb,
      'P_kappa_u':kpp,'Pout_kappa_u':kpb,
      'Ys_weighted_reciprocity':weighted_symmetry(ys,length),
    }

def run_case(level,frequency,qorder,r0s):
    start=time.perf_counter()
    p=level_panels(level)
    mid,tangent,normal,length,conductor=panel_arrays(p)
    omega=2*math.pi*frequency
    kp=np.sqrt(omega*MU0*(omega*EPS0-1j*SIGMA))
    kb=omega*math.sqrt(MU0*EPS0)
    blocks=[]; certificates=[]
    for c in (0,1):
        ix=np.flatnonzero(conductor==c)
        ys,cert=interior_operator(mid[ix],tangent[ix],normal[ix],length[ix],omega,kp,kb,qorder)
        blocks.append(ys); certificates.append(cert)
    Ys=block_diag(*blocks)
    Q=np.column_stack((conductor==0,conductor==1)).astype(float)
    b=np.asarray((1.,-1.))
    results={}
    for r0 in r0s:
        G0=assemble_g0(mid,tangent,length,r0)
        exterior_weighted_reciprocity=weighted_symmetry(G0,length)
        A=np.eye(len(length))-1j*omega*MU0*(Ys@G0)
        X,Aback,Akappa=solve_cert(A,Ys@Q)
        K=Q.T@(length[:,None]*X)
        Z,Kback,Kkappa=solve_cert(K,np.eye(2))
        V=Z@b
        J=X@V
        E=Q@V+1j*omega*MU0*(G0@J)
        integrated=Q.T@(length*J)
        terminal=.5*np.vdot(b,V)
        boundary=.5*np.vdot(E,length*J)
        loop=complex(b@Z@b)
        results[str(r0)]={
          'Zloop_ohm_per_m':[loop.real,loop.imag],
          'R_ohm_per_m':loop.real,
          'L_h_per_m':loop.imag/omega,
          'terminal_reciprocity':float(np.linalg.norm(Z-Z.T)/max(np.linalg.norm(Z),1e-300)),
          'current_residual':float(np.linalg.norm(integrated-b)/np.linalg.norm(b)),
          'zero_sum_residual':float(abs(np.sum(integrated))/np.linalg.norm(b)),
          'dissipative_power_residual':float(abs(terminal.real-boundary.real)/max(abs(terminal.real),abs(boundary.real),1e-18)),
          'terminal_power':[terminal.real,terminal.imag],
          'boundary_power':[boundary.real,boundary.imag],
          'A_backward':Aback,'A_kappa_u':Akappa,
          'K_backward':Kback,'K_kappa_u':Kkappa,
          'G0_weighted_reciprocity':exterior_weighted_reciprocity,
          'passivity_margin':loop.real,
        }
    allcert=[v for c in certificates for k,v in c.items() if k.endswith(('backward','kappa_u'))]
    return {
      'level':level,'N':len(length),'frequency_hz':frequency,'q':qorder,
      'kp':[kp.real,kp.imag],'kb':kb,
      'interior':certificates,'r0':results,
      'max_linear_certificate':max(allcert),
      'seconds':time.perf_counter()-start,'memory_mib':memory_mib(),
    }
_GG_CACHE={}

def g1_geometry(level):
    panels=level_panels(level)
    mid,tangent,normal,length,conductor=panel_arrays(panels)
    p0=mid-.5*length[:,None]*tangent
    p1=mid+.5*length[:,None]*tangent
    return panels,mid,tangent,normal,length,conductor,p0,p1

def g1_pair_integral(i,j,p0,p1,length,qorder,r0):
    li,lj=length[i],length[j]
    if i==j:
        return li*li/(2*math.pi)*(math.log(li/r0)-1.5)
    shared=None
    for a in (p0[i],p1[i]):
        for b in (p0[j],p1[j]):
            if np.linalg.norm(a-b)<=1e-18:
                shared=(a,b)
                break
        if shared is not None:
            break
    x,w=leggauss(qorder)
    u=(x+1)/2
    ww=w/2
    if shared is not None:
        c=shared[0]
        oi=p1[i] if np.linalg.norm(p0[i]-c)<=1e-18 else p0[i]
        oj=p1[j] if np.linalg.norm(p0[j]-c)<=1e-18 else p0[j]
        ti=(oi-c)/li
        tj=(oj-c)/lj
        A=np.linalg.norm(li*ti[None,:]-u[:,None]*lj*tj[None,:],axis=1)
        B=np.linalg.norm(u[:,None]*li*ti[None,:]-lj*tj[None,:],axis=1)
        if np.any(A<=0) or np.any(B<=0):
            raise RuntimeError(('G1_DUFFY_DEGENERATE',i,j))
        return li*lj/(4*math.pi)*np.sum(ww*(np.log(A/r0)+np.log(B/r0)-1))
    ri=p0[i][None,:]+u[:,None]*(p1[i]-p0[i])[None,:]
    rj=p0[j][None,:]+u[:,None]*(p1[j]-p0[j])[None,:]
    distance=np.linalg.norm(ri[:,None,:]-rj[None,:,:],axis=2)
    if np.any(distance<=0):
        raise RuntimeError(('G1_NONTOUCHING_ZERO_DISTANCE',i,j))
    return li*lj/(2*math.pi)*np.sum(ww[:,None]*ww[None,:]*np.log(distance/r0))

def assemble_gg(level,qorder,r0):
    key=(level,qorder,float(r0))
    if key in _GG_CACHE:
        return _GG_CACHE[key]
    _,mid,tangent,normal,length,conductor,p0,p1=g1_geometry(level)
    GG=np.empty((len(length),len(length)),float)
    for i in range(len(length)):
        for j in range(len(length)):
            GG[i,j]=g1_pair_integral(i,j,p0,p1,length,qorder,r0)
    _GG_CACHE[key]=(GG,length)
    return GG,length

def g1_structural_certificate(level):
    G10,length=assemble_gg(level,10,1.0)
    G20,_=assemble_gg(level,20,1.0)
    natural=length[:,None]*length[None,:]/(2*math.pi)
    denominator=np.maximum.reduce((np.abs(G10),np.abs(G20),natural))
    qmax=float(np.max(np.abs(G20-G10)/denominator))
    qfro=float(np.linalg.norm(G20-G10)/max(np.linalg.norm(G20),1e-300))
    pair=float(np.max(np.abs(G20-G20.T)/np.maximum.reduce((np.abs(G20),np.abs(G20.T),natural))))
    symmetry=float(np.linalg.norm(G20-G20.T)/max(np.linalg.norm(G20),1e-300))
    radius={}
    ell=length[:,None]
    for r0 in (.1,10.):
        Gr,_=assemble_gg(level,20,r0)
        exact=-math.log(r0)/(2*math.pi)*(ell@ell.T)
        radius[str(r0)]=float(np.linalg.norm((Gr-G20)-exact)/max(np.linalg.norm(exact),1e-300))
    return {
      'level':level,'N':len(length),
      'q10_q20_max_natural':qmax,'q10_q20_frobenius':qfro,
      'independent_pair_transpose_max':pair,'raw_transpose_frobenius':symmetry,
      'r0_rank_one_relative':radius,
      'GG_q20_sha256':hashlib.sha256(np.ascontiguousarray(G20).tobytes()).hexdigest(),
    }

def g1_interior_operator(mid,tangent,normal,length,omega,kp,kb,qorder):
    ys,cert=interior_operator(mid,tangent,normal,length,omega,kp,kb,qorder)
    Yw=length[:,None]*ys
    floor=max(1e-12,1e-10*float(np.max(np.abs(Yw))))
    metric=float(np.linalg.norm(Yw-Yw.T)/max(np.linalg.norm(Yw),len(length)*floor))
    hermitian=(Yw+Yw.conj().T)/2
    min_eigenvalue=float(np.linalg.eigvalsh(hermitian)[0])
    passivity_tolerance=max(floor,1e-9*float(np.linalg.norm(Yw,2)))
    cert=dict(cert)
    cert.update({
      'weak_admittance_floor_S_m':floor,
      'prospective_weighted_reciprocity':metric,
      'prospective_weighted_reciprocity_status':'pass' if metric<=1e-8 else 'diagnostic_fail',
      'prospective_min_hermitian_eigenvalue_S_m':min_eigenvalue,
      'prospective_passivity_tolerance_S_m':passivity_tolerance,
      'prospective_passivity_status':'pass' if min_eigenvalue>=-passivity_tolerance else 'diagnostic_fail',
    })
    return ys,cert

def run_g1_case(level,frequency,qorder,r0s):
    start=time.perf_counter()
    _,mid,tangent,normal,length,conductor,p0,p1=g1_geometry(level)
    omega=2*math.pi*frequency
    kp=np.sqrt(omega*MU0*(omega*EPS0-1j*SIGMA))
    kb=omega*math.sqrt(MU0*EPS0)
    blocks=[]; certificates=[]
    for c in (0,1):
        ix=np.flatnonzero(conductor==c)
        ys,cert=g1_interior_operator(
            mid[ix],tangent[ix],normal[ix],length[ix],omega,kp,kb,qorder
        )
        blocks.append(ys); certificates.append(cert)
    Ys=block_diag(*blocks)
    Q=np.column_stack((conductor==0,conductor==1)).astype(float)
    WQ=length[:,None]*Q
    b=np.asarray((1.,-1.))
    results={}
    for r0 in r0s:
        GG,_=assemble_gg(level,qorder,r0)
        AE=np.diag(length)-1j*omega*MU0*(GG@Ys)
        Eresponse,AEback,AEkappa=solve_cert(AE,WQ)
        Jresponse=Ys@Eresponse
        K=Q.T@(length[:,None]*Jresponse)
        Z,Kback,Kkappa=solve_cert(K,np.eye(2))
        V=Z@b
        E=Eresponse@V
        J=Jresponse@V
        integrated=Q.T@(length*J)
        terminal=.5*np.vdot(b,V)
        boundary=.5*np.vdot(E,length*J)
        loop=complex(b@Z@b)
        results[str(r0)]={
          'Zloop_ohm_per_m':[loop.real,loop.imag],
          'R_ohm_per_m':loop.real,
          'L_h_per_m':loop.imag/omega,
          'terminal_reciprocity':float(np.linalg.norm(Z-Z.T)/max(np.linalg.norm(Z),1e-300)),
          'current_residual':float(np.linalg.norm(integrated-b)/np.linalg.norm(b)),
          'zero_sum_residual':float(abs(np.sum(integrated))/np.linalg.norm(b)),
          'dissipative_power_residual':float(abs(terminal.real-boundary.real)/max(abs(terminal.real),abs(boundary.real),1e-18)),
          'terminal_power':[terminal.real,terminal.imag],
          'boundary_power':[boundary.real,boundary.imag],
          'AE_backward':AEback,'AE_kappa_u':AEkappa,
          'K_backward':Kback,'K_kappa_u':Kkappa,
          'GG_raw_transpose':float(np.linalg.norm(GG-GG.T)/max(np.linalg.norm(GG),1e-300)),
          'passivity_margin':loop.real,
        }
    return {
      'case':'M1-EQ0-G1','level':level,'N':len(length),
      'frequency_hz':frequency,'q':qorder,
      'kp':[kp.real,kp.imag],'kb':kb,
      'interior':certificates,'r0':results,
      'seconds':time.perf_counter()-start,'memory_mib':memory_mib(),
    }

g1_levels=[int(x) for x in os.environ.get('M1_G1_LEVELS','0').split(',') if x]
g1_frequencies=[float(x) for x in os.environ.get('M1_G1_FREQS','2e9').split(',') if x]
g1_qorders=[int(x) for x in os.environ.get('M1_G1_QS','20').split(',') if x]
g1_r0s=[float(x) for x in os.environ.get('M1_G1_R0S','1').split(',') if x]
for level in g1_levels:
    print(json.dumps({'case':'M1-EQ0-G1-structure',**g1_structural_certificate(level)},separators=(',',':'),allow_nan=False))
    for frequency in g1_frequencies:
        for qorder in g1_qorders:
            print(json.dumps(run_g1_case(level,frequency,qorder,g1_r0s),separators=(',',':'),allow_nan=False))
'@

$env:M1_G1_LEVELS='0,1,2'
$env:M1_G1_FREQS='1e5,1e6,1e7,1e8,5e8,1e9,2e9'
$env:M1_G1_QS='20'
$env:M1_G1_R0S='0.1,1,10'
$g1 | python -

# Mandatory final-response quadrature parity on the fine contour.
$env:M1_G1_LEVELS='2'
$env:M1_G1_QS='10,20'
$env:M1_G1_R0S='1'
$g1 | python -
```

## M1-EQ0-G2 target-tested interior Galerkin preregistration

아래 block은 위 G1의 frozen geometry, branch, scaled Hankel, solve certificate와 direct exterior `GG`를 그대로 재사용하고 interior `P/U/Pout/Uout`만 pulse Galerkin weak trace/flux로 교체한다. 결과 matrix 평균, eigenvalue clipping과 C0 retuning은 없다. `Pᴳ`는 symmetric single-layer이지만 `Uᴳ` 자체의 algebraic symmetry는 요구하지 않는다. first bounded run은 100 kHz/2 GHz self·touching pair, circle analytic DtN, EQ0 seed만 실행하고 hidden-mode gate가 실패하면 fine으로 확장하지 않는다.

```powershell
# Reconstruct the frozen G1 definition from this document so the G2 block can
# be executed in a fresh PowerShell session without first running the G1 sweep.
$oraclePath = Resolve-Path 'docs/evaluation-research/ORACLE_REPRODUCTION.md'
$oracleLines = Get-Content -LiteralPath $oraclePath -Encoding utf8
$g1Start = [Array]::IndexOf($oracleLines, '$g1 = @''')
if ($g1Start -lt 0) { throw 'G2_G1_START_NOT_FOUND' }
$g1End = -1
for ($i = $g1Start + 1; $i -lt $oracleLines.Count; $i++) {
    if ($oracleLines[$i] -eq "'@") { $g1End = $i; break }
}
if ($g1End -le $g1Start + 1) { throw 'G2_G1_END_NOT_FOUND' }
$g1 = $oracleLines[($g1Start + 1)..($g1End - 1)] -join "`n"
$g2 = $g1.Substring(0,$g1.IndexOf('g1_levels=')) + @'
from scipy.special import jve

G2_NEAR_RATIO=1.0
G2_NEAR_MAX_DEPTH=8

def g2_endpoints(mid,tangent,length):
    return mid-.5*length[:,None]*tangent,mid+.5*length[:,None]*tangent

def g2_point_segment_distance(p,a,b):
    d=b-a
    t=float(np.clip(np.dot(p-a,d)/np.dot(d,d),0.0,1.0))
    return float(np.linalg.norm(p-(a+t*d)))

def g2_segment_distance(a0,a1,b0,b1):
    return min(
      g2_point_segment_distance(a0,b0,b1),
      g2_point_segment_distance(a1,b0,b1),
      g2_point_segment_distance(b0,a0,a1),
      g2_point_segment_distance(b1,a0,a1),
    )

def g2_cross2(a,b):
    return float(a[0]*b[1]-a[1]*b[0])

def g2_proper_intersection(a0,a1,b0,b1):
    da=a1-a0; db=b1-b0
    scale=max(float(np.linalg.norm(da)),float(np.linalg.norm(db)),1e-300)
    tol=1e-14*scale*scale
    o0=g2_cross2(da,b0-a0); o1=g2_cross2(da,b1-a0)
    o2=g2_cross2(db,a0-b0); o3=g2_cross2(db,a1-b0)
    return o0*o1 < -tol*tol and o2*o3 < -tol*tol

def g2_shared_endpoint(a0,a1,b0,b1):
    for a in (a0,a1):
        for b in (b0,b1):
            if np.linalg.norm(a-b)<=1e-18:
                return a
    return None

def g2_self_integrals(length,omega,k,mu,qorder):
    x,w=leggauss(qorder)
    u=length*(x+1)/2
    ww=length*w/2
    regular=scaled_h2(0,k*u)+(2j/math.pi)*np.log(u/length)
    P=omega*mu*(np.sum(ww*(length-u)*regular)+3j*length*length/(2*math.pi))
    U=complex(length)
    small=(omega*mu*length*length/2)*(
      1-(2j/math.pi)*(np.log(k*length/2)+EULER-1.5)
    )
    small_error=(
      float(abs(P-small)/max(abs(P),1e-300))
      if abs(k*length)<=1e-3 else None
    )
    return P,U,small_error

def g2_duffy_map(a,b,source_normal,omega,k,mu,qorder,which):
    x,w=leggauss(qorder)
    rho=(x+1)/2; wr=w/2
    eta=(x+1)/2; we=w/2
    if which==0:
        D=eta[:,None]*b[None,:]-a[None,:]
    else:
        D=b[None,:]-eta[:,None]*a[None,:]
    dnorm=np.linalg.norm(D,axis=1)
    if np.any(dnorm<=0):
        raise RuntimeError(('G2_DUFFY_DEGENERATE',which))
    R=rho[:,None]
    distance=R*dnorm[None,:]
    h0=scaled_h2(0,k*distance)
    preg=h0+(2j/math.pi)*np.log(R)
    pint=float(np.linalg.norm(a))*float(np.linalg.norm(b))*(
      np.sum(wr[:,None]*we[None,:]*R*preg)+1j/(2*math.pi)
    )
    d_dot_n=D@source_normal
    geometry=d_dot_n/dnorm
    h1=scaled_h2(1,k*distance)
    ku=(1j*k/2)*geometry[None,:]*h1
    ks=-d_dot_n[None,:]/(math.pi*R*(dnorm[None,:]**2))
    ks_radial=-d_dot_n/(math.pi*dnorm**2)
    uint=float(np.linalg.norm(a))*float(np.linalg.norm(b))*(
      np.sum(wr[:,None]*we[None,:]*R*(ku-ks))
      +np.sum(we*ks_radial)
    )
    return (omega*mu/2)*pint,uint

def g2_duffy_pair(a0,a1,b0,b1,source_normal,omega,k,mu,qorder,shared):
    ao=a1 if np.linalg.norm(a0-shared)<=1e-18 else a0
    bo=b1 if np.linalg.norm(b0-shared)<=1e-18 else b0
    a=ao-shared; b=bo-shared
    p0,u0=g2_duffy_map(a,b,source_normal,omega,k,mu,qorder,0)
    p1,u1=g2_duffy_map(a,b,source_normal,omega,k,mu,qorder,1)
    return p0+p1,u0+u1

def g2_tensor_pair(a0,a1,b0,b1,source_normal,omega,k,mu,qorder):
    x,w=leggauss(qorder)
    ua=(x+1)/2; ub=(x+1)/2
    wa=w/2; wb=w/2
    da=a1-a0; db=b1-b0
    la=float(np.linalg.norm(da)); lb=float(np.linalg.norm(db))
    ra=a0[None,:]+ua[:,None]*da[None,:]
    rb=b0[None,:]+ub[:,None]*db[None,:]
    d=rb[None,:,:]-ra[:,None,:]
    distance=np.linalg.norm(d,axis=2)
    if np.any(distance<=0):
        raise RuntimeError('G2_NONTOUCHING_ZERO_DISTANCE')
    weights=wa[:,None]*wb[None,:]
    P=(omega*mu/2)*la*lb*np.sum(weights*scaled_h2(0,k*distance))
    geometry=np.einsum('abk,k->ab',d,source_normal)/distance
    U=(1j*k/2)*la*lb*np.sum(weights*geometry*scaled_h2(1,k*distance))
    return P,U

def g2_pair_integrals(a0,a1,b0,b1,source_normal,omega,k,mu,qorder,stats=None,depth=0):
    if stats is not None:
        stats['max_recursion_depth']=max(stats['max_recursion_depth'],depth)
    shared=g2_shared_endpoint(a0,a1,b0,b1)
    if shared is not None:
        if stats is not None and depth==0: stats['touching_directed_pairs']+=1
        return g2_duffy_pair(a0,a1,b0,b1,source_normal,omega,k,mu,qorder,shared)
    if g2_proper_intersection(a0,a1,b0,b1):
        raise RuntimeError('G2_NONSHARED_SEGMENT_INTERSECTION')
    la=float(np.linalg.norm(a1-a0)); lb=float(np.linalg.norm(b1-b0))
    distance=g2_segment_distance(a0,a1,b0,b1)
    if distance/max(la,lb)<G2_NEAR_RATIO:
        if stats is not None and depth==0: stats['routed_near_directed_pairs']+=1
        if depth>=G2_NEAR_MAX_DEPTH:
            raise RuntimeError(('G2_NEAR_DEPTH',depth,distance,la,lb))
        if la>=lb:
            am=(a0+a1)/2
            p0,u0=g2_pair_integrals(a0,am,b0,b1,source_normal,omega,k,mu,qorder,stats,depth+1)
            p1,u1=g2_pair_integrals(am,a1,b0,b1,source_normal,omega,k,mu,qorder,stats,depth+1)
        else:
            bm=(b0+b1)/2
            p0,u0=g2_pair_integrals(a0,a1,b0,bm,source_normal,omega,k,mu,qorder,stats,depth+1)
            p1,u1=g2_pair_integrals(a0,a1,bm,b1,source_normal,omega,k,mu,qorder,stats,depth+1)
        return p0+p1,u0+u1
    if stats is not None and depth==0: stats['tensor_directed_pairs']+=1
    return g2_tensor_pair(a0,a1,b0,b1,source_normal,omega,k,mu,qorder)

def assemble_pu_galerkin(mid,tangent,normal,length,omega,k,mu,qorder):
    count=len(length)
    p0,p1=g2_endpoints(mid,tangent,length)
    P=np.empty((count,count),complex)
    U=np.empty((count,count),complex)
    self_anchor=[]
    stats={
      'self_panels':count,'touching_directed_pairs':0,
      'routed_near_directed_pairs':0,'tensor_directed_pairs':0,
      'max_recursion_depth':0,
    }
    for m in range(count):
        for n in range(count):
            if m==n:
                P[m,n],U[m,n],small_error=g2_self_integrals(length[m],omega,k,mu,qorder)
                if small_error is not None:
                    self_anchor.append(small_error)
            else:
                P[m,n],U[m,n]=g2_pair_integrals(
                  p0[m],p1[m],p0[n],p1[n],normal[n],omega,k,mu,qorder,stats
                )
    psym=float(np.linalg.norm(P-P.T)/max(np.linalg.norm(P),1e-300))
    return P,U,{
      'P_raw_transpose':psym,
      'self_small_argument_max':max(self_anchor) if self_anchor else None,
      'pair_classes':stats,
    }

def g2_solve_cert(A,B):
    A=np.asarray(A,complex); B=np.asarray(B,complex)
    vector=B.ndim==1
    if vector: B=B[:,None]
    row_max=np.max(np.abs(A),axis=1)
    if np.any(~np.isfinite(row_max)) or np.any(row_max<=0):
        raise RuntimeError('G2_ZERO_OR_NONFINITE_ROW_SCALE')
    row=1/row_max
    scaled=row[:,None]*A
    column_max=np.max(np.abs(scaled),axis=0)
    if np.any(~np.isfinite(column_max)) or np.any(column_max<=0):
        raise RuntimeError('G2_ZERO_OR_NONFINITE_COLUMN_SCALE')
    column=1/column_max
    equilibrated=scaled*column[None,:]
    lu,piv=lu_factor(equilibrated,check_finite=False)
    y=lu_solve((lu,piv),row[:,None]*B,check_finite=False)
    x=column[:,None]*y
    residual=A@x-B
    denominator=np.linalg.norm(A,np.inf)*np.linalg.norm(x,np.inf)+np.linalg.norm(B,np.inf)
    backward=float(np.linalg.norm(residual,np.inf)/denominator)
    gecon=get_lapack_funcs('gecon',(equilibrated,))
    rcond,info=gecon(lu,np.linalg.norm(equilibrated,1),norm='1')
    if info or not np.isfinite(rcond) or rcond<=0:
        raise RuntimeError(('G2_GECON',info,rcond))
    scaling_bytes=(
      np.ascontiguousarray(row,dtype='<f8').tobytes()
      +np.ascontiguousarray(column,dtype='<f8').tobytes()
    )
    certificate={
      'backward':backward,'kappa_u':float(UROUND/rcond),
      'row_scale':row.tolist(),'column_scale':column.tolist(),
      'scaling_sha256':hashlib.sha256(scaling_bytes).hexdigest(),
    }
    return (x[:,0] if vector else x),certificate

def g2_interior_gate(certificate):
    return bool(
      certificate['P_raw_transpose']<=1e-12
      and certificate['Pout_raw_transpose']<=1e-12
      and certificate['P_solve']['backward']<=1e-10
      and certificate['Pout_solve']['backward']<=1e-10
      and certificate['P_solve']['kappa_u']<=1e-8
      and certificate['Pout_solve']['kappa_u']<=1e-8
      and certificate['Yw_reciprocity']<=1e-8
      and certificate['Yw_min_hermitian_eigenvalue_S_m']
          >=-certificate['Yw_passivity_tolerance_S_m']
      and certificate['cancellation_condition']<=1e-8
    )

def g2_weak_interior(mid,tangent,normal,length,omega,kp,kb,qorder):
    pp,up,cp=assemble_pu_galerkin(mid,tangent,normal,length,omega,kp,MU0,qorder)
    pb,ub,cb=assemble_pu_galerkin(mid,tangent,normal,length,omega,kb,MU0,qorder)
    dp,spp=g2_solve_cert(pp,up)
    db,spb=g2_solve_cert(pb,ub)
    Dwp=length[:,None]*dp
    Dwb=length[:,None]*db
    Yw=Dwp-Dwb
    floor=max(1e-12,1e-10*float(np.max(np.abs(Yw))))
    reciprocity=float(np.linalg.norm(Yw-Yw.T)/max(np.linalg.norm(Yw),len(length)*floor))
    hermitian=(Yw+Yw.conj().T)/2
    mineig=float(np.linalg.eigvalsh(hermitian)[0])
    ptol=max(floor,1e-9*float(np.linalg.norm(Yw,2)))
    cancel=(np.linalg.norm(Dwp)+np.linalg.norm(Dwb))/max(np.linalg.norm(Yw),len(length)*floor)
    cancel_condition=float(cancel*max(spp['kappa_u'],spb['kappa_u']))
    self_anchors=[
      x for x in (cp['self_small_argument_max'],cb['self_small_argument_max'])
      if x is not None
    ]
    certificate={
      'P_raw_transpose':cp['P_raw_transpose'],
      'Pout_raw_transpose':cb['P_raw_transpose'],
      'P_solve':spp,'Pout_solve':spb,
      'P_pair_classes':cp['pair_classes'],
      'Pout_pair_classes':cb['pair_classes'],
      'Yw_reciprocity':reciprocity,
      'Yw_floor_S_m':floor,
      'Yw_min_hermitian_eigenvalue_S_m':mineig,
      'Yw_passivity_tolerance_S_m':ptol,
      'cancellation_amplification':float(cancel),
      'cancellation_condition':cancel_condition,
      'self_small_argument_max':max(self_anchors,default=None),
    }
    certificate['mandatory_gate_pass']=g2_interior_gate(certificate)
    return Yw,certificate

def g2_circle_arrays(radius,count):
    theta=2*math.pi*np.arange(count+1)/count
    endpoints=radius*np.column_stack((np.cos(theta),np.sin(theta)))
    p0=endpoints[:-1]; p1=endpoints[1:]
    delta=p1-p0
    length=np.linalg.norm(delta,axis=1)
    tangent=delta/length[:,None]
    normal=np.column_stack((tangent[:,1],-tangent[:,0]))
    mid=(p0+p1)/2
    angle=(theta[:-1]+theta[1:])/2
    return mid,tangent,normal,length,angle

def g2_jratio(mode,z):
    if mode==0:
        return -jve(1,z)/jve(0,z)
    return mode/z-jve(mode+1,z)/jve(mode,z)

def run_g2_circle(radius,count,frequency,qorder):
    start=time.perf_counter()
    mid,tangent,normal,length,angle=g2_circle_arrays(radius,count)
    omega=2*math.pi*frequency
    kp=np.sqrt(omega*MU0*(omega*EPS0-1j*SIGMA))
    kb=omega*math.sqrt(MU0*EPS0)
    Yw,cert=g2_weak_interior(mid,tangent,normal,length,omega,kp,kb,qorder)
    exact_values=[
      kp/(1j*omega*MU0)*g2_jratio(mode,kp*radius)
      -kb/(1j*omega*MU0)*g2_jratio(mode,kb*radius)
      for mode in range(5)
    ]
    ys_floor=max(1e-12,1e-10*max(abs(x) for x in exact_values))
    modes=[]
    for mode,exact in enumerate(exact_values):
        e=np.exp(1j*mode*angle)
        em=np.exp(-1j*mode*angle)
        numeric=np.vdot(e,Yw@e)/np.vdot(e,length*e)
        numeric_minus=np.vdot(em,Yw@em)/np.vdot(em,length*em)
        phase_eligible=bool(abs(numeric)>=10*ys_floor and abs(exact)>=10*ys_floor)
        phase_error=(
          float(abs(np.angle(numeric/exact,deg=True)))
          if phase_eligible else None
        )
        modes.append({
          'mode':mode,'exact_S':[exact.real,exact.imag],
          'numeric_S':[numeric.real,numeric.imag],
          'numeric_minus_S':[numeric_minus.real,numeric_minus.imag],
          'relative_error':float(abs(numeric-exact)/max(abs(exact),ys_floor)),
          'phase_eligible':phase_eligible,'phase_error_deg':phase_error,
          'plus_minus_discrepancy':float(
            abs(numeric-numeric_minus)/max(abs(numeric),abs(numeric_minus),ys_floor)
          ),
        })
    analytic_gate=bool(
      max(x['relative_error'] for x in modes)<=5e-3
      and max((x['phase_error_deg'] for x in modes if x['phase_eligible']),default=0.0)<=0.25
    )
    return {
      'case':'M1-EQ0-G2-circle','radius_m':radius,'N':count,
      'frequency_hz':frequency,'q':qorder,'Ys_floor_S':ys_floor,
      'modes':modes,'interior':cert,
      'analytic_gate_pass_at_this_N':analytic_gate,
      'mandatory_operator_gate_pass':cert['mandatory_gate_pass'],
      'seconds':time.perf_counter()-start,'memory_mib':memory_mib(),
    }

def run_g2_eq0(level,frequency,qorder,r0s):
    start=time.perf_counter()
    _,mid,tangent,normal,length,conductor,_,_=g1_geometry(level)
    omega=2*math.pi*frequency
    kp=np.sqrt(omega*MU0*(omega*EPS0-1j*SIGMA))
    kb=omega*math.sqrt(MU0*EPS0)
    blocks=[]; certificates=[]
    for c in (0,1):
        ix=np.flatnonzero(conductor==c)
        Yw,cert=g2_weak_interior(mid[ix],tangent[ix],normal[ix],length[ix],omega,kp,kb,qorder)
        blocks.append(Yw); certificates.append(cert)
    Yw=block_diag(*blocks)
    Q=np.column_stack((conductor==0,conductor==1)).astype(float)
    b=np.asarray((1.,-1.))
    WQ=length[:,None]*Q
    results={}
    for r0 in r0s:
        GG,_=assemble_gg(level,20,r0)
        AE=np.diag(length)-1j*omega*MU0*(GG@(Yw/length[:,None]))
        Eresponse,AEcert=g2_solve_cert(AE,WQ)
        jresponse=Yw@Eresponse
        K=Q.T@jresponse
        Z,Kcert=g2_solve_cert(K,np.eye(2))
        V=Z@b
        E=Eresponse@V
        j=jresponse@V
        integrated=Q.T@j
        terminal=.5*np.vdot(b,V)
        boundary=.5*np.vdot(E,j)
        loop=complex(b@Z@b)
        terminal_reciprocity=float(np.linalg.norm(Z-Z.T)/max(np.linalg.norm(Z),1e-300))
        current_residual=float(np.linalg.norm(integrated-b)/np.linalg.norm(b))
        zero_sum_residual=float(abs(np.sum(integrated))/np.linalg.norm(b))
        power_residual=float(
          abs(terminal.real-boundary.real)
          /max(abs(terminal.real),abs(boundary.real),1e-18)
        )
        terminal_passivity_tolerance=max(1e-12,1e-9*abs(loop))
        terminal_gate=bool(
          terminal_reciprocity<=1e-8
          and current_residual<=1e-10
          and zero_sum_residual<=1e-10
          and power_residual<=1e-8
          and AEcert['backward']<=1e-10 and AEcert['kappa_u']<=1e-8
          and Kcert['backward']<=1e-10 and Kcert['kappa_u']<=1e-8
          and loop.real>=-terminal_passivity_tolerance
        )
        results[str(r0)]={
          'Zloop_ohm_per_m':[loop.real,loop.imag],
          'terminal_reciprocity':terminal_reciprocity,
          'current_residual':current_residual,
          'zero_sum_residual':zero_sum_residual,
          'dissipative_power_residual':power_residual,
          'terminal_power':[terminal.real,terminal.imag],
          'boundary_power':[boundary.real,boundary.imag],
          'AE_solve':AEcert,'K_solve':Kcert,
          'passivity_margin':loop.real,
          'passivity_tolerance':terminal_passivity_tolerance,
          'mandatory_terminal_gate_pass':terminal_gate,
        }
    mandatory=bool(
      all(x['mandatory_gate_pass'] for x in certificates)
      and all(x['mandatory_terminal_gate_pass'] for x in results.values())
    )
    return {
      'case':'M1-EQ0-G2','level':level,'N':len(length),
      'frequency_hz':frequency,'q':qorder,'interior':certificates,'r0':results,
      'mandatory_raw_gate_pass':mandatory,
      'seconds':time.perf_counter()-start,'memory_mib':memory_mib(),
    }

def g2_process_guard():
    usage=memory_mib()
    if usage:
        if usage['working_set']>4096 or usage['peak_working_set']>4096 or usage['private']>5120:
            raise RuntimeError(('G2_PROCESS_MEMORY_STOP',usage))
    return usage

def g2_pair_category(a0,a1,b0,b1,same):
    if same: return 'self'
    if g2_shared_endpoint(a0,a1,b0,b1) is not None: return 'touching'
    if g2_proper_intersection(a0,a1,b0,b1):
        raise RuntimeError('G2_NONSHARED_SEGMENT_INTERSECTION')
    la=float(np.linalg.norm(a1-a0)); lb=float(np.linalg.norm(b1-b0))
    return (
      'routed_near'
      if g2_segment_distance(a0,a1,b0,b1)/max(la,lb)<G2_NEAR_RATIO
      else 'tensor'
    )

def run_g2_pair_screen(frequency,qlo,qhi):
    start=time.perf_counter()
    _,mid,tangent,normal,length,conductor,_,_=g1_geometry(0)
    ix=np.flatnonzero(conductor==0)
    mid=mid[ix]; tangent=tangent[ix]; normal=normal[ix]; length=length[ix]
    p0,p1=g2_endpoints(mid,tangent,length)
    categories=np.empty((len(length),len(length)),object)
    for m in range(len(length)):
        for n in range(len(length)):
            categories[m,n]=g2_pair_category(p0[m],p1[m],p0[n],p1[n],m==n)
    omega=2*math.pi*frequency
    kp=np.sqrt(omega*MU0*(omega*EPS0-1j*SIGMA))
    kb=omega*math.sqrt(MU0*EPS0)
    materials={}
    passed=True
    for name,k in (('conductor',kp),('background',kb)):
        plo,ulo,clo=assemble_pu_galerkin(mid,tangent,normal,length,omega,k,MU0,qlo)
        phi,uhi,chi=assemble_pu_galerkin(mid,tangent,normal,length,omega,k,MU0,qhi)
        pscale=np.maximum.reduce((
          np.abs(plo),np.abs(phi),omega*MU0*np.outer(length,length)/(2*math.pi)
        ))
        uscale=np.maximum.reduce((
          np.abs(ulo),np.abs(uhi),np.sqrt(np.outer(length,length))
        ))
        class_metrics={}
        for category in ('self','touching','routed_near','tensor'):
            mask=categories==category
            pair_count=int(np.count_nonzero(mask))
            class_metrics[category]={
              'directed_pair_count':pair_count,
              'P_q_relative_max':(
                float(np.max(np.abs(phi-plo)[mask]/pscale[mask])) if pair_count else None
              ),
              'U_q_relative_max':(
                float(np.max(np.abs(uhi-ulo)[mask]/uscale[mask])) if pair_count else None
              ),
            }
        covered=all(class_metrics[x]['directed_pair_count']>0 for x in ('self','touching','routed_near'))
        pchanges=[x['P_q_relative_max'] for x in class_metrics.values() if x['P_q_relative_max'] is not None]
        uchanges=[x['U_q_relative_max'] for x in class_metrics.values() if x['U_q_relative_max'] is not None]
        material_pass=bool(
          covered and max(pchanges)<=1e-3 and max(uchanges)<=1e-3
          and clo['P_raw_transpose']<=1e-12
          and chi['P_raw_transpose']<=1e-12
          and chi['pair_classes']['max_recursion_depth']<=G2_NEAR_MAX_DEPTH
        )
        passed=passed and material_pass
        materials[name]={
          'qlo':qlo,'qhi':qhi,'pair_class_metrics':class_metrics,
          'qlo_assembly':clo,'qhi_assembly':chi,
          'mandatory_pair_gate_pass':material_pass,
        }
    return {
      'case':'M1-EQ0-G2-pair-screen','frequency_hz':frequency,
      'N_one_conductor':len(length),'materials':materials,
      'mandatory_stage_pass':bool(passed),
      'seconds':time.perf_counter()-start,'memory_mib':g2_process_guard(),
    }

def g2_complex_value(value):
    return complex(value[0],value[1])

def g2_circle_q_summary(lo,hi):
    floor=max(lo['Ys_floor_S'],hi['Ys_floor_S'])
    relative=[]; phases=[]
    for a,b in zip(lo['modes'],hi['modes']):
        za=complex(*a['numeric_S']); zb=complex(*b['numeric_S'])
        relative.append(float(abs(zb-za)/max(abs(za),abs(zb),floor)))
        if abs(za)>=10*floor and abs(zb)>=10*floor:
            phases.append(float(abs(np.angle(zb/za,deg=True))))
    return {
      'relative_max':max(relative),'phase_max_deg':max(phases,default=0.0),
      'mandatory_gate_pass':bool(max(relative)<=1e-3 and max(phases,default=0.0)<=0.25),
    }

def run_g2_circle_stage(radius,counts,frequency,qlo,qhi):
    rows=[]
    for count in counts:
        lo=run_g2_circle(radius,count,frequency,qlo)
        hi=run_g2_circle(radius,count,frequency,qhi)
        rows.append({'N':count,'qlo':lo,'qhi':hi,'quadrature':g2_circle_q_summary(lo,hi)})
    coarse=rows[-2]['qlo']; fine=rows[-1]['qlo']
    floor=max(coarse['Ys_floor_S'],fine['Ys_floor_S'])
    changes=[]; phases=[]
    for a,b in zip(coarse['modes'],fine['modes']):
        za=complex(*a['numeric_S']); zb=complex(*b['numeric_S'])
        changes.append(float(abs(zb-za)/max(abs(za),abs(zb),floor)))
        if abs(za)>=10*floor and abs(zb)>=10*floor:
            phases.append(float(abs(np.angle(zb/za,deg=True))))
    mesh={
      'coarse_N':counts[-2],'fine_N':counts[-1],
      'relative_rms':float(np.sqrt(np.mean(np.asarray(changes)**2))),
      'relative_max':max(changes),'phase_max_deg':max(phases,default=0.0),
    }
    mesh['mandatory_gate_pass']=bool(
      mesh['relative_rms']<=5e-3 and mesh['relative_max']<=1e-2
      and mesh['phase_max_deg']<=0.25
    )
    mandatory=bool(
      all(x['quadrature']['mandatory_gate_pass'] for x in rows)
      and all(x['qlo']['mandatory_operator_gate_pass'] for x in rows)
      and all(x['qhi']['mandatory_operator_gate_pass'] for x in rows)
      and fine['analytic_gate_pass_at_this_N']
      and mesh['mandatory_gate_pass']
    )
    return {
      'case':'M1-EQ0-G2-circle-stage','frequency_hz':frequency,
      'counts':counts,'qlo':qlo,'qhi':qhi,'rows':rows,'mesh':mesh,
      'mandatory_stage_pass':mandatory,'memory_mib':g2_process_guard(),
    }

def run_g2_eq0_stage(level,frequency,qlo,qhi,r0s):
    lo=run_g2_eq0(level,frequency,qlo,r0s)
    hi=run_g2_eq0(level,frequency,qhi,r0s)
    comparisons={}
    qpass=True
    for r0 in r0s:
        key=str(r0)
        za=g2_complex_value(lo['r0'][key]['Zloop_ohm_per_m'])
        zb=g2_complex_value(hi['r0'][key]['Zloop_ohm_per_m'])
        floor=max(1e-9,1e-9*abs(zb))
        relative=float(abs(zb-za)/max(abs(za),abs(zb),floor))
        phase=(
          float(abs(np.angle(zb/za,deg=True)))
          if abs(za)>=10*floor and abs(zb)>=10*floor else 0.0
        )
        gate=bool(relative<=1e-3 and phase<=0.25)
        qpass=qpass and gate
        comparisons[key]={
          'relative_max':relative,'phase_max_deg':phase,
          'mandatory_gate_pass':gate,
        }
    base_key=min((str(x) for x in r0s),key=lambda x:abs(float(x)-1.0))
    base=g2_complex_value(lo['r0'][base_key]['Zloop_ohm_per_m'])
    r0_relative={}
    for r0 in r0s:
        key=str(r0); z=g2_complex_value(lo['r0'][key]['Zloop_ohm_per_m'])
        r0_relative[key]=float(abs(z-base)/max(abs(base),1e-9))
    condition_values=[]
    for certificate in lo['interior']:
        condition_values += [
          certificate['P_solve']['kappa_u'],certificate['Pout_solve']['kappa_u']
        ]
    for result in lo['r0'].values():
        condition_values += [result['AE_solve']['kappa_u'],result['K_solve']['kappa_u']]
    tau_invariance=max(1e-12,50*max(condition_values))
    r0_pass=bool(
      tau_invariance<=1e-8
      and max(r0_relative.values(),default=0.0)<=tau_invariance
    )
    mandatory=bool(
      lo['mandatory_raw_gate_pass'] and hi['mandatory_raw_gate_pass']
      and qpass and r0_pass
    )
    return {
      'case':'M1-EQ0-G2-EQ0-stage','level':level,'frequency_hz':frequency,
      'qlo_result':lo,'qhi_result':hi,'quadrature':comparisons,
      'r0_balanced_loop_relative':r0_relative,
      'r0_tau_invariance':tau_invariance,'r0_gate_pass':r0_pass,
      'mandatory_stage_pass':mandatory,'memory_mib':g2_process_guard(),
    }

def g2_parse_list(name,default,cast):
    values=[cast(x) for x in os.environ.get(name,default).split(',') if x]
    if (
      not values or any(not np.isfinite(float(x)) for x in values)
      or len(values)!=len(set(values))
    ):
        raise RuntimeError(('G2_INVALID_ENV',name,values))
    return values

g2_stage=os.environ.get('M1_G2_STAGE','pair')
g2_freqs=g2_parse_list('M1_G2_FREQS','1e5,2e9',float)
g2_qs=g2_parse_list('M1_G2_QS','20,40',int)
g2_circle_ns=g2_parse_list('M1_G2_CIRCLE_NS','128,256',int)
g2_levels=g2_parse_list('M1_G2_LEVELS','0',int)
g2_r0s=g2_parse_list('M1_G2_R0S','1',float)
mandatory_frequencies={1e5,1e6,1e7,1e8,5e8,1e9,2e9}
if any(x not in mandatory_frequencies for x in g2_freqs):
    raise RuntimeError(('G2_FREQUENCY_NOT_FROZEN',g2_freqs))
if g2_qs!=[20,40]: raise RuntimeError(('G2_Q_NOT_FROZEN',g2_qs))
if any(x not in (128,256,512) for x in g2_circle_ns):
    raise RuntimeError(('G2_CIRCLE_N_NOT_FROZEN',g2_circle_ns))
if any(x not in (0,1,2) for x in g2_levels):
    raise RuntimeError(('G2_LEVEL_NOT_FROZEN',g2_levels))
if any(x not in (0.1,1.0,10.0) for x in g2_r0s):
    raise RuntimeError(('G2_R0_NOT_FROZEN',g2_r0s))
extreme_frequencies=[1e5,2e9]
full_frequencies=[1e5,1e6,1e7,1e8,5e8,1e9,2e9]
if g2_stage in ('pair','circle') and g2_freqs!=extreme_frequencies:
    raise RuntimeError(('G2_EXTREME_SEQUENCE_NOT_FROZEN',g2_stage,g2_freqs))
if g2_stage=='eq0':
    if g2_freqs not in (extreme_frequencies,full_frequencies):
        raise RuntimeError(('G2_EQ0_FREQUENCY_SEQUENCE_NOT_FROZEN',g2_freqs))
    if tuple(g2_levels) not in ((0,),(1,2)):
        raise RuntimeError(('G2_EQ0_LEVEL_SEQUENCE_NOT_FROZEN',g2_levels))
    if tuple(g2_r0s) not in ((1.0,),(0.1,1.0,10.0)):
        raise RuntimeError(('G2_EQ0_R0_SEQUENCE_NOT_FROZEN',g2_r0s))
g2_process_guard()
for frequency in g2_freqs:
    if g2_stage=='pair':
        result=run_g2_pair_screen(frequency,*g2_qs)
        print(json.dumps(result,separators=(',',':'),allow_nan=False),flush=True)
        if not result['mandatory_stage_pass']: raise SystemExit(2)
    elif g2_stage=='circle':
        if tuple(g2_circle_ns) not in ((128,256),(256,512)):
            raise RuntimeError(('G2_CIRCLE_SEQUENCE_NOT_FROZEN',g2_circle_ns))
        result=run_g2_circle_stage(17.5e-6,g2_circle_ns,frequency,*g2_qs)
        print(json.dumps(result,separators=(',',':'),allow_nan=False),flush=True)
        if not result['mandatory_stage_pass']: raise SystemExit(2)
    elif g2_stage=='eq0':
        for level in g2_levels:
            result=run_g2_eq0_stage(level,frequency,g2_qs[0],g2_qs[1],g2_r0s)
            print(json.dumps(result,separators=(',',':'),allow_nan=False),flush=True)
            if not result['mandatory_stage_pass']: raise SystemExit(2)
    else:
        raise RuntimeError(('G2_STAGE_NOT_FROZEN',g2_stage))
'@

# These session-local tokens make the documented stage order executable rather
# than advisory.  Every new reconstruction starts with every promotion closed.
$g2PairStagePassed=$false
$g2PairStageReviewed=$false
$g2CircleMediumStagePassed=$false
$g2CircleMediumStageReviewed=$false
$g2CircleFineStagePassed=$false
$g2CircleFineStageReviewed=$false

# Stage 1 only. Inspect both JSON rows and the process-tree guard before stage 2.
$env:M1_G2_STAGE='pair'
$env:M1_G2_FREQS='1e5,2e9'
$env:M1_G2_QS='20,40'
$env:M1_G2_CIRCLE_NS='128,256'
$env:M1_G2_LEVELS='0'
$env:M1_G2_R0S='1'
$g2 | python -u -
if ($LASTEXITCODE -ne 0) { throw "G2_PAIR_STAGE_FAILED_$LASTEXITCODE" }
$g2PairStagePassed=$true
```

pair JSON과 외부 process-tree resource를 검토해 `mandatory_stage_pass=true`를 확인한 같은 PowerShell session에서만 아래 review token을 열고 Stage 2를 별도로 실행한다. 이 두 줄은 JSON과 외부 resource guard를 사람이 검토하기 전에 자동 실행하지 않는다.

```powershell
if ($g2PairStagePassed -ne $true) { throw 'G2_PAIR_STAGE_NOT_PASSED' }
$g2PairStageReviewed=$true
```

```powershell
# Stage 2 is authorized only if stage 1 passed and was reviewed.
if ([string]::IsNullOrWhiteSpace([string]$g2)) { throw 'G2_DEFINITION_NOT_RECONSTRUCTED' }
if ($g2PairStagePassed -ne $true -or $g2PairStageReviewed -ne $true) {
    throw 'G2_PAIR_STAGE_NOT_PASSED_AND_REVIEWED_IN_THIS_SESSION'
}
$env:M1_G2_STAGE='circle'
$env:M1_G2_CIRCLE_NS='128,256'
$g2 | python -u -
if ($LASTEXITCODE -ne 0) { throw "G2_CIRCLE_MEDIUM_STAGE_FAILED_$LASTEXITCODE" }
$g2CircleMediumStagePassed=$true
```

`N=128→256`의 analytic, mesh, q20/q40와 raw operator gate 및 외부 resource guard를 검토한 뒤에만 같은 session에서 review token을 열고 Stage 2b를 별도로 실행한다.

```powershell
if ($g2CircleMediumStagePassed -ne $true) { throw 'G2_CIRCLE_MEDIUM_STAGE_NOT_PASSED' }
$g2CircleMediumStageReviewed=$true
```

```powershell
# Stage 2b is authorized only if the N=128→256 stage passed and was reviewed.
if ([string]::IsNullOrWhiteSpace([string]$g2)) { throw 'G2_DEFINITION_NOT_RECONSTRUCTED' }
if ($g2CircleMediumStagePassed -ne $true -or $g2CircleMediumStageReviewed -ne $true) {
    throw 'G2_CIRCLE_MEDIUM_STAGE_NOT_PASSED_AND_REVIEWED_IN_THIS_SESSION'
}
$env:M1_G2_CIRCLE_NS='256,512'
$g2 | python -u -
if ($LASTEXITCODE -ne 0) { throw "G2_CIRCLE_FINE_STAGE_FAILED_$LASTEXITCODE" }
$g2CircleFineStagePassed=$true
```

두 circle stage를 모두 검토·통과하고 fine stage의 외부 resource guard까지 확인한 뒤에만 같은 session에서 review token을 열고 Stage 3을 별도로 실행한다.

```powershell
if ($g2CircleFineStagePassed -ne $true) { throw 'G2_CIRCLE_FINE_STAGE_NOT_PASSED' }
$g2CircleFineStageReviewed=$true
```

```powershell
# Stage 3 is authorized only after both circle stages passed and were reviewed.
if ([string]::IsNullOrWhiteSpace([string]$g2)) { throw 'G2_DEFINITION_NOT_RECONSTRUCTED' }
if ($g2CircleFineStagePassed -ne $true -or $g2CircleFineStageReviewed -ne $true) {
    throw 'G2_CIRCLE_FINE_STAGE_NOT_PASSED_AND_REVIEWED_IN_THIS_SESSION'
}
$env:M1_G2_STAGE='eq0'
$env:M1_G2_CIRCLE_NS='128,256'
$env:M1_G2_LEVELS='0'
$env:M1_G2_R0S='1'
$g2 | python -u -
if ($LASTEXITCODE -ne 0) { throw "G2_EQ0_SEED_STAGE_FAILED_$LASTEXITCODE" }
```

Stage 1 pair screen은 `ddec4fb` 사전등록 뒤 실행돼 `passed_pair_screen_only`로 판정됐다. 이후 pair를 같은 PowerShell session에서 재확인·검토하고 Stage 2 medium circle을 시작했지만 첫 100 kHz mandatory operator gate에서 nonzero exit했다. `N=128` cancellation condition은 `2.91315e-8`, `N=256` raw `Yw` reciprocity/cancellation은 `1.41197e-8/1.63755e-7`로 `1e-8` gate를 넘었다. 따라서 현재 전체 상태는 `BLOCKED_INTERIOR_WEIGHTED_RECIPROCITY_PASSIVITY__G2_PAIR_PASSED_CIRCLE_100KHZ_RECIPROCITY_CANCELLATION_FAIL`이다.

위 Stage 2b와 Stage 3 명령은 preregistered historical continuation으로만 보존한다. Stage 2가 실패했으므로 review token을 열 수 없고 같은 runner guard가 G2 `N=256→512`와 G2 EQ0 seed 실행을 차단한다. planned G2 2 GHz circle row, G2 `N=512`, G2 EQ0 seed와 G2 `N=288/576` full sweep은 미실행이다. gate 완화, 사후 대칭화, clipping 또는 higher precision만으로 pass를 만들지 않는다. 당시 다음 후보로 `preregistered_not_run`이던 two-DtN subtraction 없는 independent A–v volume-FEM boundary Schur는 이후 H1 coarse `h`와 H2 refined `h2` stage-only gates를 통과했다. 그러나 `h4` convergence, fine analytic과 final circle은 여전히 미실행이다. production SAO 후보는 four-operator symmetric Calderón/Steklov–Poincaré 또는 Hamiltonian Schur DtN으로 별도 사전 등록한다.

G1 exterior structural gate는 `smn=max(|GG10,mn|,|GG20,mn|,ℓmℓn/(2π))`의 pair-normalized max와 Frobenius q change를 각각 `<=1e-10`, independently reversed pair와 raw transpose defect를 `<=1e-12`, `r0` rank-one relative residual을 `<=1e-8`로 판정한다. G2는 q20을 canonical, q40을 parity로 두며 self/touching/routed-near와 balanced final `Z'loop`에 기존 `0.1%/0.25°` gate를 적용한다. `r0`는 partial common mode가 아니라 q20 balanced `Z'loop`에서 비교하고, 관련 `P/Pout/AE/K`의 최대 `κ1u`로 `τinv=max(1e-12,50 max κ1u)<=1e-8`을 계산해 변화량 `<=τinv`를 요구한다. prospective interior는 `Yw=WYs` `[S·m]`, `Yw,floor=max(1e-12 S·m,1e-10 max|Yw|)`, `||Yw−Yw^T||F/max(||Yw||F,N Yw,floor)<=1e-8`로 검사한다. passivity는 `H(Yw)=(Yw+Yw^H)/2`의 raw `λmin >= -max(Yw,floor,1e-9||Yw||2)`다. Hermitian part 평가는 진단이지 operator 대칭화가 아니다. G1은 exterior power 원인 격리용 diagnostic이며 이 interior metric이나 converged A–v가 실패하면 M1은 계속 blocked다.

### G2 pair-screen captured result

| frequency | material | self/touching/routed/tensor | max `P` q-change | max `U` q-change | max depth | mandatory |
|---:|---|---|---:|---:|---:|---|
| 100 kHz | conductor | `72/144/196/4772` | `6.365673979e-11` | `8.551538175e-14` | 2 | pass |
| 100 kHz | background | `72/144/196/4772` | `5.318295400e-16` | `3.671831656e-16` | 2 | pass |
| 2 GHz | conductor | `72/144/196/4772` | `7.528215673e-6` | `1.710208992e-9` | 2 | pass |
| 2 GHz | background | `72/144/196/4772` | `7.403393159e-16` | `3.500670156e-16` | 2 | pass |

각 행의 pair 합은 `5184=72²`다. q20/q40 양쪽 raw `P` transpose의 전체 최대는 `1.888760303e-16`. process-only wall/peak-WS/private는 100 kHz `25.4061208 s / 58.60546875 MiB / 1295.23046875 MiB`, 2 GHz `24.9042113 s / 58.765625 MiB / 1295.3125 MiB`다. 외부 process-tree 또는 8 GB proof가 아니다. 이 결과는 pair quadrature/classification만 승인하며 analytic DtN, `Yw`, cancellation, terminal/power나 full G2를 승인하지 않는다.

### G2 circle 100 kHz captured failure

| N | q | analytic max/RMS | phase | `Yw` reciprocity | min Hermitian eig (`S·m`) | cancellation condition | mandatory operator |
|---:|---:|---:|---:|---:|---:|---:|---|
| 128 | 20 | `3.900617373e-3 / 2.160231503e-3` | `0.0170973021°` | `2.40109e-9` | `+7.73614e-6` | `2.91315e-8` | fail |
| 128 | 40 | `3.900617372e-3 / 2.160231503e-3` | `0.0170973021°` | `2.50717e-9` | `+7.73614e-6` | `2.91315e-8` | fail |
| 256 | 20 | `9.85103113e-4 / 5.43947230e-4` | `0.00424126654°` | `1.41197e-8` | `+1.94722e-6` | `1.63755e-7` | fail |
| 256 | 40 | `9.85103113e-4 / 5.43947230e-4` | `0.00424126653°` | `1.41083e-8` | `+1.94722e-6` | `1.63755e-7` | fail |

q20→q40 worst relative/RMS/phase는 `2.81068e-12/1.58074e-12/1.58097e-10°`, `N=128→256` q20 mesh relative/RMS/phase는 `2.90418614e-3/1.61107728e-3/0.0128560°`다. `P/Pout` transpose, backward residual, condition과 raw passivity는 통과했다. analytic, q와 mesh가 통과해도 full-space reciprocity/cancellation failure를 가릴 수 없으므로 `mandatory_stage_pass=false`다. row별 process-only 최대 wall/peak-WS/private는 `210.401 s / 88.969 MiB / 1328.805 MiB`다.

## AV-BS1 circle manifest와 analytic anchors

아래 block은 [`T1_AV_BOUNDARY_SCHUR_SPEC.md`](T1_AV_BOUNDARY_SCHUR_SPEC.md)의 `AV-BS1-CIRCLE manifest=v1`만 재현한다. mesh/hash/analytic anchor를 만들지만 FEM matrix나 physics response를 solve하지 않는다. runtime 또는 hash가 다르면 후속 solve를 시작하지 않는다.

```powershell
@'
import hashlib, json, math, platform, sys
import numpy as np
import scipy
from scipy.special import jve

A=17.5e-6
NTHETA=128
NRING=16                 # 16 non-center rings; r_j=j*A/16, j=1..16
EXPECTED_RUNTIME=('3.12.10','2.4.4','1.18.0','win32','AMD64')
EXPECTED={
  'h': (2049,3968,128,'cf5c7740449d40c74665543680c2c96d848e546a3e52d27b8254ce099f3335d0'),
  'h2':(8065,15872,256,'34eb4f9cadcefd0b20cff3ae6c483dbee4e412ca11d0ff1a8c2c6f49ec7896a9'),
  'h4':(32001,63488,512,'a91b4bf147628a34d1a29144ae353a83110b1756c699c71e2b57e9c36822835b'),
}
EXPECTED_ANCHORS=np.asarray([
  (521.497743503955718,-0.939450347510578),
  (260.749858968259883,-0.156575853973095),
  (173.833308261007582,-0.0521919833172933),
  (130.374992948407203,-0.0234863960617439),
  (104.299997421131625,-0.0125260785577377),
],dtype=np.float64)
ANCHOR_RTOL=16*np.finfo(float).eps  # 32u, u=2^-53

runtime=(platform.python_version(),np.__version__,scipy.__version__,sys.platform,platform.machine())
if runtime!=EXPECTED_RUNTIME:
    raise RuntimeError(('AV_BS_RUNTIME_MISMATCH',runtime,EXPECTED_RUNTIME))

def ccw(nodes,triangle):
    i,j,k=triangle
    x0,y0=nodes[i]; x1,y1=nodes[j]; x2,y2=nodes[k]
    cross=(x1-x0)*(y2-y0)-(y1-y0)*(x2-x0)
    if cross==0.0: raise RuntimeError(('AV_BS_ZERO_AREA',triangle))
    return triangle if cross>0.0 else (i,k,j)

def seed_mesh():
    nodes=[(0.0,0.0)]
    def node_id(ring,ray):
        return 1+(ring-1)*NTHETA+(ray%NTHETA)
    # theta_0=0; node order is ring-major then increasing ray.
    for ring in range(1,NRING+1):
        radius=A*ring/NRING
        for ray in range(NTHETA):
            theta=2.0*math.pi*ray/NTHETA
            nodes.append((radius*math.cos(theta),radius*math.sin(theta)))
    triangles=[]
    for ray in range(NTHETA):
        triangles.append(ccw(nodes,(0,node_id(1,ray),node_id(1,ray+1))))
    # Diagonal direction alternates by radial band only, not by ray.  This
    # preserves the 128-fold rotational symmetry of the circle fixture.
    for ring in range(1,NRING):
        for ray in range(NTHETA):
            a=node_id(ring,ray); b=node_id(ring,ray+1)
            c=node_id(ring+1,ray); d=node_id(ring+1,ray+1)
            raw=((a,c,d),(a,d,b)) if ring%2 else ((a,c,b),(b,c,d))
            triangles.extend(ccw(nodes,t) for t in raw)
    return nodes,triangles

def edge_data(triangles):
    counts={}
    for triangle in triangles:
        for i,j in ((triangle[0],triangle[1]),(triangle[1],triangle[2]),(triangle[2],triangle[0])):
            edge=(i,j) if i<j else (j,i)
            counts[edge]=counts.get(edge,0)+1
    boundary=sorted(edge for edge,count in counts.items() if count==1)
    if any(count not in (1,2) for count in counts.values()):
        raise RuntimeError('AV_BS_NONMANIFOLD_EDGE')
    return counts,boundary

def refine(nodes,triangles):
    counts,boundary=edge_data(triangles)
    boundary=set(boundary)
    out=list(nodes); midpoint={}
    for edge in sorted(counts):
        x=(nodes[edge[0]][0]+nodes[edge[1]][0])/2.0
        y=(nodes[edge[0]][1]+nodes[edge[1]][1])/2.0
        if edge in boundary:
            scale=A/math.hypot(x,y)
            x*=scale; y*=scale
        midpoint[edge]=len(out); out.append((x,y))
    refined=[]
    def mid(i,j): return midpoint[(i,j) if i<j else (j,i)]
    for i,j,k in triangles:
        ij=mid(i,j); jk=mid(j,k); ki=mid(k,i)
        for triangle in ((i,ij,ki),(j,jk,ij),(k,ki,jk),(ij,jk,ki)):
            refined.append(ccw(out,triangle))
    return out,refined

def manifest(level,subdivide,nodes,triangles):
    edges,boundary=edge_data(triangles)
    boundary_nodes={node for edge in boundary for node in edge}
    header=(
      f'AV-BS1-CIRCLE|manifest=v1|generator=radial-p1-v1|level={level}'
      f'|a_m={A.hex()}|n_theta={NTHETA}|n_radial={NRING}'
      f'|subdivide={subdivide}|diag=alternate_by_radial_band'
      '|coordinates=float.hex|triangles=ccw'
    )
    lines=[header]
    for index,(x,y) in enumerate(nodes):
        tag='center' if index==0 else ('boundary' if index in boundary_nodes else 'interior')
        lines.append(f'n|{index}|{x.hex()}|{y.hex()}|{tag}')
    for index,triangle in enumerate(triangles):
        lines.append(f't|{index}|{triangle[0]}|{triangle[1]}|{triangle[2]}')
    for index,edge in enumerate(boundary):
        lines.append(f'b|{index}|{edge[0]}|{edge[1]}')
    payload='\n'.join(lines).encode('utf-8')
    condition=[]; area=[]
    for i,j,k in triangles:
        origin=np.asarray(nodes[i])
        transform=np.column_stack((np.asarray(nodes[j])-origin,np.asarray(nodes[k])-origin))
        area.append(abs(float(np.linalg.det(transform)))/2.0)
        condition.append(float(np.linalg.cond(transform)))
    result={
      'level':level,'nodes':len(nodes),'edges':len(edges),'triangles':len(triangles),
      'boundary_edges':len(boundary),'euler':len(nodes)-len(edges)+len(triangles),
      'min_area_m2':min(area),'max_area_m2':max(area),
      'max_element_kappa2':max(condition),
      'quality_16u_kappa':8*np.finfo(float).eps*max(condition), # 16u, u=2^-53
      'sha256':hashlib.sha256(payload).hexdigest(),
    }
    expected=EXPECTED[level]
    observed=(result['nodes'],result['triangles'],result['boundary_edges'],result['sha256'])
    if observed!=expected or result['euler']!=1 or result['quality_16u_kappa']>2e-10:
        raise RuntimeError(('AV_BS_MANIFEST_MISMATCH',level,observed,expected,result))
    return result

levels=[]
nodes,triangles=seed_mesh(); levels.append(manifest('h',1,nodes,triangles))
nodes,triangles=refine(nodes,triangles); levels.append(manifest('h2',2,nodes,triangles))
nodes,triangles=refine(nodes,triangles); levels.append(manifest('h4',4,nodes,triangles))

mu0=4e-7*math.pi; eps0=8.8541878128e-12
sigma=59.6e6; frequency=1e5; omega=2*math.pi*frequency
kp=np.sqrt(-1j*omega*mu0*sigma)
if kp.real<0.0 or kp.imag>0.0: kp=-kp
z=kp*A
kp_full=np.sqrt(omega*omega*mu0*eps0-1j*omega*mu0*sigma)
if kp_full.real<0.0 or kp_full.imag>0.0: kp_full=-kp_full
kb=omega*math.sqrt(mu0*eps0)
anchors=[]
for mode in range(5):
    ratio=jve(mode+1,z)/jve(mode,z)
    target=-kp/(1j*omega*mu0)*ratio
    expected=complex(*EXPECTED_ANCHORS[mode])
    anchor_relative=abs(target-expected)/abs(expected)
    if not np.isfinite(anchor_relative) or anchor_relative>ANCHOR_RTOL:
        raise RuntimeError(('AV_BS_FROZEN_ANCHOR_MISMATCH',mode,target,expected,anchor_relative,ANCHOR_RTOL))
    # Independent full-wave safe-point diagnostic only.  Canonical AV-BS1 is
    # MQS (kb=0) and never forms these two large DtN values then subtracts.
    zp=kp_full*A; zb=kb*A
    rp=jve(mode+1,zp)/jve(mode,zp); rb=jve(mode+1,zb)/jve(mode,zb)
    dp=kp_full/(1j*omega*mu0)*(mode/zp-rp)
    db=kb/(1j*omega*mu0)*(mode/zb-rb)
    direct=dp-db
    relative=abs(target-direct)/abs(target)
    if not np.isfinite(relative) or relative>1e-12:
        raise RuntimeError(('AV_BS_ANALYTIC_DIAGNOSTIC',mode,relative))
    anchors.append({'m':mode,'Ys_S':[float(target.real),float(target.imag)],
                    'frozen_anchor_relative':float(anchor_relative),
                    'full_wave_Dp_minus_Db_relative_diagnostic':float(relative)})

print(json.dumps({'case':'AV-BS1-CIRCLE-manifest-v1','runtime':runtime,
                  'radius_m':A,'frequency_hz':frequency,
                  'kp_per_m':[float(kp.real),float(kp.imag)],
                  'mesh_levels':levels,'analytic_anchors':anchors},
                 indent=2,allow_nan=False))
'@ | python -
```

이 block은 H1 실행 전의 historical manifest preflight만 재현하며, 현재의 `passed_AV_BS_h_stage_only_pending_h2_review` 상태를 변경하거나 physics solve를 다시 수행하지 않는다. H1 fixture와 실제 artifact의 현재 provenance는 아래 절 및 [`T1_AV_BOUNDARY_SCHUR_RESULTS.md`](T1_AV_BOUNDARY_SCHUR_RESULTS.md)에 별도로 고정한다.

## AV-BS1 standalone primary-h fixture H0 historical freeze

다음은 physics solve를 수행하지 않는 정적 재현 명령이다.

```powershell
python -m pytest -q tests/test_research_av_bs1_boundary_schur.py

$errors=$null; $tokens=$null
[System.Management.Automation.Language.Parser]::ParseFile(
  (Resolve-Path tools/research/run_av_bs1_stage.ps1),
  [ref]$tokens,
  [ref]$errors
) | Out-Null
if ($errors.Count) { $errors | ForEach-Object Message; exit 1 }

powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File tools/research/run_av_bs1_stage.ps1 -Stage manifest

Get-FileHash tools/research/av_bs1_boundary_schur.py -Algorithm SHA256
Get-FileHash tools/research/run_av_bs1_stage.ps1 -Algorithm SHA256
```

H0 고정 결과는 `13 passed`, manifest payload SHA-256 `e79cd30b88fbf339399b3b059ce958138a5b16ed90bc52cde1ed0f87c2dd9a95`, fixture SHA-256 `94cce6454dd632e83db83a621f5192823cd113348970f2e4894c23733de1c066`, runner SHA-256 `31da5df7e17456d3b82754b0c581ec704521f6d21a8875961e4b6d0fde6555f7`다. manifest의 `physics_solve_performed=false`를 확인한다. 이 hash/token은 아래 H0 failure 뒤 폐기됐다.

H0 당시 tracked review token은 [`../../tools/research/av_bs1_primary_h_review_token.json`](../../tools/research/av_bs1_primary_h_review_token.json)의 이전 revision이었다. 당시 token, fixture, runner와 문서가 한 commit에 있고 checkout이 clean인 상태에서 아래 명령을 한 번 실행했다. 현재 파일은 H1 token으로 갱신됐으므로 H0 명령 provenance와 혼동하지 않는다.

```powershell
# H0 commit 4fa5ec8의 clean checkout에서 2026-08-15 한 번 실행했다.
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File tools/research/run_av_bs1_stage.ps1 -Stage primary-h
```

runner는 PowerShell runner와 Python child/descendant를 포함한 execution tree를 100 ms로 감시하고 성공 sample이 없으면 fail-closed한다. final `AV-BS1-h-result-v1` wrapper는 numerical child stdout과 resource report checksum을 결합한다. 성공하더라도 status는 `passed_AV_BS_h_stage_only_pending_h2_review`, `next_stage_authorized=false`, fine analytic/mesh/final circle pass는 `null`이다. 이 명령은 `h2`, `h4`, withheld radius 또는 EQ0를 열지 않는다.

실제 H0 출력은 `BLOCKED_AV_BS_MESH_HASH: h sparse nnz mismatch`였고 factorization 전이었다. ignored artifact와 digest는 [`T1_AV_BOUNDARY_SCHUR_RESULTS.md`](T1_AV_BOUNDARY_SCHUR_RESULTS.md)에 고정했다. 다음 명령은 artifact가 남아 있는 동일 local checkout에서만 digest를 재확인한다.

```powershell
$result = 'validation-output/av-bs1/av-bs1-primary-h-20260814T183617Z.json'
Get-FileHash -LiteralPath $result -Algorithm SHA256
$wrapper = Get-Content -LiteralPath $result -Raw -Encoding utf8 | ConvertFrom-Json
$wrapper.payload.status
$wrapper.payload.numerical.detail
$wrapper.payload.resource.mandatory_resource_gate_pass
```

H1 topology correction의 assembly-only certificate는 다음으로 재현한다. 이 block은 review token의 실행 권한과 무관하게 LU/factorization/physics를 호출하지 않는다.

```powershell
@'
from pathlib import Path
import importlib.util, json
p=Path('tools/research/av_bs1_boundary_schur.py')
s=importlib.util.spec_from_file_location('avbs_h1',p)
m=importlib.util.module_from_spec(s); s.loader.exec_module(m)
nodes,triangles=m.seed_mesh(); manifest=m.mesh_manifest(nodes,triangles)
raw,mass=m._assemble_volume(nodes,triangles)
canonical,certificate=m._canonicalize_cyclic_diagonals(
    raw,nodes,triangles,mesh_condition=manifest['max_element_kappa2'])
_,boundary=m.edge_data(triangles)
_,_,mapping=m._boundary_partition(len(nodes),boundary)
trace=m._assemble_trace_mass(nodes,boundary,mapping)
print(json.dumps({
  'raw_K_nnz':raw.nnz,'canonical_K_nnz':canonical.nnz,
  'M_nnz':mass.nnz,'MGamma_nnz':trace.nnz,
  'canonical_K_sha256':m._sparse_sha256(canonical),
  'M_sha256':m._sparse_sha256(mass),
  'MGamma_sha256':m._sparse_sha256(trace),
  'cyclic_certificate':certificate},indent=2,allow_nan=False))
'@ | python -
```

필수 핵심은 tags `1920`, tag hash `e80c75ed...`, maximum cancellation `2.1676835831040652e-13 < 5.788860430596403e-13`, canonical `K.nnz=10241`, canonical K hash `733c83ae...`, correction relative Frobenius `1.3572884739080543e-16`다. 이 값은 H1 commit `057ed39`과 아래 artifact에서 모두 재현됐다.

H1 static 결과는 `16 passed`, fixture SHA-256 `e2a1c8efff67873b57dc7a658b013e3e76d0c921988011f4c8e70cd25f00f8a7`, runner SHA-256 `dd880de721b9a688ae953c7363dc4a8b482ec1b26f59871d4ca8f83397eb2b7c`다. authorized token은 fixture/runner, H0 artifact, cyclic tag, canonical K certificate와 세 독립 감사 증거를 모두 검증했고, 성공 artifact 독립 review 뒤 consumed tombstone으로 교체됐다.

## AV-BS1 H1 guarded `primary-h` result

commit `057ed39f6a80dfe05aeab06c8bb8f6e6e3429a93`의 clean checkout에서 기존 `primary-h` 명령을 H1으로 한 번 실행했다. ignored artifact는 `validation-output/av-bs1/av-bs1-primary-h-20260814T190732Z.json`, file SHA-256은 `af17bbcc49cebc7e9ddb88e821ec0338a435fb2bf8ce51b117cfb3019b78b44d`다.

다음은 solve를 재실행하지 않고 저장된 outer checksum과 범위만 검증한다.

```powershell
@'
from pathlib import Path
from hashlib import sha256
import json
p=Path(r'validation-output\av-bs1\av-bs1-primary-h-20260814T190732Z.json')
raw=p.read_bytes(); wrapper=json.loads(raw); payload=wrapper['payload']
canonical=json.dumps(payload,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()
assert sha256(raw).hexdigest() == 'af17bbcc49cebc7e9ddb88e821ec0338a435fb2bf8ce51b117cfb3019b78b44d'
assert sha256(canonical).hexdigest() == wrapper['payload_sha256'] == '3cdef96c8de1585acfe4cc256d63e6815b93be9906df51d9b97ff5d4f51f330b'
assert payload['status'] == 'passed_AV_BS_h_stage_only_pending_h2_review'
assert payload['mandatory_stage_pass'] is True
assert payload['next_stage_authorized'] is False
assert payload['fine_analytic_pass'] is None
assert payload['mesh_convergence_pass'] is None
assert payload['final_circle_pass'] is None
print(payload['status'], payload['resource']['wall_seconds'])
'@ | python -
```

raw reciprocity `1.072085475889738e-15`, reverse order `2.2332401790276927e-16`, minimum Hermitian eigenvalue/tolerance `9.256477814190828e-6 / 4.478026532708378e-13 S·m`, max power mismatch `4.7212709501079303e-14`와 max backward residual `4.181585185007905e-17`이 h-stage gate를 통과했다. coarse analytic trend max는 `1.161940848%`이고 fine/convergence 판정은 아직 없다. resource는 wall `1.4714704 s`, 11 samples, peak tree WS `185.2890625 MiB`, private/commit `1.4127578735 GiB`다. 이 결과는 h2, final circle, 8 GB laptop 또는 PowerSI evidence가 아니다.

## AV-BS1 H2-P0 assembly-only manifest

H2-P0는 H1 `h`의 deterministic one-to-four refinement, 3,712개 canonical cyclic tag, raw/canonical `K/M/MΓ`, partition/support hash와 conservative resource arithmetic만 재현한다. 아래 명령은 factorization, physics solve, token, finalizer 또는 result artifact를 만들지 않는다.

```powershell
python -m pytest -q tests/test_research_av_bs1_boundary_schur_h2.py
# 6 passed

$errors=$null; $tokens=$null
[System.Management.Automation.Language.Parser]::ParseFile(
  (Resolve-Path tools/research/run_av_bs1_h2_stage.ps1),
  [ref]$tokens,
  [ref]$errors
) | Out-Null
if ($errors.Count) { $errors | ForEach-Object Message; exit 1 }

powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File tools/research/run_av_bs1_h2_stage.ps1 -Stage manifest

Get-FileHash tools/research/av_bs1_boundary_schur_h2.py -Algorithm SHA256
Get-FileHash tools/research/run_av_bs1_h2_stage.ps1 -Algorithm SHA256
Get-FileHash tests/test_research_av_bs1_boundary_schur_h2.py -Algorithm SHA256
```

frozen output은 다음을 요구한다.

```text
payload_sha256 = 68d2e20a471e0e9475dd8e575ffd1e246f4098bb6c0e2b688d0f6f702fb511ba
fixture_sha256 = 032100623fca51ab22a48493b46f23bc8ce5fd1250203d527062671d47599384
runner_sha256 = 6ce0002e9d3790542008d9e1e608168f1e40f51d56c9a8ff1d37003a15f8feb7
test_sha256 = 039b84ea05ee31c9f9d025d85dd1ec8d4741adc18e6db8883073d93e4d828c69
status = preregistered_H2_P0_assembly_only_no_solve
authorization_state = not_authorized
factorization_performed = false
physics_solve_performed = false
mesh V/E/T/B = 8065/23936/15872/256
candidate/excluded/tag count = 3840/128/3712
raw/canonical K.nnz = 55937/48513
M/MGamma.nnz = 55937/768
maximum cancellation/bound = 3.6286352763558246e-13 / 5.802823100831367e-13
sparse/raw/+25pct resource bytes = 1328172 / 2043070476 / 2553838095
```

parser와 PowerShell `ValidateSet`은 `manifest`만 허용한다. `primary-h2`는 실행 path가 아니라 invalid choice다. 전체 lineage serialization, exact hashes와 non-claims는 [`T1_AV_BOUNDARY_SCHUR_H2_PREREG.md`](T1_AV_BOUNDARY_SCHUR_H2_PREREG.md)에 고정한다.

## AV-BS1 H2-P1 token-gated static candidate — historical preregistration

H2-P1은 P0의 no-solve assembly manifest와 H1 artifact를 결합하는 별도 executable contract다. 이 preregistration snapshot 당시 checkout에는 review token이 없으므로 아래 검증은 manifest/static path만 실행했다. `primary-h2`, factorization, harmonic extension과 physics result는 이 역사적 block에서는 실행하지 않았다. 후속 one-use 실행 결과는 아래 별도 절에 기록한다.

```powershell
python -m pytest -q `
  tests/test_research_av_bs1_boundary_schur_h2.py `
  tests/test_research_av_bs1_boundary_schur_h2_p1.py
# 37 passed; P1 alone 31 passed

python -m py_compile tools/research/av_bs1_boundary_schur_h2_p1.py

$errors=$null; $tokens=$null
[System.Management.Automation.Language.Parser]::ParseFile(
  (Resolve-Path tools/research/run_av_bs1_h2_p1_stage.ps1),
  [ref]$tokens,
  [ref]$errors
) | Out-Null
if ($errors.Count) { $errors | ForEach-Object Message; exit 1 }

python tools/research/av_bs1_boundary_schur_h2_p1.py --stage manifest
Get-FileHash tools/research/av_bs1_boundary_schur_h2_p1.py -Algorithm SHA256
Get-FileHash tools/research/run_av_bs1_h2_p1_stage.ps1 -Algorithm SHA256
Get-FileHash tests/test_research_av_bs1_boundary_schur_h2_p1.py -Algorithm SHA256
Get-FileHash docs/evaluation-research/T1_AV_BOUNDARY_SCHUR_H2_P1_PREREG.md -Algorithm SHA256
```

당시 frozen candidate output은 다음을 요구했다.

```text
manifest_payload_sha256 = 05a21364deeb123432a0f52af9a6dbb818f9c9aa2814c34665dfc8da6b8463c4
fixture_sha256 = 0600604cf7ab7b2a1d2b67c240ed1c659001a989482ce4f94c4b97c305ca3ca4
runner_sha256 = f54d2645bb01dcd287a7836af8a99990055803c793f45ffd9f91e06426624dc6
test_sha256 = 01f0203009087a97f00b58461fb6fda339a2866700399be8d5453bdff47c3199
preregistration_doc_sha256 = adbd6756343eafa426b54a90aa2dfa56c053c56b145331f803a3e7ce4338b868
status = preregistered_H2_P1_token_missing_no_solve
authorization_state = not_authorized
available_solve_stages = []
factorization_performed = false
physics_solve_performed = false
wall_stop_seconds = 900
M9_interior_field_shape = 7809 x 9 complex128
M9_interior_field_raw_bytes = 1124496
```

fixture/finalizer는 frozen H2 `K/M/MΓ`를 assembly-only로 재구성한다. signed M9 interior field마다 `Ap,II u + Ap,IΓ v`의 raw backward residual을 독립 재계산하고 `1e-10` gate를 적용한 뒤, consistent mass volume integral과 boundary power/mismatch를 다시 계산한다. native runner는 process tree 전체를 감시하고, child launch 뒤 pass/failure/resource-stop/finalizer-failure 어느 경우에도 active token을 atomic consumed tombstone으로 교체해야 한다. failure code는 동결한 여덟 `BLOCKED_AV_BS_*` 값만 허용하며 no-child/non-2 resource stop은 `RESULT_SCHEMA`와 `RESOURCE`를 함께 보존한다. 상세 non-claims와 one-use 순서는 [`T1_AV_BOUNDARY_SCHUR_H2_P1_PREREG.md`](T1_AV_BOUNDARY_SCHUR_H2_P1_PREREG.md)에 고정한다.

## AV-BS1 H2 one-use result

위 P1 block은 실행 전 immutable preregistration snapshot이다. clean preregistration commit `defbd6dda2e62637f41c1a8fc8f8f422aaba0e8b`와 token-only child commit `9f3d36b106cbad033ae35a716e21d47b2e178aa1` 뒤 17.5 µm/100 kHz `primary-h2`를 정확히 한 번 실행했다.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
  tools/research/run_av_bs1_h2_p1_stage.ps1 -Stage primary-h2
```

result는 [`../../validation-output/av-bs1/av-bs1-primary-h2-20260814T222208Z.json`](../../validation-output/av-bs1/av-bs1-primary-h2-20260814T222208Z.json)에 보존한다. stdout가 아니라 저장 artifact와 consumed token을 기준으로 한 frozen audit 값은 다음과 같다.

```text
artifact_file_sha256 = b890e4af13d97591b3134788984b3657f6f6b046e3043ce0a6f6792fe1ad7f55
result_payload_sha256 = 5704f3feb5bb90b6e38f8ed3ec67fc690339e9b7f0c1722d40a977295e82593e
numerical_payload_sha256 = bd2f6542a93e3a7adc62f4435540752651ddf1cff84226319b4aa5e8dbdc0be5
resource_report_sha256 = b17468d7383ed5021a783ade4c3b7c1c5e21628580d5f6c298ef1b7b97b26bd2
preflight_payload_sha256 = 6eafea2fe291a7d013c02ee560570fa0afc4acecff78b1145ac89250d9387452
claim_sha256 = f6837c2957704e836fedd5db2e64a0bb4a276c4e5cfc98c4f6a5f0c07101b051
guard_contract_sha256 = 57b70d0f96c558d6650fdf2e9c0a9d29e89d5ff45464ce64ff3a5b648c40af14
status = passed_AV_BS_h2_stage_only_pending_h4_preregistration
mandatory_stage_pass = true
failure_codes = []
factorization_performed = true
physics_solve_performed = true
fine_analytic_pass = null
mesh_convergence_pass = null
final_circle_pass = null
next_stage_authorized = false
```

stage-evaluable maxima are:

```text
background/conductor backward residual = 5.981236336103848e-17 / 5.383112747661412e-17
background/conductor kappa1_u = 1.0244351010046593e-11 / 1.0244323279051323e-11
M9 PDE residual max = 4.421492019510585e-16
raw reciprocity / reverse order = 1.8831838353064343e-15 / 2.465264212368452e-16
minimum Hermitian eigenvalue / tolerance = 2.3635694288740116e-6 / 2.2396879199202253e-13 S*m
power mismatch max = 1.1134002810265329e-13
h-to-h2 trend RMS / max / phase = 0.00462796342945947 / 0.008680146425905828 / 0.00016639608723203282 deg
h2 analytic trend RMS / max / phase = 0.0015525356105053729 / 0.0029139683841210083 / 0.00005682250879185882 deg
```

마지막 두 trend 행은 gate가 아니며 H4/fine/final pass를 만들지 않는다. runner resource는 child exit `0`, successful tree samples `71`, wall `8.6075797 s`, stop `null`, peak tree WS `348827648 B`, peak tree private/commit `1704058880 B`를 기록했다. active token은 SHA-256 `81574c1099bdd140004940b7cb768a20298b153445c1b16b9e21de95011c48c2`의 consumed tombstone으로 교체됐고 original authorized token SHA-256 `f8a1aa5804b0edfd58f485faf83f7c6f73c284bc04b1a4abffbd8d0260aca4df`, `uses_remaining=0`, `consumption_validated_pass=true`, `next_stage_authorized=false`를 보존한다.

이 결과는 H2 local stage-only pass다. H4는 별도 preregistration/static audit/clean commit/one-use token 전에는 실행하지 않으며, final circle, withheld radius, EQ0, product accuracy와 8 GB status는 계속 미승인이다.

## AV-BS1 H4-P0 assembly-only manifest

H2 one-use result를 보존한 채 H4 topology/canonical assembly/resource envelope만 재현한다. 이 runner는 `manifest` 외 stage를 받지 않고 factorization/physics/token path를 노출하지 않는다.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File tools/research/run_av_bs1_h4_p0_stage.ps1 -Stage manifest
python -m pytest -q tests/test_research_av_bs1_boundary_schur_h4_p0.py
```

Frozen current bytes:

```text
fixture SHA-256: 331218882d2004d0d97e03378ae8af12b23cb4129a9c062e0592ee590a53e94b
runner SHA-256: b45c907fb5300e46717f423c8512a3500c7db8b42b647101d64a8a11440116c9
test SHA-256: 1539ef4151b8ea416c9ee2f2bf71c1baebd4e83bb2d3684e050437fcb1def40c
prereg doc SHA-256: 419dfb85ff40a43f2a0c2b1143b8531b16c95402f0c0d454764cfd3f5c03e524
manifest payload SHA-256: 71f8e902322016541bd9302231fa9965d7dfff67cdce1135e16ee1011a2aa990
static result: 6 passed
```

Manifest authority는 `V/E/T/B=32001/95488/63488/512`, mesh `a91b4bf...`, 7,424 topology-owned tags, raw/canonical K `8a020c80.../a510df2a...`, `256uκ` maximum/bound `8.540375354048666e-13/1.1605646201662821e-12`를 모두 다시 계산해 단언한다. inherited dense upper는 4 GiB gate를 실패하고 prospective capped envelope는 `factor_fit_unproven=true`, `primary_h4_authorized=false`로 남는다. 이 H4-P0 snapshot 당시 다음 후보였던 H4-P0R preregistration은 바로 아래 contract-only block으로 완료됐지만, 어느 block도 factorization 또는 H4 physics를 승인하지 않는다.

## AV-BS1 H4-P0R factor-pilot manifest-only contract

H4-P0R의 현재 상태는 **`preregistered_H4_P0R_contract_only_no_factor`**다. 이 contract-only fixture와 runner는 `manifest`만 노출하고 frozen H4 `KII/MII/AbII/ApII`, manual row/column equilibration, factor 순서와 resource/lifecycle schema를 재구성한다. review token과 executable factor path는 없으며 factorization, RHS/solve, extension, `Y`/Schur, modal/PDE/power와 physics를 실행하거나 승인하지 않는다.

```powershell
python tools/research/av_bs1_boundary_schur_h4_p0r.py --stage manifest
powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
  tools/research/run_av_bs1_h4_p0r_stage.ps1 -Stage manifest
python -m pytest -q tests/test_research_av_bs1_boundary_schur_h4_p0r.py
```

Frozen current contract evidence:

```text
fixture SHA-256: f6c4149e425021a9133d7ac98fbea403ba048e48f9c70d6e88171e3436374461
runner SHA-256: ff6623280a728b2dfe6ad219e965d4c21d2392020b6d0490dcc7e63f43a9e50f
test SHA-256: d071584cf6843ca9d2b75cac348ddfa343ac6f173646fe4c2575af68d6d73129
prereg doc SHA-256: 5db1b047ca72c72be338b6003507e04598c0b0b89b992302d3aea108a8d2e1f4
manifest payload SHA-256: eaab10df7fb1557490cc75db7e7ff9fca2881013b6a6fb13f02faea43ddf7023
matrix contract SHA-256: 89fadbf8f7f93118f6cccda65cc635bd37eeecfd70652e40baa6014c722e2f46
resource policy SHA-256: 13df68af8b9008824c09653bdb32a56c618107858cdb71b987bf6f4375915680
static result: 23 passed
authorization_state: not_authorized
factorization_performed: false
physics_solve_performed: false
next_stage_authorized: false
```

Matrix contract는 `31489×31489` real `KII/MII`의 nnz를 `204545/219393`, complex `AbII/ApII` nnz를 같은 `204545/219393`으로 고정한다. factor 후보는 `AbII` 뒤 `ApII`를 한 번씩 순차 처리하고 두 factor를 동시에 보존하지 않으며 `COLAMD`, `diag_pivot_thresh=1.0`, manual `Dr*A*Dc`, SuperLU internal equilibration off를 사용한다. portable factor bytes는 `24*(L_nnz+U_nnz)+8*(4*n+2)`이고 per-factor cap은 2 GiB다. 기존 4/5/5 GiB tree stops와 900 s wall cap, pre-spawn available physical/commit headroom `5,081,474,791/5,618,345,703 B`도 유지한다. 이 값들은 미래 pilot의 사전등록 gate일 뿐 아직 측정 결과가 아니다.

Exact next는 현재 manifest-only contract를 clean commit과 독립 audit에 고정한 뒤, 별도 H4-P0R executable fixture/runner/test/result/finalizer와 one-use token을 사전등록하는 것이다. 그 audited token 뒤에만 `AbII`, `ApII`를 순차 factor하는 factor-only run 한 번을 허용하고 pass/fail 모두 token을 consume한다. 그 결과를 독립 감사하기 전에는 H4-P1, `primary-h4` 또는 어떤 H4 physics도 열지 않는다.

## AV-BS1 H4-P0R-P1 safe replay and interruption evidence

위 문단은 immutable manifest-only parent의 당시 next step이다. 후속 P1 executable/lifecycle은 구현됐고 일곱 public invocation은 모두 certified factor result 없이 fail-closed 됐다. 일곱 번째는 `A_background_II` native `splu` 반환만 완료했으며 certificate/prefix는 없고 `A_conductor_II`는 미시도다. 일곱 one-use token은 재실행 없이 소비·삭제됐으며 현재 checkout에는 P1 token이 없다. 아래 명령만 safe static replay로 허용하며 `-Stage primary-h4-p0r`, hidden inner mode, finalizer 또는 consumer를 직접 호출하지 않는다.

```powershell
python tools/research/av_bs1_boundary_schur_h4_p0r_p1.py --stage manifest
powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
  tools/research/run_av_bs1_h4_p0r_p1_stage.ps1 -Stage manifest
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTEST_ADDOPTS='-p no:cacheprovider'
python -m pytest -q tests/test_research_av_bs1_boundary_schur_h4_p0r_p1.py
```

다음은 세 번째 public invocation 전의 **역사적 retry-v2** static candidate binding이다. P1 preregistration document와 live wrapper payload는 당시 문서 동결 뒤 외부에서 계산했다.

```text
fixture SHA-256: 8c497cb1d0926600b2ddd974df6fd8d40b09471c617869294a01c0e018ce5acf
runner SHA-256: 4272d6725cb0e16b7ce39b083985965f792da5893f222ba07cf91695fba8f30c
test SHA-256: b44366596690d6d55d18a89d8df2370faca767fca722affd5e07268da3195235
static result: 187 passed
token_state: absent
factor_fit_unproven: true
factorization_performed: false
physics_solve_performed: false
next_stage_authorized: false
```

두 번째 시도의 immutable v1 close만 읽으려면 다음처럼 exact file/hash와 non-sensitive summary를 확인한다. 이 evidence를 수정·이동·삭제하지 않는다.

```powershell
$p = 'validation-output/av-bs1/outer-observer/session-e017a79e5ce345959d8e77f46b8d90b2/outer-resource-envelope-close.json'
Get-FileHash -Algorithm SHA256 -LiteralPath $p
$j = Get-Content -Raw -Encoding UTF8 -LiteralPath $p | ConvertFrom-Json
$j | Select-Object schema,started_utc,ended_utc,wall_elapsed_nanoseconds,stop_reason,monitor_error_present,sample_count,inner_visible_sample_count,inner_actual_exit_code,cleanup_verified,mandatory_outer_resource_gate_pass,post_cleanup_recovery_state
```

Expected SHA-256은 `3de68a75e4e1fa23876b63f1843ad217f7ba0290f8a86b491bac09124f5ea39c`다. `OUTER_RESOURCE_EXCEPTION`, samples `2/2`, inner exit `-1`, cleanup true, gate false, no claim/no recovery를 보존한다. ready/start-release/complete/exit-release, claim, result, factor prefix, tombstone와 seal은 없다. 따라서 factor/RHS/solve/physics는 시작할 수 없었다. v1 close에는 exact caught PID/API/message가 없으므로 short-lived `Add-Type` descendant race는 high-confidence inference로만 재현 문서에 남긴다.

close-v2 correction은 instrumented outer tree에서만 maximum 3 whole attempts를 허용한다. native exited 또는 exact not-found, fresh complete snapshot absence와 stable root를 모두 확인한 non-root만 retry한다. 모든 partial sum을 버리고 root/reuse/live/access/incomplete-snapshot/exhaustion은 fatal이다. 이전 token commits `b6c8615...`와 `d9e064f...`는 mechanically checkout 가능해도 영구 재사용 금지다. 다음 public run은 새로운 token-absent contract audit와 fresh token-only child 뒤에만 별도 승인된다.

### Third public invocation evidence

역사적 retry-v2 contract `29aeed318abb1cefb189917d707066987e5ea3b3`의
fresh token-only child는 `6328174b8315f71b407f79584f134359b9f48685`, token
ID는 `7af97159e9224924832085a293c65c27`이다. public invocation은
`2026-08-15T10:58:35.7129115Z`부터 `2026-08-15T10:58:39.7341700Z`까지
한 번 실행되고 exit `2`로 끝났다. 다음 read-only 명령은 보존된 evidence의
경로와 hash만 확인하며 어느 파일도 수정·이동·삭제하지 않는다.

```powershell
$outer = 'validation-output/av-bs1/outer-observer/session-399b2ac2a1754822bd7da61aae88acaf/outer-resource-envelope-close.json'
$control = 'validation-output/av-bs1/control-plane/session-401d8ddafdbe42628541ebe2bdc367cf'
Get-FileHash -Algorithm SHA256 -LiteralPath $outer
Get-FileHash -Algorithm SHA256 -LiteralPath "$control/preflight-6cb7af1c254d49309bb8e5866c6501fc/control-plane-report.json"
Get-FileHash -Algorithm SHA256 -LiteralPath "$control/session-index-0001.json"
Get-FileHash -Algorithm SHA256 -LiteralPath "$control/session-index-0002.json"
```

Expected SHA-256은 outer close
`a675b5f829e343717d66c1c1d4d40335aed7015c09ad5c3385d2d90aa97e20cd`,
control report
`da6069826cf46335bb8b86655abfaad8eb3fb61c87d076ebf916849f998c5f7b`,
pre-close/final indexes
`c693ad457906f54047427e7b9d831d9b1cd764d0457d3557a872c6a5f8fe5018`와
`f4824fba0f58c71b85acd1fc286aa29e0d323ef704e828649119c6e39f78d579`다.
outer inner-ready/start-release와 control bootstrap-ready/start-release는 있지만
control target-complete/exit-release, claim, factor prefix, tombstone와 seal은 없다.
control report는 default `MaximumAttempts=1` sampling에서
`TRANSIENT_DESCENDANT_DISAPPEARANCE_RETRY_EXHAUSTED`, cleanup true, gate false를
증명한다. 따라서 이는 control preflight monitor failure이고 factor/RHS/solve/H4
physics/PowerSI/8 GiB 결과가 아니다. token은 byte-identical 상태로 재사용되지 않았고
deletion-only commit `ba97dd8b274659a649d9a4020193c3ef72572665`에서 폐기됐다.

### Frozen control-plane retry-v3 replay boundary

retry-v3는 `Get-TreeSample` default와 factor call sites의
`MaximumAttempts=1`을 유지한다. explicit maximum `3`/event limit `16`은
`control_pre_helper_tree_sample`, `control_active_outer_tree_sample`,
`control_active_cleanup_root_tree_sample`,
`control_post_completion_outer_tree_sample`,
`control_post_completion_cleanup_root_tree_sample`,
`control_envelope_close_tree_sample`의 여섯 control context와 기존 instrumented
outer context에만 적용한다. control report/close와 execution resource scope는 v2다.

report event는 close event의 exact prefix이고, close-only event의 PID/birth는 frozen
report identity map과 일치해야 한다. truncation 또는 confirmed-count/event-count
불일치는 `CONTROL_TREE_SAMPLE_RETRY_EVIDENCE_INCOMPLETE`, uncovered close-only
identity는 `CONTROL_ENVELOPE_CLOSE_RETRY_IDENTITY_UNCOVERED`로 gate를 false로 둔다.
첫 fatal `monitor_failure`는 이후 sample 성공에도 유지되며 attempt-3 exhaustion은
pass할 수 없다. no-claim cleanup은 token을 변경하거나 seal하지 않고 exact original,
present drift, absent를 각각 versioned recovery-state로 분류한다. 세 prior token commits
`b6c8615...`, `d9e064f...`, `6328174...`는 영구 재사용 금지다. 현재 token absent,
factorization/physics false, `next_stage_authorized=false`다.

outer/inner sampler는 하나의 PID→birth evidence registry를 공유하지만 cleanup ID set은
분리한다. 따라서 inner-only identity도 final close에 직렬화되고 cross-context PID reuse는
exact sample에서 fatal이며 cleanup ownership은 넓어지지 않는다. metric `exited` birth는
confirmation 전에 positive `Int64`와 expected-birth equality를 만족해야 하고 invalid/mismatch는
retry 없이 fatal이다. report freeze 뒤 close threshold/system check도 기존 stop reason이 없을
때만 새 reason을 설정해 첫 failed disposition을 보존한다.

현재 frozen retry-v3 binding은 다음과 같다. final preregistration document SHA와
그 값을 포함하는 live wrapper payload는 이 문서군의 byte freeze 뒤 별도 계산한다.

```text
fixture SHA-256: 95c9f5c08282105f7934fbea194694fff3ab850daac633619534044721639234
runner SHA-256: cee65497b414a5c9da2b572dc2f026a496b304889c7ddf86a0a2856ebb842d9c
test SHA-256: 0f118612aefa9dc80526abf6604a2234f14c50454000f76ef172a534398042fd
full no-cache: 312 passed
implementer focused: 45 passed
independent focused: 74 passed, 238 deselected
token_state: absent
factorization_performed: false
physics_solve_performed: false
next_stage_authorized: false
```

독립 audit의 두 deferred observation은 failed-only hardening이다. capped attempt-3
virtual identity omission은 별도 binding이 없지만 exhaustion 자체가 non-null failure와
gate false를 강제한다. preserved failed stop reason은 final close에서 nonempty만 요구하고
exact allowlist로 다시 제한하지 않지만 sticky failure가 이미 authorization을 차단한다.
둘 다 failed evidence를 pass로 바꾸지 못한다.

### Fourth public invocation evidence

Clean contract `4d39eab9c464f67e8684e2d539ac3a2b092142a2` was the sole parent
of token-only commit `9b4854d0cc7ae21e5e9eafc394a9474cb53ea689`. Token ID
`4c209c8a4dac49b89c59dd69bf68c4c2`, raw SHA-256
`d3bb3a42c83848678f0b99c0c669fffb5ab02e594cfba31ee9251c8216d05d64`,
was exposed to exactly one public invocation from
`2026-08-15T13:37:16.0468244Z` through
`2026-08-15T13:37:20.5004777Z`; the command returned exit `2`.

The following read-only checks address only the immutable fourth-attempt
evidence. They do not invoke manifest, preflight, primary, factor, finalizer, or
consumer stages.

```powershell
$outer = 'validation-output/av-bs1/outer-observer/session-2085628c53894c8eade7a735ec60f36c'
$control = 'validation-output/av-bs1/control-plane/session-b2db30095a1042f4b4d5bbdc56550727'
Get-FileHash -Algorithm SHA256 -LiteralPath "$outer/inner-ready.json"
Get-FileHash -Algorithm SHA256 -LiteralPath "$outer/outer-start-release.json"
Get-FileHash -Algorithm SHA256 -LiteralPath "$outer/outer-resource-envelope-close.json"
Get-FileHash -Algorithm SHA256 -LiteralPath "$control/bounded-bootstrap.py"
Get-FileHash -Algorithm SHA256 -LiteralPath "$control/canonical-json-sha256.py"
Get-FileHash -Algorithm SHA256 -LiteralPath "$control/preflight-2b050acbc608406fbf0308823618358f/bootstrap-ready.json"
Get-FileHash -Algorithm SHA256 -LiteralPath "$control/preflight-2b050acbc608406fbf0308823618358f/start-release.json"
```

Expected SHA-256 values are:

```text
outer inner-ready: 485beb7c468c5f849554a099bf8fa4f456dc4748782968cbc4d2a3729bf7c5fc
outer start-release: 296353a46cccd301adfdbeeef7d7538a79a724a73225c219c988608e20060c51
outer close: 73073432faa0525f560b6536ea2a5fd54329ba3a30f04ab05ab8cef59460e758
control bootstrap: 3b8d2230e316b541c0d59d6334c13eaa1cf1c0e56ab7f02b1753dbefaeddec0f
control canonical helper: 7d80a4c230409aa0462a59f5cb9de167101ddd53b9f31119d0679f08a853d45c
control bootstrap-ready: 3d123e0ca997949cc24d7a80ac775d0f129d803632e64e59a19d8bde80d05416
control start-release: d0c6b2f8fbfcf39ea8bfdc8d9d28398007a7d75347224d3f6150616769406ede
outer/control stdout and stderr: e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
```

No control target-complete, control report, control close, control session index,
outer inner-complete, outer exit-release, claim, factor prefix, numerical result,
tombstone, quarantine, or terminal seal exists for this token. The outer close
records `OUTER_RESOURCE_EXCEPTION`, `mandatory_outer_resource_gate_pass=false`,
cleanup verified, and monitor failure
`NONROOT_DISAPPEARANCE_NOT_CONFIRMED` at `outer_tree_sample` attempt `1` for PID
`40224`. Expected and observed birth are both `639223978396874096`, and the
failure has no Win32 error. This proves an identity-queryable exited process was
still present in the first complete snapshot. It is not a memory limit result:
peak tree working set was `464506880 B`, peak tree commit `1718468608 B`,
lifetime peak commit `1806934016 B`, minimum commit headroom `73405591552 B`,
and minimum available physical memory `47205404672 B`.

The exact original token remained on disk because no claim existed, but the
public attempt was semantically spent. It was never reused and deletion-only
commit `f1aeeac018a96cbd82341db36efbf5e5a9a55431` removed it. Current
token state is absent. Factorization, RHS, solve, H4 physics, PowerSI evidence,
and 8 GiB fit evidence were never started.

### Historical retry-v4 replay boundary

Retry-v4 leaves Python, all schemas, the three-attempt whole-sample retry, the
16-event cap, and factor sampling unchanged. Only when a caller already uses
`MaximumAttempts>1` and the initial non-root probe is `exited` with a positive
birth equal to the bound birth may an initial snapshot-present result receive
two 25 ms rechecks, for three complete snapshots total. Root identity is checked
around every recheck. A final absent snapshot returns the existing
`CONFIRMED_NONROOT_DISAPPEARANCE` event; presence after all three remains
`NONROOT_DISAPPEARANCE_NOT_CONFIRMED`. `not_found`/87 plus presence, default-one
factor sampling, live reappearance, PID reuse, malformed/query/access failures,
root loss/reuse, and incomplete snapshots remain fatal.

```text
Python fixture SHA-256: 95c9f5c08282105f7934fbea194694fff3ab850daac633619534044721639234
PowerShell runner SHA-256: 7893cd57fd4b1686addd434fa0103d9f3b0e0087f4783f2c82e459661abfb645
static tests SHA-256: 18aabcdf7b32c6b013aec65497d31f4d908f3085f53f64cb3791f38a2e79381d
focused retry-v4 tests: 11/11 passed
full no-cache P1 suite: 322/322 passed in 82.01 s
PowerShell AST: 47,623 tokens / 0 errors
Python AST: clean
token_state: absent
factorization_performed: false
physics_solve_performed: false
next_stage_authorized: false
```

### Fifth public invocation evidence

The fifth public `primary-h4-p0r` invocation is an immutable failed attempt, not
a command to replay. Clean retry-v4 contract
`099db849564207b636f7431ee2fb52a540a7cb4e` was the sole parent of token-only
commit `5ba4b69398f526a0fcf640cf1dc4e7197cc7e660`. The invocation ran once from
`2026-08-15T14:33:12.122Z` through `2026-08-15T14:34:23.345Z` and returned exit
`2`. Token ID `96d4f060ffa94d4888ffe3e59f550225` had original raw/canonical
SHA-256 `a9cbd954b27685892bf720c777ef57f49830da7970eee9a267d6d87eb5665d00` /
`a17271e2e3d4621a04b002edc7ada972a462f60552958282b3d07f99d441852b`.

The attempt created claim raw/canonical SHA-256
`0301eeccbf092799b97a914b4678bf8856aa8bb73296a07144435eddabf78103` /
`d9b7a41503dc32c591da9b2a06b512df5675f3a46eb78eaa24d3fe2b7e4b596e`
and guard raw/canonical SHA-256
`78c66a1b45e19c04a30a37ac433d0437994798408cce46afb2c327a8f2b80c65` /
`9dd8bd575628ef86b75569baf40a4d56cf1e850e9bd16676de4e3d45a723a17c`.
Factor-only child PID `55552`, birth `639224012129440335`, was visible in all
`108` successful samples. Resource SHA-256
`d9b9a858c8f4aaebc1ca6ebb34f90235bcb84dd6288c04ecc95b3d7e5abc1da6`
records `MONITOR_QUERY_FAILED` /
`NONROOT_DISAPPEARANCE_NOT_CONFIRMED`. The four active factor calls had not
opted into maximum `3`, so they used the function default `1`.

No factor-prefix-1/2, factor-complete, or monitor-release file exists;
`numerical.json` is zero bytes. Result file SHA-256
`cf0d59c36e3f8275eeee56f30ccc7a2db74c986052714c6e31c6e26bc9de08e9`
is a schema-failure wrapper because the zero-byte stdout was hashed in the
resource but omitted from finalizer arguments. That overlay is secondary and
deferred; the primary disposition is resource monitor stop. Completed factors
and certificates are empty, factor attempt/performance are `null`, and RHS,
solve, physics, PowerSI, and accuracy output are absent.

Outer session
`validation-output/av-bs1/outer-observer/session-1c765eb54ec54d5a801c87c960970c50`
has close SHA-256
`9d0c0aab58e78eb5d91d36be2aef46bf89968df87d4abcc044089805acce9af2`.
It verified cleanup and inner exit `2` with no monitor failure/stop reason, but
counted `27` confirmed disappearances, stored the bounded `16`, and marked
truncation. Therefore the mandatory outer gate was false and the expected seal
is absent. The consumed tombstone SHA-256 is
`3caed852fd51fd908cbfa750becd2c63cbd6f768abff91eba23221bd0f418c2b`;
commit `46c08d405f530cce0cfbba9d908f8c266a26a002` preserves it and deletion-only
commit `71d3dab442cbdfa6361e4e91de57d8f5b4d1a990` retires the token.

### Frozen retry-v5 replay boundary

Retry-v5 makes exactly two run-enabling changes. The four active factor tree
samples pass explicit `MaximumAttempts=3`; the caught-final cleanup sample keeps
default `1`. The shared bounded outer/control retry-event cap rises from `16`
to `64`, which retains the observed `27` without weakening the existing
truncation/no-seal gate beyond `64`. Retry eligibility, two 25 ms waits, three
complete snapshots, and every fatal root/reuse/query/access/not-found/
incomplete-snapshot rule are unchanged. Empty-stdout normalization is not part
of retry-v5.

```text
Python fixture SHA-256: 54aa7da9daa013cec41585ae07757e6c54e8e9d15da7f4f16d4f6546b6fb35aa
PowerShell runner SHA-256: 3888524877f7a90966fb932f7f9fc4c9a48480134eb6295712eaa0ef548416a3
static tests SHA-256: b51498ebe27a0210a09b3a12b26e0146d39c1249906469bcb1add1d2f2443f08
focused retry-v5 tests: 29/29 passed in 15.91 s
full no-cache P1 suite: 324/324 passed in 82.91 s
PowerShell AST: 47,631 tokens / 0 errors
Python syntax: clean
token_state: absent
factor_fit_unproven: true
next_stage_authorized: false
```

At that frozen retry-v5 point, only manifest/static replay was safe before a new
clean contract and external authorization cycle. The five then-spent token
commits were not reusable.

### Sixth public invocation evidence

The sixth public `primary-h4-p0r` invocation is also immutable evidence, never a
command to replay. Clean retry-v5 contract
`bddbf9cb3547ae0385c6e6bbc47424f630cca87e` was the sole parent of token-only
commit `82775327d79742b6c3111ad33a87fd1a4953ee79`. Token ID
`a3f49b44dd164da3a0ca1a6dc4c976c3` had original raw/canonical SHA-256
`69b7e93720d8044d60f4ca95ccc5440670904d27f75ee77730e2947a01d58ed4` /
`68444b687e63750bdd0c7056a45b29d53d825c3572c2db03245cb032d63037a8`.
Outer observation ran from `2026-08-15T15:39:01.9701124Z` through
`2026-08-15T15:39:55.3757836Z`; the public invocation returned exit `2`.

Claim raw SHA-256 is
`f15962f38e5141420a15536eda96653c76fa2ff26be6ec1e7c3fb69fe50ab955`;
guard and monitor-ready raw SHA-256 are
`1acdc9f1d7eaa04a4e2800dbec81710b88580e1718aee073e2b49d0618602b09` and
`9d65ff505fea0eb6c50351f3f0dc22b6a1afb4641d24f4e51380badba471dcaf`.
Factor child PID `55380` was launched. Provisional numerical artifact SHA-256
`ab5adac793f9acda882bfa0c66c5ce20319e1cb06fe5cb88c9968803acf8822e`
contains payload SHA-256
`55d4436ab55c77be6d7fb37549fc8e4e5b94871cf05518e7dfc97fb93c5bcd25`,
failure `BLOCKED_AV_BS_RESULT_SCHEMA`, detail
`claimed preflight payload mismatch`, and attempted/performed `false/false`.
The redundant post-claim preflight comparison failed before `_factor_one` and
the `splu` call. No factor-complete, monitor-release, prefix, completed factor,
certificate, RHS, solve, H4 physics, PowerSI, or 8 GiB result exists.

Outer session
`validation-output/av-bs1/outer-observer/session-21f46fec2e314e248cf051e4273b97f0`
has ready/start/close SHA-256
`b91d5b80d73efc02bd0ca55a48692d2db744fd033de1fff63ef301e20a88dd17`,
`118addd5e9740ab3ddd0e8dfd48279d933f9c874db6f073305096417b56ef3bd`,
and `d2777207e4000cbde8e4bf2332eeba96dd54ec3b8918a0d107049531d15cd8f8`.
It independently stopped on attempt `3`, context `outer_tree_sample`, operation
`get_process_times`, PID `53404`, expected/observed birth
`639224051920616566`, code
`TRANSIENT_DESCENDANT_DISAPPEARANCE_RETRY_EXHAUSTED`, confirmation
`signaled_handle_and_complete_snapshot_absent`. It retained all `30/30` retry
events, set `tree_sample_retry_events_truncated=false`, verified cleanup, and
wrote no seal. All `107` recorded identities were absent at forensic review.
Peak working set/private commit/lifetime peak commit were
`513355776/1790554112/2135834624` bytes; minimum available physical and commit
headroom were `46603452416/72951267328` bytes. This was not a resource ceiling.

The outer emergency layer lacked trusted inner completion evidence and
therefore conservatively recorded attempted/performed null/null. That does not
contradict the direct child wrapper's false/false. Emergency raw record SHA-256
`43bb34b225b6b1d376715615a90b7b6202e10da5a10679484a644b1911c4d1fb`
is exact and honest about consumption and lack of authority, but its nested
`bindings` has seven keys and omits `resource_policy_sha256`. Its stored review
binding was computed from the historical full eight-key preflight bindings, so
the strict validator correctly rejects it. Commit
`06061a234ad7b1b911d7425b7765482bda58a87a` preserves this provisional record;
deletion-only retirement `c001b4498fc750b5955f5118844945c499fce119`
removes the token. It is not a validated terminal tombstone or seal.

The surviving ignored `%TEMP%` directory
`av-bs1-241bd96890c24f6b8117fca08e2d5aa3` is forensic source evidence, not a
tracked immutable reproduction input. Do not move, modify, or promote its path
to a canonical result contract. The claim, tracked validation-output, commit
objects, and recorded hashes are the durable evidence.

### Historical retry-v6 replay boundary

Retry-v6 makes only two corrections: remove the redundant three-line
post-claim preflight call/comparison in Python, and add nested
`bindings.resource_policy_sha256` to the emergency runner producer. The strict
validator, schema, ABI, retry-v5 max3/cap64 behavior, matrix/factor path, RHS,
solve, and physics logic do not change.

```text
Python fixture SHA-256: 2373a13f51e2833e416e1ce6326587b9e1c782b5165d99f7002e7dbc4658ebc4
PowerShell runner SHA-256: 852ce8a03b25e33b9eb26ec6f5ce295381dab493b1b26762ddea14be7196000d
static tests SHA-256: f1d0b044cbf53e90dba128ec398ccd8b7a81da8c5137bea202b2852eb3f288af
focused retry-v6 tests: 8/8 passed
full no-cache P1 suite: 330/330 passed in 81.83 s
token_state: absent
factor_fit_unproven: true
next_stage_authorized: false
```

Only manifest/static replay was safe at this frozen retry-v6 boundary. None of
the six then-spent token commits was reusable.

### Seventh public invocation and current retry-v7 boundary

The seventh public invocation is immutable evidence, not a command to replay.
It ran once at token-only commit
`4f60bd5e5fe4e166e72ba00e9bb019a70504e7df` with token ID/raw SHA-256
`a7a3942cb0cf421e9fc52fd43176bb35` /
`3b6cd6557832358e6235824d680b8fe01eaf29899b89c2dea1850e9109e4511b`.
Result and quarantined final bytes share SHA-256
`e2be4dd5fa0467012c194239c8b8ac833723b4355a371c42a8aafc089efeb932`.
Numerical raw/payload SHA-256 are
`5bf26072794a4030ec311d3c60bcf3efd8c8d112319587d0a48d65490fd56e72` /
`173e22362525cb34e3d1812731779b5ad454e6d5ea16245ad891904814df19ee`.

Native `splu` returned for `A_background_II`. The child therefore records
attempted/performed `true/true` and `completed_factors=["A_background_II"]`.
The next check failed on `A_background_II native/exported nnz mismatch` before
certificate construction: `factor_certificates=[]`, both prefix hashes are
null, and `A_conductor_II` was not attempted. Neither native `factor.nnz` nor
exported `L.nnz`/`U.nnz` survived into durable evidence, so their exact counts
or delta must not be inferred.

Resource report SHA-256
`0f738eb5f098944618d80b1cb2c18b161068a991ef8a4e98ed8a055ba6af324b`
contains `174` inner samples. Outer close SHA-256
`0e88d08f60f56c95678dd8f6c7ae850b681fd60ba8ed6fc9d3868dcb8080ec01`
contains `573` samples and all `31` retry events with no truncation. Both
mandatory resource gates pass. Terminal seal SHA-256
`0b2f56231315bb260921f2b3fe220fea33315cce4679964c6b86711a4f8b51a1`
is complete and fail-closed: authoritative pass and next-stage authorization
are false. These counts prove monitoring coverage, not numerical accuracy or an
8 GiB factor fit. No RHS, solve, H4 physics, or PowerSI work ran.

Consumed-record commit `51e50699546fe5b594dfdc669fb1628de0c3adb7` and
deletion-only retirement `17414cf0be147d0d5d9d75046354e199f707e7a6` leave
the token absent. Retry-v7 changes no schema, retry/cap policy, or factor order.
It accepts only `0 < exported L/U nnz <= native factor nnz`, validates the
native and exported portable-byte formulas separately, and applies the existing
cap to both representations.

```text
Python fixture SHA-256: 7a1dba5eafcbf601fd532a9a3d2bc10de2218c74bccdacd04bdba5f1038df348
PowerShell runner SHA-256: 852ce8a03b25e33b9eb26ec6f5ce295381dab493b1b26762ddea14be7196000d
static tests SHA-256: bd2e3e2ca4d668d3cd8c92b55737a74f6bff743a823bae024713d59d393c8ba7
focused retry-v7 tests: 25/25 passed
full no-cache P1 suite: 341/341 passed in 88.40 s
token_state: absent
factor_fit_unproven: true
next_stage_authorized: false
```

Only manifest/static replay is safe before a new clean no-token contract and
independent authorization lifecycle complete. Do not invoke any of the seven
spent token commits or any hidden inner/finalizer/consumer stage directly.

## Focused regression

```powershell
python -m pytest tests/test_tri_fem_gap.py tests/test_tri_fem_pair.py tests/test_tri_fem_sheet.py tests/test_tri_fem_stack.py tests/test_mfdm_solver.py tests/test_mfdm_adapter.py tests/test_surface_patch_plane.py tests/test_via_peec.py tests/test_pad_augmented_capacitance.py tests/test_research_axisymmetric_electrostatics.py tests/test_edge_cell_capacitance.py tests/test_global_mna.py -q
python -m pytest tests/test_finite_route_reducer.py tests/test_fft_bem_capacitance.py -q
```
