"""Assemble a no-solve lossless L02 full-face hybrid R/G/C operator pack."""
from __future__ import annotations
import argparse, hashlib, json, time
from pathlib import Path
import numpy as np
from scipy import sparse

ROOT=Path(__file__).resolve().parents[2]; R=ROOT/'outputs/research'
PROGRAM='SPD Decap PI Evaluator'; VERSION='0.23.1'
NATIVE,L14,L25=756889,903945,1483296; TARGET=349710; CONTACTS=38856; NFREE=1583840
NBRANCH=3095567; NRIM=621696; NCURRENT=604031; POTENTIAL=3996022; FREQ=1.0e6
PINS={
'boundary_result':(R/'astra-l02-rt0-boundary-continuations-04/result.json','1143d87c7423c74cfb422a6280e93590ef3e87f11e0028ebb5eb2984b952ce80'),
'boundary':(R/'astra-l02-rt0-boundary-continuations-04/boundary-continuation-map.npz','bee3df63c4178620aa330c19e06c4d8351d7bd2531a7d724f33abdddeb0cb24e'),
'hybrid_result':(R/'astra-l02-hybrid-cell-geometry-02/result.json','2f1a30f604f86d04791270b627e4009b5024781b1d95ba2ee7711e23e4896799'),
'hybrid':(R/'astra-l02-hybrid-cell-geometry-02/hybrid-cell-geometry.npz','15f1f6f6ad0ffd45ada9a74bde80d0e79ce82517a3ab485095d57898fbd06834'),
'space':(R/'astra-l02-full-rt0-current-space-01/l02-rt0-current-space.npz','7cd77ee8b03fd0226596748a1913ee5a913381617498c9927ef12d8b080bc98f'),
'gc':(R/'astra-l02-cell-gc-loads-01/l02-cell-gc-loads.npz','12ab38f5d692c442a18d6ec7df913c6d96108be73850f27c7014f6f26c9c2459'),
'map':(R/'astra-l02-l14-l25-combined-assembly-map-02/combined-assembly-map.npz','a6ccff514fe788396eb9d7620ebc828f966efbea3f7899e7a73991c17f8a2469'),
'operator':(R/'astra-l02-l14-l25-combined-operator-02/combined-operator.npz','45cc79706849631e9c33dc2f0d0e3c7910fe7dcce817585c728f6f1e3c30a1e8'),
'categories':(R/'astra-combined-source-categories-01/combined-source-categories.npz','f9253786da9eb88367c6bde51e78c64b647528e2856358b1fdd204ade1db6e9f'),
'circuit_result':(R/'astra-l02-circuit-contact-binding-01/result.json','1ef00f9c88553faf71f6cd87b4899c23d4a1be598a18a08c19b8776148690efc'),
'circuit':(R/'astra-l02-circuit-contact-binding-01/circuit-contact-binding.npz','61ed51f68ae0cc39cdd5ee07e919e105fd15aee5a6b4c8374b063f7d72221020')}
def sha(p):
 with p.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def req(c,m):
 if not c:raise ValueError(m)
def rec(p):return {'path':str(p),'sha256':sha(p),'size_bytes':p.stat().st_size}
def put(a,p,m):
 m=m.tocsc();a[p+'_data']=m.data;a[p+'_indices']=m.indices;a[p+'_indptr']=m.indptr;a[p+'_shape']=np.asarray(m.shape,np.int64)
def get(a,p):return sparse.csc_matrix((a[p+'_data'],a[p+'_indices'],a[p+'_indptr']),shape=tuple(a[p+'_shape']))
def dump(path,**a):
 t=path.with_suffix('.tmp')
 with t.open('xb') as f:np.savez_compressed(f,**a)
 t.replace(path)
def js(path,x):
 t=path.with_suffix('.tmp');t.write_text(json.dumps(x,indent=2,sort_keys=True,allow_nan=False)+'\n');t.replace(path)
def self_check():
 h=np.array([[2.,-1.,-1.],[-1.,2.,-1.],[-1.,-1.,2.]])
 rho=3.; q=h+np.ones((3,3))/(9*rho); a=np.full(3,1/(3*rho));g=np.array([.1+.2j,.3+.4j]);w=np.r_[a,g];b=np.zeros((5,5),complex);b[:3,:3]=q;b[3:,3:]=np.diag(g);b-=np.outer(w,w)/(1/rho+g.sum());req(np.max(abs(b-b.T))<1e-14,'hybrid Schur selfcheck')
def run(out):
 t=time.perf_counter();req(not out.exists(),'output exists');out.mkdir(parents=True);(out/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
 for n,(p,h) in PINS.items():req(sha(p)==h,n+' hash')
 with np.load(PINS['space'][0],allow_pickle=False) as z:
  rim=np.asarray(z['electrode_rim_branch_indices']);rim_contact=np.asarray(z['electrode_rim_contact_index']); facets=np.asarray(z['local_facet_branch_index']); free=np.asarray(z['free_triangle_indices']); tri_p=np.asarray(z['triangle_potential_index']); tri_c=np.asarray(z['triangle_contact_index'])
 trace=np.flatnonzero(~np.isin(np.arange(NBRANCH),rim));req(len(trace)==NBRANCH-NRIM==2473871,'nonrim count')
 contact_global=np.r_[TARGET,np.arange(L25,L25+CONTACTS-1)];trace_global=np.full(NBRANCH,-1,np.int64);trace_global[trace]=L25+CONTACTS-1+np.arange(len(trace));rim_global=np.full(NBRANCH,-1,np.int64);rim_global[rim]=contact_global[rim_contact];local_terminal=trace_global[facets];missing=local_terminal<0;local_terminal[missing]=rim_global[facets[missing]];occ=np.bincount(facets.ravel(),minlength=NBRANCH);req(POTENTIAL==L25+CONTACTS-1+len(trace) and not np.any(local_terminal<0) and int(occ[trace].sum())==2*1655953+817918 and int(occ[rim].sum())==621696 and int(occ.sum())==4751520,'full-face terminal occurrence identity')
 with np.load(PINS['boundary'][0],allow_pickle=False) as z:
  ext=np.asarray(z['exterior_branch_index']);cls=np.asarray(z['boundary_class_code']);req(np.all(trace_global[ext]>=0) and len(ext)==817918 and len(cls)==len(ext),'boundary trace map')
 with np.load(PINS['hybrid'][0],allow_pickle=False) as z:
  hup=np.asarray(z['dc_trace_h_upper_s']);rho=np.asarray(z['unit_divergence_resistance_ohm']);w=np.asarray(z['unit_divergence_flux_weights']);req(hup.shape==(NFREE,6) and rho.shape==(NFREE,) and np.array_equal(z['local_facet_branch_index'],facets) and np.all(w==1/3),'hybrid cells')
 with np.load(PINS['gc'][0],allow_pickle=False) as z:
  ar=sparse.csr_matrix((z['owner_cell_area_um2_data'],z['owner_cell_area_um2_indices'],z['owner_cell_area_um2_indptr']),shape=tuple(z['owner_cell_area_um2_shape'])); dens=np.asarray(z['owner_density_f_per_um2']); extnode=np.asarray(z['owner_external_active_indices']);scale=complex(z['gc_frequency_scale_s'][0]);req(np.array_equal(z['free_triangle_indices'],free) and np.array_equal(z['triangle_potential_index'],tri_p) and np.array_equal(z['triangle_contact_index'],tri_c),'gc topology')
 # owner-cell area becomes voltage-dependent P0 terminal admittance; no P1 load is read.
 co=ar.tocoo(); cell_to_free=np.full(len(tri_p),-1,np.int64);cell_to_free[free]=np.arange(NFREE); fr=cell_to_free[co.col];g=co.data*dens[co.row]*scale
 keep=fr>=0; order=np.argsort(fr[keep],kind='stable');fr,own,g=fr[keep][order],co.row[keep][order],g[keep][order];cnt=np.bincount(fr,minlength=NFREE);req(int(cnt.max())==4 and np.array_equal(np.bincount(cnt,minlength=5),np.asarray([981692,447201,152160,2672,115])),'fixed free-cell owner histogram')
 ptr=np.r_[0,np.cumsum(cnt)]; padded_owner=np.full((NFREE,5),-1,np.int32);padded_g=np.zeros((NFREE,5),np.complex128)
 for k in range(5):
  rows=np.flatnonzero(cnt>k); idx=ptr[rows]+k;padded_owner[rows,k]=own[idx];padded_g[rows,k]=g[idx]
 contact=tri_c[co.col]>=0;cfirst=contact_global[tri_c[co.col[contact]]];csecond=extnode[co.row[contact]];cval=(co.data[contact]*dens[co.row[contact]]*scale).astype(np.complex128)
 req(np.all(np.isin(cfirst,contact_global)) and np.all(csecond<L25),'contact gc endpoints')
 with np.load(PINS['map'][0],allow_pickle=False) as z:
  ff=np.asarray(z['final_finite_first_active_index']);fs=np.asarray(z['final_finite_second_active_index']);fy=np.asarray(z['final_finite_admittance_s']);fnative=np.asarray(z['final_finite_native_active_row']);fsplit=np.asarray(z['final_finite_split_leg']);req(np.all((ff<L25)|( (ff>=L25)&(ff<L25+CONTACTS-1))) and np.all((fs<L25)|((fs>=L25)&(fs<L25+CONTACTS-1))),'finite depends retired l02 coordinate')
 with np.load(PINS['operator'][0],allow_pickle=False) as z:r=get(z,'r');b=get(z,'b');req(r.shape==(NCURRENT,NCURRENT) and b.shape==(2340069,NCURRENT),'combined L25 R/B');b=b[:L25,:].tocsc()
 with np.load(PINS['circuit'][0],allow_pickle=False) as z:
  native_contact=np.asarray(z['native_contact_ordinal']);support=np.asarray(z['contact_support_index']);junctions=json.loads(np.asarray(z['junctions_json_utf8'],np.uint8).tobytes());missing_native=set(range(CONTACTS))-set(native_contact.tolist());req(len(support)==CONTACTS and len(np.unique(native_contact))==38836 and len(missing_native)==20 and len(junctions)==20,'circuit binding contact ownership');req(np.count_nonzero((np.asarray(z['resistance_ohm'])>0)&(np.asarray(z['inductance_h'])>0))==76139,'positive RL circuit legs')
 with PINS['circuit_result'][0].open() as f: circuit_result=json.load(f)
 req(len(circuit_result['excluded_leaves'])==2 and circuit_result['junction_count']==20,'circuit composite/leaf policy')
 req(np.count_nonzero(fsplit==-1)==1692369 and np.count_nonzero(fsplit==0)==20 and np.count_nonzero(fsplit==1)==20,'final split row counts')
 for j in junctions:
  q=int(j['contact_ordinal']);rid=int(j['replaced_active_finite_index']);rows=np.flatnonzero(fnative==rid);req(len(rows)==2 and set(fsplit[rows].tolist())=={0,1},'junction has exactly two split rows');r0=rows[fsplit[rows]==0][0];r1=rows[fsplit[rows]==1][0];mid=contact_global[q];outer=j['original_active_endpoints'];req(ff[r0]==outer[0] and fs[r0]==mid and ff[r1]==mid and fs[r1]==outer[1],'junction endpoint orientation');expect=np.asarray([1/(j['first_leg_resistance_ohm']+2j*np.pi*FREQ*j['first_leg_inductance_h']),1/(j['second_leg_resistance_ohm']+2j*np.pi*FREQ*j['second_leg_inductance_h'])]);req(np.max(abs(np.asarray([fy[r0],fy[r1]])-expect))<1e-12*max(1.,np.max(abs(expect))),'junction RL admittance')
 leaf_ord=[]
 for leaf in circuit_result['excluded_leaves']:
  hit=np.flatnonzero(support==int(leaf['drill_support_index']));req(len(hit)==1 and int(hit[0]) in set(native_contact.tolist()),'retained leaf shares native contact');leaf_ord.append(int(hit[0]))
 req(contact_global[0]==TARGET and len(np.unique(contact_global))==CONTACTS,'contact ordinal zero target and no duplicate')
 arrays={};put(arrays,'l25_r',r);put(arrays,'l25_b',b);arrays.update(potential_count=np.asarray([POTENTIAL]),mixed_count=np.asarray([POTENTIAL+NCURRENT]),contact_global_active_index=contact_global,trace_branch_index=trace,trace_global_active_index=trace_global,exterior_trace_global_active_index=trace_global[ext],electrode_rim_branch_index=rim,electrode_rim_contact_index=rim_contact,local_facet_terminal_global_index=local_terminal,hybrid_h_upper_s=hup,hybrid_rho_ohm=rho,hybrid_w=w,owner_external_active_indices=extnode,free_cell_owner_count=cnt,free_cell_owner_ordinal=padded_owner,free_cell_owner_external_active_index=np.where(padded_owner>=0,extnode[np.maximum(padded_owner,0)],-1),free_cell_gc_admittance_s=padded_g,contact_gc_first_active_index=cfirst,contact_gc_second_active_index=csecond,contact_gc_admittance_s=cval,finite_first_active_index=ff,finite_second_active_index=fs,finite_admittance_s=fy,final_finite_native_active_row=fnative,final_finite_split_leg=fsplit,exterior_branch_index=ext,exterior_boundary_class=cls,circuit_contact_support_index=support,circuit_native_contact_ordinal=native_contact,circuit_retained_leaf_contact_ordinal=np.asarray(leaf_ord,np.int64))
 # Preserve exactly the five non-L02 source CSC components; require their retired range empty.
 with np.load(PINS['categories'][0],allow_pickle=False) as z:
  names=json.loads(np.asarray(z['category_names_json_utf8'],np.uint8).tobytes());keepn=['retained_gc','termination','l14_sheet_dc','l14_distributed_gc','l25_distributed_gc']
  req(set(names)-set(keepn)=={'l02_distributed_gc','l02_sheet_dc'},'category set')
  for n in keepn:
   m=get(z,n);req(m.shape==(2340069,2340069) and m[L25:,:].nnz==0 and m[:,L25:].nnz==0,'category retired l02 dependence '+n);put(arrays,'category_'+n,m[:L25,:L25])
 arrays['category_names_json_utf8']=np.frombuffer(json.dumps(keepn).encode(),np.uint8);arrays['input_hashes_json_utf8']=np.frombuffer(json.dumps({n:h for n,(_,h) in PINS.items()},sort_keys=True).encode(),np.uint8)
 artifact=out/'hybrid-operator-pack.npz';dump(artifact,**arrays)
 result={'program':PROGRAM,'version':VERSION,'status':'PASS_NO_SOLVE_L02_FULL_FACE_HYBRID_OPERATOR_PACK','inputs':{n:rec(p) for n,(p,_) in PINS.items()},'driver':rec(out/'driver-at-run.py'),'output':rec(artifact),'counts':{'potential_count':POTENTIAL,'mixed_count':POTENTIAL+NCURRENT,'l25_current_count':NCURRENT,'nonrim_trace_potentials':len(trace),'electrode_rim_occurrences':int(occ[rim].sum()),'exterior_boundary_ports':len(ext),'contact_count':CONTACTS,'free_cells':NFREE,'free_cell_owner_histogram':[981692,447201,152160,2672,115],'contact_gc_stamps':len(cval),'finite_rows':len(fy),'split_composite_legs':40,'retained_shared_native_leaves':2},'assertions':{'all_exterior_ports_retained':True,'full_face_terminal_map_no_negative':True,'full_face_occurrence_identity':True,'circuit_binding_20_composites_to_40_verified_positive_RL_legs':True,'circuit_binding_two_shared_native_leaves_retained':True,'contact_ordinal_zero_is_target_no_duplicates':True,'no_q0_or_solve':True,'finite_once_owned_retired_l02_free':True,'non_l02_categories_retired_l02_free':True,'p0_gc_uses_area_density_frequency_scale':True,'hybrid_owner_max_exactly_4':True},'scope':'Lossless no-solve component pack. Every full-face facet maps to a terminal coordinate: nonrim facets to retained trace potentials and rim facets to declared contact potentials. P0 G/C, cell H/rho/w, L25 R/B, and five non-L02 categories are retained. The pinned circuit binding keeps two shared-native excluded leaves without new branches and replaces 40 excluded owners by 20 composites split into 40 verified positive-RL legs. No global Y/KCL closure, q=0 condition, factorization, Green/FMM or solvability claim.','elapsed_s':time.perf_counter()-t};js(out/'result.json',result);return result
def main():
 p=argparse.ArgumentParser();p.add_argument('--output',type=Path,default=R/'astra-l02-full-face-hybrid-operator-01');p.add_argument('--self-check',action='store_true');a=p.parse_args();self_check();
 if a.self_check:print('PASS');return
 x=run(a.output.resolve());print(json.dumps({'status':x['status'],'output':x['output'],'elapsed_s':x['elapsed_s']}))
if __name__=='__main__':main()
