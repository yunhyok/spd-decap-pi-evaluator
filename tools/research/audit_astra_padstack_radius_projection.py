"""SPD Decap PI Evaluator v0.23.1: prove omitted inner-radius information.

Only two tiny authored format probes are parsed. The production SPD and source
caches are untouched; this does not infer their missing inner-radius values.
"""
import argparse
from dataclasses import asdict
from hashlib import sha256
import json
import mmap
from pathlib import Path
from time import monotonic
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
DOC = Path('C:/Cadence/Sigrity2025.1/doc/spdformat/Padstack_Description_Lines.html')
DOC_SHA = 'a6159ad90292da475bb6fbb554722482695c1ed96f7b126583c47742dfdaa7eb'
SOURCE_PINS = {
    'src/spd_decap_pi/_core/io/spd.py': 'fc17618801367294d1d2c1eabcef1d54db2603cfe88e2d6bfc191a3b484761fe',
    'src/spd_decap_pi/raw_spatial_contact_compiler.py': 'a1d83f96cfda9fe62e1f30cfec5868596b752dd17a447c000b18ddb63dfb5c7b',
}


def run(output):
    start = monotonic()
    assert sha256(DOC.read_bytes()).hexdigest() == DOC_SHA
    for path, digest in SOURCE_PINS.items():
        assert sha256((ROOT/path).read_bytes()).hexdigest() == digest
    from spd_decap_pi._core.io.spd import _parse_padstacks as core_parse
    from spd_decap_pi.raw_spatial_contact_compiler import _parse_padstacks as raw_parse
    output.mkdir()
    (output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    records = {}
    for label, inner_radius_um in (('solid_inner_zero', 0), ('hollow_inner_15um', 15)):
        data = (f'.PadStackDef TEST 20um {inner_radius_um}um Material = COPPER InnerMaterial = AIR\n'
                '.PadDef TOP\nRegular Circle 30um\n.EndPadDef\n.EndPadStackDef\n').encode('ascii')
        path = output/f'{label}.spd-fragment'
        path.write_bytes(data)
        with path.open('rb') as stream, mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as mapped:
            analysis = SimpleNamespace(padstacks=core_parse(mapped, 0, len(data), []))
        stacks, shapes, _, _ = raw_parse(path, 0, len(data), analysis, {'top':'TOP'}, lambda:False)
        assert len(stacks) == len(shapes) == 1
        row, shape = asdict(stacks[0]), asdict(shapes[0])
        assert row['drill_diameter_pm'] == 40_000_000 and shape['width_pm'] == 60_000_000
        records[label] = dict(authored_outer_radius_um=20, authored_inner_radius_um=inner_radius_um,
            core=asdict(analysis.padstacks[0]), cached_padstack=row, cached_pad_shape=shape,
            snippet_sha256=sha256(data).hexdigest())
    solid, hollow = records.values()
    assert solid['core'] == hollow['core']
    numeric = lambda row: {key:value for key,value in row['cached_padstack'].items() if key != 'source_record_sha256'}
    assert numeric(solid) == numeric(hollow)
    assert solid['cached_padstack']['source_record_sha256'] != hollow['cached_padstack']['source_record_sha256']
    assert solid['cached_pad_shape'] == hollow['cached_pad_shape']
    result = dict(program='SPD Decap PI Evaluator', version='0.23.1',
        status='CONFIRMED_OUTER_RADIUS_SEMANTICS_AND_INNER_RADIUS_PROJECTION_LOSS', elapsed_s=monotonic()-start,
        driver_sha256=sha256(Path(__file__).read_bytes()).hexdigest(),
        official_document=dict(path=str(DOC), sha256=DOC_SHA, product_version='Sigrity2025.1 September2025'),
        source_pins=SOURCE_PINS, cases=records,
        same_retained_numeric_padstack=True, same_retained_pad_shape=True, distinct_source_hashes=True,
        illustrative_equal_length_dc_resistance_ratio=(20**2)/(20**2-15**2),
        scope='Official syntax calls the first/second positional dimensions OuterRadius/InnerRadius. '
              'The existing source-cache drill_diameter_pm is twice OuterRadius for this syntax. '
              'The actual parser accepts distinct explicit inner radii yet produces identical retained '
              'physical scalars, with only the opaque source hash differing. The selected DR-0102_60 '
              'outer diameter is therefore40um under this contract, not a40um empty bore; its60um '
              'regular pads do not set barrel diameter. Actual inner radius, fill and conductivity '
              'overrides remain unbound. The2.2857 resistance ratio is only an authored geometric '
              'counterexample with equal material/length, not a source-via or board estimate. '
              'No raw production SPD, geometry subtraction, solver update or field solve occurred.')
    (output/'result.json').write_text(json.dumps(result, indent=2, allow_nan=False), encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    destination = parser.parse_args().output.resolve()
    if destination.exists():
        raise FileExistsError(destination)
    try:
        run(destination)
    except Exception as error:
        destination.mkdir(parents=True, exist_ok=True)
        (destination/'failure.json').write_text(json.dumps(dict(error=repr(error))), encoding='utf-8')
        raise
