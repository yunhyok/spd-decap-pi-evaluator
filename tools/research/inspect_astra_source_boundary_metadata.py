"""SPD Decap PI Evaluator v0.23.1: inspect bounded non-geometry source metadata.

Skip the 422MB Shape and 647MB Node/Trace/Via sections. This is not a complete
source parse or a proof that no outline exists anywhere in the source file.
"""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import re
from time import monotonic

ROOT = Path(__file__).resolve().parents[2]
SOURCE = Path('D:/S4LB002-2Para_260729_1_injected.spd')
SIZE = 1116717287
SOURCE_SHA = '40cb44b2376f59d6b606eb9b4d138204fe51b2dc6b3332d3b7c0e7d4202866d2'
PAD_ANCHOR = 1068973014
PAD_SHA = '38c75fc35e1652c3f9aaf9ef226e6cf8c75bc7b65e89cb59efad89c4f54dffd9'
KEYWORD = re.compile(rb'outline|boundary|specialvoid|voidthreshold', re.I)


def inspect(data, offset):
    headers, hits = [], []
    for match in re.finditer(rb'(?m)^[^\r\n]*', data):
        line = match[0]
        if line.startswith(b'* ') and b'description' in line.lower():
            headers.append(dict(offset=offset+match.start(), line=line.decode('utf-8')))
        if KEYWORD.search(line):
            hits.append(dict(offset=offset+match.start(), line=line.decode('utf-8')))
    return headers, hits


def run(output):
    started = monotonic()
    assert SOURCE.stat().st_size == SIZE
    assert inspect(b'* Shape description lines\n.Outline x\n', 10)[1] == [dict(offset=36, line='.Outline x')]
    ranges = [('prefix', 0, 683), ('stackup', 422134455, 422158408),
              ('post_via_metadata', 1068969763, SIZE)]
    windows, header_index, keyword_lines = [], [], []
    with SOURCE.open('rb') as stream:
        for name, begin, end in ranges:
            stream.seek(begin)
            data = stream.read(end-begin)
            assert len(data) == end-begin
            if name == 'stackup':
                assert data.startswith(b'* CuttingBoundary description lines\n\n* Layer description lines')
                assert data.endswith(b'* ConformalLayer description lines\n\n')
            if name == 'post_via_metadata':
                local = PAD_ANCHOR-begin
                assert sha256(data[local:local+19]).hexdigest() == PAD_SHA
                assert data.rstrip().endswith(b'\n.End')
            headers, hits = inspect(data, begin)
            header_index.extend(headers)
            keyword_lines.extend(hits)
            windows.append(dict(name=name, begin=begin, end=end, bytes=len(data), sha256=sha256(data).hexdigest()))
    output.mkdir()
    (output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    result = dict(program='SPD Decap PI Evaluator', version='0.23.1',
        status='INSPECTED_BOUNDED_SOURCE_METADATA__NOT_FULL_OUTLINE_ABSENCE_PROOF',
        elapsed_s=monotonic()-started, driver_sha256=sha256(Path(__file__).read_bytes()).hexdigest(),
        source_path=str(SOURCE), source_size_bytes=SIZE, source_sha256_from_accepted_cache=SOURCE_SHA,
        source_hash_recomputed=False, accepted_paddef_offset=PAD_ANCHOR, accepted_paddef_sha256=PAD_SHA,
        windows=windows, total_source_bytes_read=sum(row['bytes'] for row in windows),
        section_headers=header_index, boundary_keyword_lines=keyword_lines,
        empty_cutting_boundary_section=dict(begin=422134455, end=422134492,
            raw_text='* CuttingBoundary description lines\n\n'),
        scope='Only the named byte ranges were read. The large Shape/Node/Trace/Via sections '
              'were skipped; no global absence or lateral dielectric boundary is inferred. '
              'A hash-matched accepted PadDef anchors the post-via metadata window. '
              'The observed empty CuttingBoundary section is distinct from .Outline. '
              'No geometry, solver, cache, product runtime or old result is changed.')
    (output/'result.json').write_text(json.dumps(result, indent=2, allow_nan=False), encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    destination = parser.parse_args().output.resolve()
    if destination.exists():
        raise FileExistsError(destination)
    run(destination)
