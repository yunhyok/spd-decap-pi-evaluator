from pathlib import Path
import numpy as np
import pytest
from spd_decap_pi._core.io.touchstone import TouchstoneError, TouchstoneNetwork, open_circuit_zpp, read_touchstone, s_to_z

def write(tmp_path,name,body):
 p=tmp_path/name; p.write_text(body,encoding='utf8'); return p
def test_nonreciprocal_three_port_is_row_major(tmp_path):
 values='1e6 '+ ' '.join(f'{x} 0' for x in (.1,.2,.3,.4,.5,.6,.7,.8,.9)); n=read_touchstone(write(tmp_path,'n.s3p','# Hz S RI R 1\n'+values)); assert n.s_parameters[0,0,1]==.2 and n.s_parameters[0,1,0]==.4
def test_legacy_two_port_order(tmp_path):
 n=read_touchstone(write(tmp_path,'n.s2p','# MHz S RI R 50\n1 .1 0 .2 0 .3 0 .4 0')); assert n.frequencies_hz[0]==1e6 and n.s_parameters[0,0,1]==.3 and n.s_parameters[0,1,0]==.2
@pytest.mark.parametrize(('fmt,pair,expected'),[('RI','0.5 0.5',.5+.5j),('MA','2 90',2j),('DB','6.020599913 180',-2+0j)])
def test_format_units_wrapping_and_inline_comments(tmp_path,fmt,pair,expected):
 text=f'! Port[1] = A\n# GHz S {fmt} R 50\n1 {pair} {pair} ! inline\n{pair} {pair}\n'; n=read_touchstone(write(tmp_path,'x.s2p',text)); assert n.port_mapping=={1:'A'}; assert np.allclose(n.s_parameters[0,0,0],expected)
def test_known_s_to_z_and_open_port(tmp_path):
    n=read_touchstone(write(tmp_path,'x.s1p','# Hz S RI R 50\n1e6 0.5 0')); z=s_to_z(n); assert np.allclose(z.z_parameters[0,0,0],150); assert np.allclose(open_circuit_zpp(z,1),[150])
    with pytest.raises(TouchstoneError): open_circuit_zpp(z, True)
    with pytest.raises(TouchstoneError): open_circuit_zpp(z, 1.5)
@pytest.mark.parametrize('body',[ '# Hz S RI R 50\n1 .1 0', '# Hz S RI R 50\n2 .1 0\n1 .1 0', '# Hz Y RI R 50\n1 .1 0 .1 0 .1 0 .1 0'])
def test_invalid_input_fails_closed(tmp_path,body):
 with pytest.raises(TouchstoneError): read_touchstone(write(tmp_path,'bad.s2p',body))

def test_option_only_file_fails_closed(tmp_path):
 with pytest.raises(TouchstoneError): read_touchstone(write(tmp_path,'empty.s1p','# Hz S RI R 50\n'))

def test_s_to_z_rejects_near_singular_forward_error_but_accepts_high_z_boundary():
 f=np.asarray([1e6])
 safe=np.asarray([[[0j,0j],[0j,1.0-1e-7]]])
 accepted=s_to_z(TouchstoneNetwork(f,safe,50.0,{},'RI'))
 assert accepted.condition_numbers[0] < 1e8 and accepted.z_parameters[0,1,1].real > 1e8
 unsafe=np.asarray([[[0j,0j],[0j,1.0-1e-10]]])
 with pytest.raises(TouchstoneError,match='unreliable'):
  s_to_z(TouchstoneNetwork(f,unsafe,50.0,{},'RI'))
