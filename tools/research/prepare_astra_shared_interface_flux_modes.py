"""SPD Decap PI Evaluator v0.23.1: common RT0 normal-flux interface coordinates."""
from hashlib import sha256
import json
from pathlib import Path
import numpy as np
from qualify_astra_source_joint_self_green import ROOT, PINS


def main():
    output = ROOT/'outputs/research/astra-shared-interface-flux-modes-01'
    output.mkdir()
    interface = ROOT/'outputs/research/astra-joint-overlap-current-conformity-02/interface-jumps.npz'
    assert sha256(interface.read_bytes()).hexdigest() == '46bc120bd8ed776f0a1e1b033a4b2edab930c86a54ce6e661490f8faae0f28de'
    mesh = ROOT/list(PINS)[2]
    assert sha256(mesh.read_bytes()).hexdigest() == PINS[list(PINS)[2]]
    with np.load(interface, allow_pickle=False) as d:
        faces = d['local_template_interface_face_ids']
        future_faces = d['future_shared_face_ids']
    with np.load(mesh, allow_pickle=False) as d:
        triangles = d['vertices_local_um'][d['face_vertices'][faces]]*1e-6
    areas = np.linalg.norm(np.cross(triangles[:, 1]-triangles[:, 0], triangles[:, 2]-triangles[:, 0]), axis=1)/2
    weights = areas/areas.sum()
    center = weights @ triangles.mean(axis=1)
    spans = np.ptp(triangles.reshape(-1, 3), axis=0)
    y = (triangles[:, :, 1]-center[1])/spans[1]
    z = (triangles[:, :, 2]-center[2])/spans[2]
    # Exact triangular averages of quadratic normal-flux densities, then P0 traces.
    raw = np.column_stack((np.ones(len(faces)), y.mean(axis=1), z.mean(axis=1),
        ((y*y).sum(axis=1)+y.sum(axis=1)**2)/12,
        ((y*z).sum(axis=1)+y.sum(axis=1)*z.sum(axis=1))/12,
        ((z*z).sum(axis=1)+z.sum(axis=1)**2)/12))
    zero_mean = raw[:, 1:]-(weights @ raw[:, 1:])[None]
    u, singular, _ = np.linalg.svd(np.sqrt(weights)[:, None]*zero_mean, full_matrices=False)
    retained = singular > singular.max()*1e-12
    density = np.column_stack((np.ones(len(faces)), u[:, retained]/np.sqrt(weights)[:, None]))
    flux = weights[:, None]*density
    gram = flux.T @ (flux/weights[:, None])
    net_flux = flux.sum(axis=0)
    assert np.max(abs(gram-np.eye(len(net_flux)))) < 1e-12
    assert abs(net_flux[0]-1) < 1e-12 and np.max(abs(net_flux[1:])) < 1e-12
    post_outward, bridge_outward = flux, -flux
    assert np.count_nonzero(post_outward+bridge_outward) == 0
    artifact = output/'shared-flux-modes.npz'
    np.savez_compressed(artifact, template_interface_face_ids=faces, translated_future_interface_face_ids=future_faces,
        triangles_m=triangles, face_areas_m2=areas, relative_area_weights=weights,
        exact_p0_polynomial_averages=raw, centered_polynomial_singular_values=singular,
        shared_integrated_flux_modes=flux, post_local_outward_flux=post_outward,
        bridge_local_outward_flux=bridge_outward, net_flux_per_mode=net_flux)
    result = dict(program='SPD Decap PI Evaluator', version='0.23.1',
        status='ACCEPT_SHARED_INTERFACE_FLUX_COORDINATES_ONLY',
        driver_sha256=sha256(Path(__file__).read_bytes()).hexdigest(), mesh_sha256=PINS[list(PINS)[2]],
        interface_artifact_sha256=sha256(interface.read_bytes()).hexdigest(), artifact_sha256=sha256(artifact.read_bytes()).hexdigest(),
        interface_faces=len(faces), trace_modes=flux.shape[1], candidate_polynomials=6,
        centered_singular_values=singular.tolist(), area_weighted_orthonormality_error=float(abs(gram-np.eye(flux.shape[1])).max()),
        net_flux_per_mode=net_flux.tolist(), opposite_owner_jump=0.,
        scope='Same32 source-conforming interface triangles on both material owners. Exact triangle '
        'averages of1,y,z,y²,yz,z² generate initial P0 normal-flux modes. Weighted SVD removes '
        'dependent projected traces; coefficients shared with opposite outward signs ensure '
        'pointwise RT0 normal continuity. The unit-net-flux mode still needs balancing terminal '
        'or other-interface flux in each conductor extension. These are interface coordinates '
        'only, not interior energy lifts, complete global reduced basis, charge/contact conditions '
        'or proof that the current thickness mesh resolves100MHz skin current.')
    (output/'result.json').write_text(json.dumps(result, indent=2, allow_nan=False), encoding='utf-8')
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
