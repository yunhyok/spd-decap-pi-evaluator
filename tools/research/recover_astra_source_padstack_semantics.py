"""SPD Decap PI Evaluator v0.23.1: bounded source PadStack field recovery.

One64KiB source window at an already verified PadDef location; every recovered
block must match its accepted cache hash. No whole-SPD parse or board solve.
"""
import argparse
from collections import Counter
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
from time import monotonic

ROOT = Path(__file__).resolve().parents[2]
SOURCE = Path('D:/S4LB002-2Para_260729_1_injected.spd')
INVENTORY = ROOT/'outputs/research/astra-3d-source-domain-inventory-01/result.json'
INVENTORY_SHA = 'daed9082c027fb36706fb8a2eb9a00d094272f7bc7dfaf4b7d378e3a2fe6e663'
DOC = Path('C:/Cadence/Sigrity2025.1/doc/spdformat/Padstack_Description_Lines.html')
DOC_SHA = 'a6159ad90292da475bb6fbb554722482695c1ed96f7b126583c47742dfdaa7eb'
ANCHOR = 1068973014
ANCHOR_SHA = '38c75fc35e1652c3f9aaf9ef226e6cf8c75bc7b65e89cb59efad89c4f54dffd9'
LENGTH = re.compile(rb'([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)(mm|um|u|m|mil)', re.I)


def pm(token):
    match = LENGTH.fullmatch(token)
    assert match is not None
    value = Decimal(match[1].decode('ascii')) * {'mm':10**9,'um':10**6,'u':10**6,'m':10**12,'mil':25400000}[match[2].decode('ascii').lower()]
    assert value == value.to_integral() and value >= 0
    return int(value)


def run(output):
    start = monotonic()
    assert sha256(INVENTORY.read_bytes()).hexdigest() == INVENTORY_SHA
    assert sha256(DOC.read_bytes()).hexdigest() == DOC_SHA
    inventory = json.loads(INVENTORY.read_bytes())
    assert SOURCE.stat().st_size == inventory['cache_identity']['source_coverage']['source_size_bytes']
    cache = ROOT/inventory['inputs']['raw_spatial']['path']
    assert cache.stat().st_size == inventory['inputs']['raw_spatial']['size_bytes']
    connection = sqlite3.connect(cache.as_uri()+'?mode=ro&immutable=1', uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA query_only=ON')
    connection.set_progress_handler(lambda:int(monotonic()-start > 15), 10000)
    try:
        meta = dict(connection.execute('SELECT key,value FROM meta'))
        assert all(meta[key] == value for key,value in inventory['cache_identity']['raw_meta'].items())
        expected = {row['padstack_id_fold']:dict(row) for row in connection.execute('SELECT * FROM padstacks ORDER BY ordinal')}
    finally:
        connection.close()
    offset, count = ANCHOR-16384, 65536
    with SOURCE.open('rb') as stream:
        stream.seek(offset)
        window = stream.read(count)
    assert len(window) == count and sha256(window[ANCHOR-offset:ANCHOR-offset+19]).hexdigest() == ANCHOR_SHA
    pattern = rb'(?m)^\.PadStackDef[ \t]+(?P<name>\S+)[^\r\n]*\r?\n[\s\S]*?^\.EndPadStackDef[^\r\n]*\r?\n'
    records, blocks = [], []
    for match in re.finditer(pattern, window):
        name = match['name'].decode('utf-8')
        assert name.casefold() in expected
        cached = expected[name.casefold()]
        block = match[0]
        assert sha256(block).hexdigest() == cached['source_record_sha256'], name
        header = block.splitlines()[0]
        tail = header.split(None, 2)[2] if len(header.split(None, 2)) > 2 else b''
        dimensions = []
        for token in tail.split():
            if not LENGTH.fullmatch(token):
                break
            dimensions.append(pm(token))
        assert len(dimensions) <= 2
        outer = dimensions[0] if dimensions else None
        inner = dimensions[1] if len(dimensions) == 2 else None
        assert (None if outer in (None,0) else outer*2) == cached['drill_diameter_pm']
        assert inner is None or outer is not None and 0 <= inner <= outer
        attributes = {m[1].decode('ascii'):m[2].decode('utf-8') for m in re.finditer(rb'([A-Za-z][A-Za-z0-9_]*)\s*=\s*(\S+)', block)}
        records.append(dict(padstack_id=name, ordinal=cached['ordinal'], source_offset=offset+match.start(),
            source_size_bytes=len(block), source_record_sha256=cached['source_record_sha256'],
            header=header.decode('utf-8'), outer_radius_pm=outer, inner_radius_pm=inner,
            inner_radius_explicit=inner is not None, attributes=attributes,
            historical_cache_diameter_field='drill_diameter_pm', historical_cache_diameter_pm=cached['drill_diameter_pm']))
        blocks.append(block)
    assert len(records) == len({row['padstack_id'].casefold() for row in records}) == len(expected)
    assert {row['padstack_id'].casefold() for row in records} == set(expected)
    selected = next(row for row in records if row['padstack_id']=='DR-0102_60')
    dut = next(row for row in records if row['padstack_id']=='DUT')
    assert selected['outer_radius_pm']==20_000_000 and selected['inner_radius_pm'] is None
    output.mkdir()
    (output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    payload = b''.join(blocks)
    (output/'padstack-blocks.spd-fragment').write_bytes(payload)
    result = dict(program='SPD Decap PI Evaluator', version='0.23.1',
        status='VERIFIED_ALL_CACHED_PADSTACK_SOURCE_BLOCKS_AND_EXPLICIT_DIMENSIONS', elapsed_s=monotonic()-start,
        driver_sha256=sha256(Path(__file__).read_bytes()).hexdigest(), source_path=str(SOURCE),
        source_size_bytes=SOURCE.stat().st_size, source_sha256_from_accepted_cache=meta['source_sha256'],
        full_source_hash_recomputed=False, source_read_offset=offset, source_read_bytes=count,
        accepted_paddef_offset=ANCHOR, accepted_paddef_sha256=ANCHOR_SHA,
        inventory_sha256=INVENTORY_SHA, official_format_document=str(DOC), official_format_sha256=DOC_SHA,
        block_collection_sha256=sha256(payload).hexdigest(), block_collection_size_bytes=len(payload),
        padstack_count=len(records), explicit_inner_radius_count=sum(row['inner_radius_explicit'] for row in records),
        attribute_counts=dict(Counter(key for row in records for key in row['attributes'])),
        selected_device_via=selected, selected_device_pad=dut, padstacks=records,
        scope='Every complete recovered PadStack block matches its accepted source-cache hash. '
              'Official positional OuterRadius/InnerRadius semantics are preserved without silently '
              'turning an omitted inner radius into a measured manufacturing value. All attributes '
              'are retained as raw key/value evidence; their interpretation may need additional '
              'format rules. This new bounded64KiB recovery supersedes the earlier HQ no-source-read '
              'efficiency rule only for these missing source fields. No complete SPD/scenario parse, '
              'cache mutation, field solve, board outline recovery or product change occurred.')
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
