"""Fail-closed D117 Triangle W0 research pilot (no subprocess/network)."""
from __future__ import annotations
import argparse, hashlib, importlib, importlib.metadata, json, math, sys, zipfile
from pathlib import Path
from typing import Sequence

PRODUCT="SPD Decap PI Evaluator"; VERSION="0.23.1"
TRIANGLE_OPTIONS="pq15Cz"
AUTHORIZED_ARTIFACT_ROOT=Path(r"D:\SPD-Decap-PI-Evaluator-W7\e2f219e71d8c8a397009f72242cce10d78cfc7ab\260903-d117-triangle-w0-research-pilot-01"); AUTHORIZED_SITE=AUTHORIZED_ARTIFACT_ROOT/"site"
WHEEL_NAME="triangle-20250106-cp312-cp312-win_amd64.whl"; WHEEL_SIZE=1426720; WHEEL_SHA256="0327032a7984a7262180ef2ddd78b36dfdcdecbc79f0f9f173732ce7c670b8ed"
PYPI_METADATA_URL="https://pypi.org/pypi/triangle/20250106/json"; TRIANGLE_RELEASE_URL="https://pypi.org/project/triangle/20250106/"; WHEEL_URL="https://files.pythonhosted.org/packages/a1/a5/4a09c3f9d2687d8752c912a97f2c5086cdd83721b3b13f8288f13b771fa7/triangle-20250106-cp312-cp312-win_amd64.whl"; WRAPPER_LICENSE_URL="https://github.com/drufat/triangle/blob/master/LICENSE"; UPSTREAM_LICENSE_URL="https://www.cs.cmu.edu/~quake/triangle.html"
EXPECTED_D115C_SHA="0c8daed46719b199ee50b1ec9b94dd5cac0fdedecbc7b58668b7665b5e9a2a80"; EXPECTED_D115C_SIZE=1063056
EXPECTED_D103_SHA="4ea63cf86f6b1f4e8d56eead82033606cd2a2f9b6fae1b14e857a51e4c0749f9"; EXPECTED_D103_SIZE=204735
EXPECTED_D104_SHA="bf3d965281f3fd09cf6be26cbd49cca22b4b3085f265ba859349e8091754263b"; EXPECTED_D104_SIZE=19728
EXPECTED_SOURCE_SHA="a3eabfa5a30945228e674b32bd7ef641400bcbcd382fc2951cbbe81a16293ff5"; EXPECTED_SOURCE_SIZE=258181
EXPECTED_INTERSECTION_SHA="f3a2abe59324887bec79aaab38b670f0943cb5cf11936548343a8ef6940cad04"; EXPECTED_INTERSECTION_SIZE=317
EXPECTED_ISLAND="spd-surface-island:cb8510a79529b7f6f4f4afd4"; EXPECTED_LAYER="Signal$L30(OTHER_POWER1)"; EXPECTED_NET="ADC_VDD_180_VQPS_SYS_1_AON/0"; EXPECTED_BOUNDS=[-12000.0,12000.0,-11000.0,13000.0]; EXPECTED_SNAPPED_PM=[-12000000000,12000000000,-11000000000,13000000000]; EXPECTED_AREA=926540.7647500002; EXPECTED_STOP="STOP_W0_EIGHT_ROW_COVERAGE_CONFLICT"
MAX_ORIGINAL_VERTICES=1024; MAX_SOURCE_VERTICES=MAX_ORIGINAL_VERTICES; MAX_PSLG_VERTICES=4096; MAX_MESH_VERTICES=50000; MAX_MESH_FACES=100000
class Refusal(RuntimeError): pass
def _sha(p:Path):
    if not p.is_file() or p.is_symlink(): raise Refusal("input missing or reparse point")
    h=hashlib.sha256(); n=0
    with p.open("rb") as f:
        for b in iter(lambda:f.read(1<<20),b""): h.update(b); n+=len(b)
    return n,h.hexdigest()
def _inside(p:Path,r:Path):
    try:return p.resolve(strict=False).is_relative_to(r.resolve(strict=True))
    except OSError:return False
def _verify_wheel_site(site:Path):
    if site.resolve(strict=True)!=AUTHORIZED_SITE.resolve(strict=True): raise Refusal("unauthorized triangle site")
    if any(f.suffix.lower()==".pyc" for f in site.rglob("*")): raise Refusal("bytecode present in Triangle site")
    wheel=AUTHORIZED_ARTIFACT_ROOT/WHEEL_NAME
    if _sha(wheel)!=(WHEEL_SIZE,WHEEL_SHA256): raise Refusal("Triangle wheel identity mismatch")
    with zipfile.ZipFile(wheel) as z:
        names=z.namelist()
        if any(n.startswith(("/","\\")) or ".." in n.split("/") for n in names): raise Refusal("unsafe wheel path")
        payload={n:z.read(n) for n in names if n.startswith("triangle/") or n.startswith("triangle.libs/")}
        wheel_txt=z.read("triangle-20250106.dist-info/WHEEL").decode()
        if "Tag: cp312-cp312-win_amd64" not in wheel_txt: raise Refusal("wheel tag mismatch")
    for name,data in payload.items():
        f=site/Path(name)
        if not f.is_file() or f.is_symlink() or f.read_bytes()!=data: raise Refusal(f"installed payload mismatch: {name}")
    allowed={str(Path(n)) for n in payload}
    for f in site.rglob("*"):
        if f.is_file() and f.suffix.lower() in {".py",".pyd",".dll"} and str(f.relative_to(site)) not in allowed: raise Refusal("unexpected importable payload")
def load_triangle_site(site:Path):
    site=site.resolve(strict=True); _verify_wheel_site(site)
    d=next((x for x in importlib.metadata.distributions(path=[str(site)]) if x.metadata.get("Name","").lower()=="triangle"),None)
    if d is None or d.version!="20250106": raise Refusal("Triangle version metadata mismatch")
    for n in list(sys.modules):
        if n=="triangle" or n.startswith("triangle."): del sys.modules[n]
    sys.path[:]=[p for p in sys.path if Path(p or ".").resolve(strict=False)!=site]; sys.path.insert(0,str(site)); m=importlib.import_module("triangle")
    if not getattr(m,"__file__",None) or not Path(m.__file__).resolve(strict=True).is_relative_to(site) or getattr(m,"__version__",None)!="20250106": raise Refusal("Triangle import escaped site")
    return m
def _ring(r):
    p=[(float(x),float(y)) for x,y in r.coords[:-1]]
    if len(p)<3 or len(p)!=len(set(p)) or any(not math.isfinite(x) or not math.isfinite(y) for x,y in p): raise Refusal("invalid ring")
    return p
def build_pslg(shape,thickness=20.0):
    from shapely.geometry import Polygon
    if not math.isfinite(float(thickness)) or float(thickness) <= 0: raise Refusal("invalid thickness")
    if shape.geom_type!="Polygon" or shape.is_empty or not shape.is_valid or shape.area<=0 or not math.isfinite(shape.area): raise Refusal("only valid single Polygon accepted")
    rings=[shape.exterior,*shape.interiors]; count=sum(len(r.coords)-1 for r in rings)
    if count>MAX_ORIGINAL_VERTICES: raise Refusal("original vertex ceiling exceeded")
    vs=[]; ss=[]; mm=[]; originals=[]; idx={}; marker=1; step=4*thickness
    for ring in rings:
        pts=_ring(ring)
        for i,a in enumerate(pts):
            b=pts[(i+1)%len(pts)]; parts=1
            while parts*step<math.dist(a,b): parts*=2
            idx.setdefault(a,len(vs));
            if idx[a]==len(vs): vs.append(a)
            prev=idx[a]
            for j in range(1,parts+1):
                q=b if j == parts else (a[0]+(b[0]-a[0])*j/parts,a[1]+(b[1]-a[1])*j/parts); idx.setdefault(q,len(vs))
                if idx[q]==len(vs): vs.append(q)
                cur=idx[q]; ss.append((prev,cur)); mm.append(marker+i); prev=cur
            originals.append((a,b,marker+i))
        marker+=len(pts)
    if len(vs)>MAX_PSLG_VERTICES: raise Refusal("split PSLG vertex ceiling exceeded")
    holes=[(float(Polygon(r).representative_point().x),float(Polygon(r).representative_point().y)) for r in shape.interiors]
    return {"vertices":vs,"segments":ss,"segment_markers":mm,"holes":holes},originals
def _area2(a,b,c): return (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])
def _check_segment_chains(xy,tris,segs,marks,originals):
    if len(segs)!=len(marks): raise Refusal("marker length mismatch")
    for s in segs:
        if not isinstance(s,(list,tuple)) or len(s)!=2 or any(type(i) is not int or i<0 or i>=len(xy) for i in s) or s[0]==s[1]: raise Refusal("invalid or zero-length returned segment")
    if len({tuple(sorted(s)) for s in segs})!=len(segs): raise Refusal("duplicate returned segment")
    expected={m for _,_,m in originals};
    if set(marks)!=expected: raise Refusal("marker substitution")
    inc={}
    for f in tris:
        for u,v in zip(f,(*f[1:],f[0])): inc[tuple(sorted((u,v)))]=inc.get(tuple(sorted((u,v))),0)+1
    if {e for e,n in inc.items() if n==1}!={tuple(sorted(map(int,s))) for s in segs}: raise Refusal("boundary segment mismatch")
    allc=[c for p in xy for c in p]+[c for a,b,_ in originals for c in (*a,*b)]; max_param_tol=0.0
    for a,b,m in originals:
        dx,dy=b[0]-a[0],b[1]-a[1]; den=dx*dx+dy*dy
        if den<=0: raise Refusal("zero-length source segment")
        coord_tol=max(64*math.ulp(max(1.0,max(abs(x) for x in allc))),1e-12)
        if math.sqrt(den) <= 2*coord_tol: raise Refusal("source segment too short")
        tol=coord_tol/math.sqrt(den); max_param_tol=max(max_param_tol,tol); ints=[]
        for seg,got in zip(segs,marks):
            if got!=m: continue
            ts=[]
            for i in seg:
                x,y=xy[int(i)]; t=((x-a[0])*dx+(y-a[1])*dy)/den; cross=(x-a[0])*dy-(y-a[1])*dx
                if abs(cross)<=coord_tol*math.sqrt(den) and -tol<=t<=1+tol: ts.append(t)
            if len(ts)!=2: raise Refusal("non-collinear marked segment")
            ints.append((min(ts),max(ts)))
        cur=0.0
        for lo,hi in sorted(ints):
            if hi-lo <= tol: raise Refusal("zero-length marked interval")
            if lo>cur+tol or lo<cur-tol: raise Refusal("segment chain gap/overlap")
            cur=max(cur,hi)
        if abs(cur-1)>tol: raise Refusal("segment endpoint missing")
    return {"marker_count":len(expected),"marked_segment_count":len(segs),"coordinate_tolerance":coord_tol,"parameter_tolerance":max_param_tol}
def _canonical_mesh(vs,fs):
    clean=lambda x:0.0 if float(x)==0.0 else float(x); order=sorted(range(len(vs)),key=lambda i:tuple(clean(x) for x in vs[i])); remap={o:n for n,o in enumerate(order)}; out=[f"{PRODUCT} v{VERSION} D117 W0 geometry-only\n"]
    out += [f"v {clean(vs[i][0]):.17g} {clean(vs[i][1]):.17g} {clean(vs[i][2]):.17g}\n" for i in order]
    fs2=[]
    for f in fs:
        q=tuple(remap[i] for i in f); fs2.append(min(q[i:]+q[:i] for i in range(3)))
    out += [f"f {a} {b} {c}\n" for a,b,c in sorted(fs2)]; return "".join(out).encode("ascii")
def _certify_3d(vs,fs,area,t):
    if len(vs)>MAX_MESH_VERTICES or len(fs)>MAX_MESH_FACES: raise Refusal("mesh ceiling exceeded")
    if any(len(v)!=3 or any(not math.isfinite(float(x)) for x in v) for v in vs) or len({tuple(v) for v in vs})!=len(vs): raise Refusal("invalid 3D vertices")
    edges={}; seen=set(); terms=[]; mina=180.0; maxasp=0.0; origin=tuple(min(float(v[j]) for v in vs) for j in range(3))
    for f in fs:
        if len(f)!=3 or any(type(i)is not int or i<0 or i>=len(vs) for i in f): raise Refusal("invalid face")
        key=min(tuple(f[i:]+f[:i]) for i in range(3))
        if key in seen: raise Refusal("duplicate face")
        seen.add(key); a,b,c=(vs[i] for i in f); u=[b[j]-a[j] for j in range(3)]; v=[c[j]-a[j] for j in range(3)]; cr=(u[1]*v[2]-u[2]*v[1],u[2]*v[0]-u[0]*v[2],u[0]*v[1]-u[1]*v[0]); ar=math.sqrt(sum(x*x for x in cr))/2
        if ar<=0 or not math.isfinite(ar): raise Refusal("non-positive/nonfinite face area")
        ls=[math.dist(a,b),math.dist(b,c),math.dist(c,a)]
        if any(not math.isfinite(x) or x<=0 for x in ls): raise Refusal("nonfinite edge length")
        asp=max(ls)/(2*ar/max(ls)); maxasp=max(maxasp,asp)
        if not math.isfinite(asp): raise Refusal("nonfinite aspect")
        if asp>8: raise Refusal("aspect ratio exceeds 8")
        for x,y,z in ((a,b,c),(b,c,a),(c,a,b)):
            p,q,r=math.dist(x,y),math.dist(y,z),math.dist(z,x); angle=math.degrees(math.acos(max(-1,min(1,(p*p+r*r-q*q)/(2*p*r))))); 
            if not math.isfinite(angle): raise Refusal("nonfinite angle")
            mina=min(mina,angle)
        if mina<=7.5: raise Refusal("minimum angle gate failed")
        for x,y in zip(f,(*f[1:],f[0])): edges.setdefault(tuple(sorted((x,y))),[]).append((x,y))
        ar0=tuple(a[j]-origin[j] for j in range(3)); br0=tuple(b[j]-origin[j] for j in range(3)); cr0=tuple(c[j]-origin[j] for j in range(3)); term=(ar0[0]*(br0[1]*cr0[2]-br0[2]*cr0[1])-ar0[1]*(br0[0]*cr0[2]-br0[2]*cr0[0])+ar0[2]*(br0[0]*cr0[1]-br0[1]*cr0[0]))/6
        if not math.isfinite(term): raise Refusal("nonfinite volume term")
        terms.append(term)
    if len({i for e in edges for i in e})!=len(vs) or any(len(d)!=2 or d[0]!=(d[1][1],d[1][0]) for d in edges.values()): raise Refusal("topology incidence failure")
    vol=math.fsum(terms); exp=area*t; err=vol-exp; tol=max(1e-7,abs(exp)*1e-9)
    if not all(math.isfinite(x) for x in (vol,exp,err,tol)): raise Refusal("nonfinite volume certificate")
    if vol<=0 or abs(err)>tol: raise Refusal("volume gate failed")
    return {"vertex_count":len(vs),"face_count":len(fs),"edge_count":len(edges),"signed_volume":vol,"expected_volume":exp,"volume_error":err,"volume_tolerance":tol,"volume_origin_um":origin,"minimum_angle_deg":mina,"maximum_aspect_ratio":maxasp,"aspect_definition":"longest edge / shortest altitude","edge_incidence_all_two":True,"edge_incidence_all_two_opposite":True}
def mesh_polygon(shape,triangle_module,thickness=20.0):
    pslg,orig=build_pslg(shape,thickness)
    import numpy as np
    inp={"vertices":np.asarray(pslg["vertices"],float),"segments":np.asarray(pslg["segments"],np.int32),"segment_markers":np.asarray(pslg["segment_markers"],np.int32).reshape(-1,1)}
    if pslg["holes"]: inp["holes"]=np.asarray(pslg["holes"],float)
    try:r=triangle_module.triangulate(inp,TRIANGLE_OPTIONS)
    except Exception as e: raise Refusal(f"Triangle meshing failed: {e}") from e
    if not all(k in r for k in ("vertices","triangles","segments","segment_markers")): raise Refusal("incomplete Triangle result")
    try:
        rawv, rawt, raws, rawm = r["vertices"], r["triangles"], r["segments"], r["segment_markers"]
        if getattr(rawv,"ndim",None)!=2 or rawv.shape[1]!=2 or getattr(rawt,"ndim",None)!=2 or rawt.shape[1]!=3 or getattr(raws,"ndim",None)!=2 or raws.shape[1]!=2 or getattr(rawm,"ndim",None)!=2 or rawm.shape[1]!=1: raise Refusal("malformed Triangle array dimensions")
        if getattr(rawm,"ndim",1)==2 and rawm.shape[1]!=1: raise Refusal("malformed marker dimensions")
        if any(float(x)!=int(x) for row in rawt for x in row) or any(float(x)!=int(x) for row in raws for x in row) or any(float(x)!=int(x) for row in rawm for x in row): raise Refusal("non-integral Triangle index/marker")
        xy=[(float(p[0]),float(p[1])) for p in rawv]; ts=[(int(t[0]),int(t[1]),int(t[2])) for t in rawt]
    except Refusal: raise
    except Exception as e: raise Refusal("malformed Triangle result") from e
    if len(xy)>MAX_MESH_VERTICES or len(set(xy))!=len(xy) or any(not all(math.isfinite(x) for x in p) for p in xy): raise Refusal("invalid 2D mesh")
    if any(any(i<0 or i>=len(xy) for i in f) for f in ts) or len({i for f in ts for i in f})!=len(xy) or len({min(f[i:]+f[:i] for i in range(3)) for f in ts})!=len(ts) or any(_area2(xy[a],xy[b],xy[c])<=0 for a,b,c in ts): raise Refusal("invalid 2D faces")
    from shapely.geometry import Polygon; from shapely.ops import unary_union
    tri_areas=[abs(_area2(xy[a],xy[b],xy[c]))/2 for a,b,c in ts]
    if any(not math.isfinite(x) or x<=0 for x in tri_areas): raise Refusal("invalid triangle area")
    tri_area_sum=math.fsum(tri_areas); tri_area_error=tri_area_sum-shape.area; cov=unary_union([Polygon((xy[a],xy[b],xy[c])) for a,b,c in ts]); symdiff_area=float(cov.symmetric_difference(shape).area); tol=max(1e-7,shape.area*1e-12)
    if not math.isfinite(tri_area_error) or not math.isfinite(symdiff_area) or abs(tri_area_error)>tol or symdiff_area>tol: raise Refusal("2D coverage failure")
    bd={}
    for a,b,c in ts:
        for u,v in ((a,b),(b,c),(c,a)): bd.setdefault(tuple(sorted((u,v))),[]).append((u,v))
    boundary_count=sum(len(d)==1 for d in bd.values()); closed_estimate=2*len(ts)+2*boundary_count; closed_vertex_estimate=2*len(xy)
    if closed_vertex_estimate>MAX_MESH_VERTICES: raise Refusal("closed-vertex estimate exceeds cap")
    if any(len(d) not in (1,2) for d in bd.values()): raise Refusal("nonmanifold 2D edge")
    if closed_estimate>MAX_MESH_FACES: raise Refusal("closed-face estimate exceeds cap")
    chain=_check_segment_chains(xy,ts,r["segments"].tolist(),[int(x[0]) for x in r["segment_markers"]],orig); n=len(xy); vs=[(x,y,0.0) for x,y in xy]+[(x,y,float(thickness)) for x,y in xy]; fs=[]
    for a,b,c in ts: fs.extend(((c,b,a),(a+n,b+n,c+n)))
    for d in bd.values():
        if len(d)==1: a,b=d[0]; fs.extend(((a,b,b+n),(a,b+n,a+n)))
    cert=_certify_3d(vs,fs,float(shape.area),float(thickness)); euler=len(vs)-cert["edge_count"]+len(fs); exp_euler=2-2*len(pslg["holes"])
    if euler!=exp_euler: raise Refusal("Euler/genus failure")
    cert.update(chain); cert.update({"euler_characteristic":euler,"expected_genus":len(pslg["holes"]),"coverage_tolerance":tol,"boundary_side_outward_proof":"constructed right-hand boundary sides","top_bottom_opposite":True,"options":TRIANGLE_OPTIONS,"source_area_um2":float(shape.area),"triangle_area_sum_um2":tri_area_sum,"triangle_area_error_um2":tri_area_error,"symmetric_difference_area_um2":symdiff_area,"hole_count":len(pslg["holes"]),"holes_preserved":True,"two_d_vertex_count":len(xy),"two_d_triangle_count":len(ts),"original_pslg_vertex_count":sum(len(r.coords)-1 for r in [shape.exterior,*shape.interiors]),"original_pslg_segment_count":sum(len(r.coords)-1 for r in [shape.exterior,*shape.interiors]),"split_pslg_vertex_count":len(pslg["vertices"]),"split_pslg_segment_count":len(pslg["segments"]),"boundary_edge_count":boundary_count,"closed_face_estimate":closed_estimate,"closed_vertex_estimate":closed_vertex_estimate,"all_faces_triangles":True,"finite_vertices_and_faces":True,"nonzero_face_areas":True,"duplicate_2d_vertices":False,"duplicate_2d_faces":False,"duplicate_3d_vertices":False,"duplicate_3d_faces":False,"boundary_marker_chains_complete":True}); can=_canonical_mesh(vs,fs); cert.update({"canonical_sha256":hashlib.sha256(can).hexdigest(),"canonical_bytes":len(can),"geometry_only":True,"research_only":True,"non_oracle":True,"nonproduction":True,"solver_executed":False}); return {"vertices":vs,"faces":fs,"canonical":can,"certificate":cert}
def _load_json(p):
    try:return json.loads(p.read_text(encoding="utf-8"))
    except (OSError,UnicodeError,json.JSONDecodeError) as e: raise Refusal("invalid receipt") from e
def _validate_inputs(a):
    d115c=Path(a.d115c).resolve(strict=True); d103=Path(a.d103).resolve(strict=True); d104=Path(a.d104_root).resolve(strict=True)/"geometry_receipt.json"
    if _sha(d115c)!=(EXPECTED_D115C_SIZE,EXPECTED_D115C_SHA) or _sha(d103)!=(EXPECTED_D103_SIZE,EXPECTED_D103_SHA) or _sha(d104)!=(EXPECTED_D104_SIZE,EXPECTED_D104_SHA): raise Refusal("sealed receipt identity mismatch")
    j=_load_json(d115c); j3=_load_json(d103); j4=_load_json(d104)
    if j.get("status")!=EXPECTED_STOP or j.get("code")!=EXPECTED_STOP: raise Refusal("D115C status mismatch")
    linked=j.get("inputs",{}); l3=linked.get("d103",{}); l4=linked.get("d104",{})
    root_ref=linked.get("d104_root",{})
    if l3.get("sha256")!=EXPECTED_D103_SHA or l3.get("size_bytes")!=EXPECTED_D103_SIZE or l4.get("sha256")!=EXPECTED_D104_SHA or l4.get("size_bytes")!=EXPECTED_D104_SIZE or Path(str(l3.get("path",""))).resolve()!=d103 or Path(str(l4.get("path",""))).resolve()!=d104 or not isinstance(root_ref,dict) or Path(str(root_ref.get("path",""))).resolve()!=d104.parent: raise Refusal("linked receipt mismatch")
    rows=[x for x in j.get("d104_cells",[]) if x.get("ordinal")==264]
    row=rows[0] if len(rows)==1 else None; inter=row.get("intersection",{}) if row else {}; src115=row.get("source_wkb",{}) if row else {}
    if j.get("w0",{}).get("snapped_bounds_pm") != EXPECTED_SNAPPED_PM: raise Refusal("D115C snapped bounds mismatch")
    if not row or (row.get("layer"),row.get("net"),row.get("island_id"))!=(EXPECTED_LAYER,EXPECTED_NET,EXPECTED_ISLAND) or src115.get("filename")!="cell_0264.wkb" or src115.get("size_bytes")!=EXPECTED_SOURCE_SIZE or src115.get("sha256")!=EXPECTED_SOURCE_SHA or src115.get("wkb_size_bytes")!=EXPECTED_SOURCE_SIZE or src115.get("wkb_sha256")!=EXPECTED_SOURCE_SHA or row.get("intersection_bbox_um")!=EXPECTED_BOUNDS or inter.get("bbox_um")!=EXPECTED_BOUNDS or row.get("intersection_wkb_sha256")!=EXPECTED_INTERSECTION_SHA or inter.get("wkb_sha256")!=EXPECTED_INTERSECTION_SHA or row.get("intersection_wkb_size_bytes")!=EXPECTED_INTERSECTION_SIZE or inter.get("wkb_size_bytes")!=EXPECTED_INTERSECTION_SIZE or row.get("boundary_contact") is not True or inter.get("boundary_contact") is not True or inter.get("nonempty") is not True or row.get("intersection_area_um2")!=EXPECTED_AREA or inter.get("area_um2")!=EXPECTED_AREA: raise Refusal("sealed W0 fields mismatch")
    cells=[x for x in j4.get("cells",[]) if x.get("ordinal")==264]
    cell=cells[0] if len(cells)==1 else None; sw=cell.get("geometry",{}) if cell else {}; source=d104.parent/sw.get("filename","")
    if not cell or cell.get("layer")!=EXPECTED_LAYER or cell.get("net")!=EXPECTED_NET or cell.get("island_id")!=EXPECTED_ISLAND or sw.get("filename")!="cell_0264.wkb" or sw.get("wkb_size_bytes")!=EXPECTED_SOURCE_SIZE or sw.get("wkb_sha256")!=EXPECTED_SOURCE_SHA or _sha(source)!=(EXPECTED_SOURCE_SIZE,EXPECTED_SOURCE_SHA): raise Refusal("D104 source identity mismatch")
    if j4.get("status")!="PASS": raise Refusal("D104 status mismatch")
    l30=[x for x in j3.get("stackup_layers",[]) if x.get("layer_name")==EXPECTED_LAYER]
    if j3.get("status")!="PASS" or len(l30)!=1: raise Refusal("D103 stackup identity mismatch")
    layer=l30[0]; t=layer.get("thickness_um")
    if (layer.get("ordinal"),layer.get("raw_layer_ordinal"),layer.get("layer_kind"),layer.get("thickness_origin"))!=(58,58,"conductor","source"): raise Refusal("D103 L30 layer identity mismatch")
    if t!=20.0 or not math.isfinite(float(t)) or float(t)<=0: raise Refusal("D103 L30 thickness mismatch")
    from shapely import wkb; from shapely.geometry import box
    try:s=wkb.loads(source.read_bytes()); i=s.intersection(box(*EXPECTED_BOUNDS)); b=wkb.dumps(i)
    except Exception as e: raise Refusal("intersection derivation failed") from e
    if len(b)!=EXPECTED_INTERSECTION_SIZE or hashlib.sha256(b).hexdigest()!=EXPECTED_INTERSECTION_SHA or not math.isclose(i.area,EXPECTED_AREA,rel_tol=0,abs_tol=1e-6): raise Refusal("derived intersection mismatch")
    return i,t,{"d115c":{"path":str(d115c),"size_bytes":EXPECTED_D115C_SIZE,"sha256":EXPECTED_D115C_SHA},"d103":{"path":str(d103),"size_bytes":EXPECTED_D103_SIZE,"sha256":EXPECTED_D103_SHA},"d104":{"path":str(d104),"size_bytes":EXPECTED_D104_SIZE,"sha256":EXPECTED_D104_SHA,"root":str(d104.parent)},"source":{"path":str(source),"filename":"cell_0264.wkb","size_bytes":EXPECTED_SOURCE_SIZE,"sha256":EXPECTED_SOURCE_SHA},"intersection":{"size_bytes":EXPECTED_INTERSECTION_SIZE,"sha256":EXPECTED_INTERSECTION_SHA,"bbox_um":EXPECTED_BOUNDS,"area_um2":EXPECTED_AREA,"boundary_contact":True},"ordinal":264,"layer":EXPECTED_LAYER,"net":EXPECTED_NET,"island":EXPECTED_ISLAND,"snapped_bounds_pm":EXPECTED_SNAPPED_PM}
def _make_payload(inputs,cert,runtime):
    return {"program":PRODUCT,"version":VERSION,"status":"PASS_D117_TRIANGLE_W0_RESEARCH_PILOT","d115c_overall_status":EXPECTED_STOP,"geometry_only":True,"research_only":True,"non_oracle":True,"nonproduction":True,"solver_executed":False,"stage0_success_does_not_authorize_stage1":True,"supply_chain":{"metadata_url":PYPI_METADATA_URL,"release_url":TRIANGLE_RELEASE_URL,"wheel_url":WHEEL_URL,"wheel_filename":WHEEL_NAME,"wheel_size":WHEEL_SIZE,"wheel_sha256":WHEEL_SHA256,"wheel_tag":"cp312-cp312-win_amd64","artifact_local":True,"install_mode":"--no-deps --no-index --no-cache-dir --target artifact-root/site","shipped_dependency":False},"license_boundary":{"wrapper":"LGPL-3.0 metadata","wrapper_license_url":WRAPPER_LICENSE_URL,"upstream_notice_url":UPSTREAM_LICENSE_URL,"upstream_terms":"restrictive private/research/institutional use versus commercial distribution by direct arrangement","scope":"research-only external tool; non-shipped","shipped_dependency":False,"legal_review_required":True,"not_legal_advice":True},"inputs":inputs,"runtime":runtime,"ceilings":{"max_original_vertices":MAX_ORIGINAL_VERTICES,"max_source_vertices":MAX_SOURCE_VERTICES,"max_pslg_vertices":MAX_PSLG_VERTICES,"max_mesh_vertices":MAX_MESH_VERTICES,"max_mesh_faces":MAX_MESH_FACES,"scope":"actual D115C W0-clipped ordinal264","full_domain":False},"quality_gates":{"options":TRIANGLE_OPTIONS,"minimum_angle_strictly_greater_than_deg":7.5,"aspect_max":8.0,"aspect_definition":"longest edge / shortest altitude","coverage":"exact polygon symmetric-difference and area sum","holes_preserved":True,"manifold_closed":True,"euler_genus_proven":True,"signed_volume_proven":True,"deterministic_canonicalization":True,"three_serial_replays_required":True},"certificate":cert}
def main(argv:Sequence[str]|None=None):
    p=argparse.ArgumentParser(description=f"{PRODUCT} v{VERSION} Stage-I W0 geometry-only research pilot"); p.add_argument("--version",action="version",version=f"{PRODUCT} v{VERSION}"); [p.add_argument(x,required=True) for x in ("--d115c","--d103","--d104-root","--triangle-site","--output")]; a=p.parse_args(argv)
    try:
        site=Path(a.triangle_site).resolve(strict=True); out=Path(a.output).resolve(strict=False)
        if site!=AUTHORIZED_SITE.resolve(strict=True) or out.exists() or not _inside(out,AUTHORIZED_ARTIFACT_ROOT): raise Refusal("site/output confinement failed")
        shape,t,inputs=_validate_inputs(a); tri=load_triangle_site(site); result=mesh_polygon(shape,tri,t)
        import platform
        impl=platform.python_implementation(); machine=platform.machine().upper(); tri_version=getattr(tri,"__version__",None)
        if impl!="CPython" or sys.version_info[:2]!=(3,12) or machine!="AMD64" or tri_version!="20250106": raise Refusal("runtime compatibility mismatch")
        runtime={"implementation":impl,"python":sys.version.split()[0],"platform":sys.platform,"machine":machine,"numpy":importlib.metadata.version("numpy"),"shapely":importlib.metadata.version("shapely"),"triangle":tri_version}; payload=_make_payload(inputs,result["certificate"],runtime); out.mkdir(parents=True); (out/"mesh.canonical.txt").write_bytes(result["canonical"]); (out/"mesh_receipt.json").write_text(json.dumps(payload,sort_keys=True,separators=(",",":"),ensure_ascii=True)+"\n",encoding="ascii"); print(f"{PRODUCT} v{VERSION} PASS_D117_TRIANGLE_W0_RESEARCH_PILOT; d115c_overall_status={EXPECTED_STOP}"); return 0
    except (Refusal,OSError) as e: print(f"{PRODUCT} v{VERSION} STOP: {e}",file=sys.stderr); return 2
if __name__=="__main__": raise SystemExit(main())
