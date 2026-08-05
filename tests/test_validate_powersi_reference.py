import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from hashlib import sha256
import pytest
SCRIPT=Path(__file__).parents[1]/'scripts'/'validate_powersi_reference.py'
spec=importlib.util.spec_from_file_location('validate_powersi_reference',SCRIPT); module=importlib.util.module_from_spec(spec); assert spec.loader; spec.loader.exec_module(module)
def test_cli_argument_parsing_and_mapping(tmp_path):
 args=module.parse_args(['--scenario','a.spdpi','--touchstone','r.s24p','--rail-port','R=5','--modal-max-index','12','--output',str(tmp_path/'x.json')]); assert args.modal_max_index==12 and module.rail_ports(args.rail_port)=={'R':5}
def test_cli_mapping_rejects_invalid():
 with pytest.raises(ValueError): module.rail_ports(['broken'])

def test_cli_mapping_rejects_duplicate_touchstone_port():
 with pytest.raises(ValueError): module.rail_ports(['A=1','B=1'])

def test_coupling_pair_rejects_rail_outside_manifest():
 with pytest.raises(ValueError, match='--rail-port'):
  module.coupling_pairs(['A=B'], {'A': 1})

def test_explicit_port_label_must_be_selected_and_unique():
 assert module.port_labels(['A=custom header'], {'A': 1}) == {'A': 'custom header'}
 with pytest.raises(ValueError, match='--rail-port'):
  module.port_labels(['B=custom header'], {'A': 1})

def test_explicit_port_label_fails_closed_when_touchstone_header_is_absent(monkeypatch,tmp_path):
 network=SimpleNamespace(s_parameters=module.np.zeros((1,1,1)),port_mapping={})
 monkeypatch.setattr(module,'read_touchstone',lambda _path: network)
 monkeypatch.setattr(module,'s_to_z',lambda _network: pytest.fail('must fail before S-to-Z'))
 with pytest.raises(ValueError,match='requires Touchstone'):
  module.main(['--scenario','missing.spdpi','--touchstone','headerless.s1p','--rail-port','R=1','--port-label','R=custom header','--output',str(tmp_path/'out.json')])

def test_comparison_rejects_missing_band_or_reference_endpoint_coverage():
 f=module.np.asarray([1e5,1e6,1e7,1e8]); z=module.np.ones(4,dtype=complex)
 with pytest.raises(ValueError): module.comparison_metrics(f,z,module.np.asarray([2e5,1e8]),module.np.ones(2,dtype=complex))
 with pytest.raises(ValueError): module.comparison_metrics(module.np.asarray([1e5,1e6]),z[:2],f,z)

def test_comparison_wraps_phase_error_at_plus_minus_180_degrees():
 f=module.np.asarray([1e5,1e6,1e7,1e8])
 reference=module.np.exp(1j*module.np.deg2rad(179.0))*module.np.ones_like(f,dtype=complex)
 model=module.np.exp(1j*module.np.deg2rad(-179.0))*module.np.ones_like(f,dtype=complex)
 metrics=module.comparison_metrics(f,model,f,reference)
 assert metrics['overall']['phase_rms_deg']==pytest.approx(2.0)
 assert metrics['overall']['phase_max_abs_deg']==pytest.approx(2.0)

def test_fixed_log_quadrature_is_stable_when_raw_sampling_density_changes():
 sparse=module.np.asarray([1e5,1e6,1e7,1e8])
 dense=module.np.geomspace(1e5,1e8,1001)
 reference_sparse=(1.0+1.0j)*module.np.ones_like(sparse,dtype=complex)
 model_sparse=(1.1+0.9j)*module.np.ones_like(sparse,dtype=complex)
 reference_dense=(1.0+1.0j)*module.np.ones_like(dense,dtype=complex)
 model_dense=(1.1+0.9j)*module.np.ones_like(dense,dtype=complex)
 sparse_metrics=module.comparison_metrics(sparse,model_sparse,sparse,reference_sparse)
 dense_metrics=module.comparison_metrics(dense,model_dense,dense,reference_dense)
 assert dense_metrics['overall']['complex_rms_uohm']==pytest.approx(sparse_metrics['overall']['complex_rms_uohm'])
 assert dense_metrics['overall']['rms_db']==pytest.approx(sparse_metrics['overall']['rms_db'])

def test_log_quadrature_uses_true_trapezoidal_endpoint_weights():
 grid=module.np.exp(module.np.asarray([0.0,1.0,3.0,6.0]))
 weights=module._quadrature_weights(grid)
 module.np.testing.assert_allclose(weights,module.np.asarray([0.5,1.5,2.5,1.5])/6.0)

def test_cli_rejects_out_of_range_port_before_loading_scenario(monkeypatch, tmp_path):
 network=SimpleNamespace(s_parameters=module.np.zeros((1,2,2)))
 monkeypatch.setattr(module, 'read_touchstone', lambda _path: network)
 monkeypatch.setattr(module, 's_to_z', lambda _network: None)
 monkeypatch.setattr(module, 'load_scenario_bundle', lambda _path: pytest.fail('must not solve'))
 with pytest.raises(ValueError, match='outside'):
  module.main(['--scenario','missing.spdpi','--touchstone','x.s2p','--rail-port','R=3','--output',str(tmp_path/'out.json')])

def test_cli_rejects_output_alias_before_reading_inputs(monkeypatch, tmp_path):
 scenario=tmp_path/'scenario.spdpi'; touchstone=tmp_path/'reference.s2p'
 scenario.write_bytes(b'unchanged'); touchstone.write_bytes(b'unchanged')
 monkeypatch.setattr(module, 'read_touchstone', lambda _path: pytest.fail('must not read'))
 with pytest.raises(ValueError, match='must not overwrite'):
  module.main(['--scenario',str(scenario),'--touchstone',str(touchstone),'--rail-port','R=1','--output',str(scenario)])
 assert scenario.read_bytes()==b'unchanged' and touchstone.read_bytes()==b'unchanged'

def test_file_hash_is_full_sha256_without_path_metadata(tmp_path):
 touchstone=tmp_path/'private-reference.s2p'; payload=b'one\x00two'; touchstone.write_bytes(payload)
 assert module._file_hash(touchstone)==sha256(payload).hexdigest()
 network=module.TouchstoneNetwork(frequencies_hz=module.np.asarray([1e5]),reference_ohm=1.0,s_parameters=module.np.zeros((1,2,2)),data_format='RI',port_mapping={})
 converted=SimpleNamespace(condition_numbers=module.np.asarray([1.0]),relative_residuals=module.np.asarray([0.0]))
 metadata=module._touchstone_metadata(touchstone,network,network,converted,0)
 assert metadata['basename']==touchstone.name and metadata['sha256']==sha256(payload).hexdigest() and metadata['full_file_sha256']==sha256(payload).hexdigest() and metadata['discarded_dc_record_count']==0 and 'path' not in metadata and str(touchstone.parent) not in str(metadata)

def test_cli_filters_singular_dc_before_s_to_z_and_reports_it(monkeypatch,tmp_path):
 scenario=tmp_path/'legacy.spdpi'; scenario.write_bytes(b'fixture')
 touchstone=tmp_path/'reference.s1p'
 touchstone.write_text('# Hz S RI R 1\n0 1 0\n100000 0 0\n1000000 0 0\n10000000 0 0\n100000000 0 0\n',encoding='utf-8')
 output=tmp_path/'report.json'
 frequencies=module.np.asarray([1e5,1e6,1e7,1e8])
 solve=SimpleNamespace(
  frequencies_hz=frequencies,
  impedance_ohm=module.np.ones(4,dtype=complex),
  diagnostics=SimpleNamespace(condition_numbers=module.np.ones(4),relative_residuals=module.np.zeros(4)),
 )
 outcome=SimpleNamespace(solve=solve,solver_version='test-solver',convergence=None)
 monkeypatch.setattr(module,'load_scenario_bundle',lambda _path: SimpleNamespace(scenario=object()))
 monkeypatch.setattr(module,'build_evaluation_project',lambda *_args,**_kwargs: object())
 monkeypatch.setattr(module,'evaluate_project_rail_converged',lambda *_args,**_kwargs: outcome)
 module.main(['--scenario',str(scenario),'--touchstone',str(touchstone),'--rail-port','R=1','--output',str(output)])
 report=json.loads(output.read_text(encoding='utf-8'))
 assert report['touchstone']['discarded_dc_record_count']==1
 assert report['touchstone']['source_record_count']==5
 assert report['touchstone']['converted_positive_frequency_record_count']==4
 assert report['touchstone']['full_file_sha256']==sha256(touchstone.read_bytes()).hexdigest()
