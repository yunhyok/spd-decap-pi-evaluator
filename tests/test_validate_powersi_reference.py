import importlib.util
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

def test_comparison_rejects_missing_band_or_reference_endpoint_coverage():
 f=module.np.asarray([1e5,1e6,1e7,1e8]); z=module.np.ones(4,dtype=complex)
 with pytest.raises(ValueError): module.comparison_metrics(f,z,module.np.asarray([2e5,1e8]),module.np.ones(2,dtype=complex))
 with pytest.raises(ValueError): module.comparison_metrics(module.np.asarray([1e5,1e6]),z[:2],f,z)

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
 network=SimpleNamespace(reference_ohm=1.0,s_parameters=module.np.zeros((1,2,2)),data_format='RI',port_mapping={})
 converted=SimpleNamespace(condition_numbers=module.np.asarray([1.0]),relative_residuals=module.np.asarray([0.0]))
 metadata=module._touchstone_metadata(touchstone,network,converted)
 assert metadata['basename']==touchstone.name and metadata['sha256']==sha256(payload).hexdigest() and 'path' not in metadata and str(touchstone.parent) not in str(metadata)
