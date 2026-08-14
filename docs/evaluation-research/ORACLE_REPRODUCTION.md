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

## Focused regression

```powershell
python -m pytest tests/test_tri_fem_gap.py tests/test_tri_fem_pair.py tests/test_tri_fem_sheet.py tests/test_tri_fem_stack.py tests/test_mfdm_solver.py tests/test_mfdm_adapter.py tests/test_surface_patch_plane.py tests/test_via_peec.py tests/test_pad_augmented_capacitance.py tests/test_research_axisymmetric_electrostatics.py tests/test_edge_cell_capacitance.py tests/test_global_mna.py -q
python -m pytest tests/test_finite_route_reducer.py tests/test_fft_bem_capacitance.py -q
```
