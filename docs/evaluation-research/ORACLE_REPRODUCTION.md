# SPD Decap PI Evaluator v0.22.0 — Oracle Reproduction Appendix

최종 갱신: 2026-08-14 (Asia/Seoul)

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

## Focused regression

```powershell
python -m pytest tests/test_tri_fem_gap.py tests/test_tri_fem_pair.py tests/test_tri_fem_sheet.py tests/test_tri_fem_stack.py tests/test_mfdm_solver.py tests/test_mfdm_adapter.py tests/test_surface_patch_plane.py tests/test_via_peec.py tests/test_pad_augmented_capacitance.py tests/test_research_axisymmetric_electrostatics.py tests/test_edge_cell_capacitance.py tests/test_global_mna.py -q
python -m pytest tests/test_finite_route_reducer.py tests/test_fft_bem_capacitance.py -q
```
