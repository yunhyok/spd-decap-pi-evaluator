"""Restore the existing D104-named raw index for bounded read-only queries."""

import hashlib
import json
from pathlib import Path
import sqlite3
from time import monotonic
from zipfile import ZipFile
import zlib


ROOT = Path(__file__).resolve().parents[2]
BUNDLE = Path(r"D:\SPD-Decap-PI-Evaluator-W7\2928ca73ffa0d0d1421cd393939b6fea1d025f42\260729-17dt-raw-spatial-v3\S4LB002-2Para_260729_1_injected_candidate.spdpi")
MEMBER = "attachments/spatial/raw-spatial-contact-v3-40cb44b2376f59d6.sqlite.zlib"
COMPRESSED_SHA = "c5f7085edcf9d0e638f01602633f9df158472eb8e2959989d9fa7693e504947a"
DECODED_SIZE = 2752458752  # Independently read from this SQLite header.
DEST = ROOT / "outputs/research/astra-step3-source-index-01"


def main():
    started = monotonic()
    DEST.mkdir(parents=True, exist_ok=False)
    partial = DEST / "raw-spatial.sqlite.partial"
    decoded = DEST / "raw-spatial.sqlite"
    report_path = DEST / "extraction.json"
    report = {"program": "SPD Decap PI Evaluator", "version": "0.23.1", "status": "STOP"}
    compressed_hash, decoded_hash = hashlib.sha256(), hashlib.sha256()
    read = written = 0
    try:
        with ZipFile(BUNDLE) as archive, archive.open(MEMBER) as source, partial.open("xb") as target:
            assert archive.getinfo(MEMBER).file_size == 680683143
            inflater = zlib.decompressobj()
            while chunk := source.read(1024 * 1024):
                if monotonic() - started > 60:
                    raise TimeoutError("60-second extraction budget exceeded")
                read += len(chunk)
                compressed_hash.update(chunk)
                pending = chunk
                while pending:
                    produced = inflater.decompress(pending, 1024 * 1024)
                    pending = inflater.unconsumed_tail
                    written += len(produced)
                    if written > DECODED_SIZE or inflater.unused_data:
                        raise ValueError("unexpected expansion or trailing compressed data")
                    decoded_hash.update(produced)
                    target.write(produced)
                    if monotonic() - started > 60:
                        raise TimeoutError("60-second extraction budget exceeded")
            assert inflater.eof and not inflater.unused_data
            assert written == DECODED_SIZE
            assert compressed_hash.hexdigest() == COMPRESSED_SHA
        partial.rename(decoded)
        # Reuse the existing file schema; never call a builder, compiler, or whole-DB validator.
        with sqlite3.connect(decoded.as_uri() + "?mode=ro&immutable=1", uri=True) as connection:
            connection.execute("PRAGMA query_only=ON")
            connection.execute("PRAGMA trusted_schema=OFF")
            assert connection.execute("PRAGMA application_id").fetchone()[0] == 0x53505257
            assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
            tables = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            assert {"nodes", "pad_shapes", "vias", "source_coverage"} <= set(tables)
        report.update(status="ACCEPT_BYTE_IDENTICAL_INDEX_ONLY", database=str(decoded), tables=tables)
    except Exception as exc:
        report.update(error_type=type(exc).__name__, error=str(exc))
        raise
    finally:
        report.update(bundle=str(BUNDLE), member=MEMBER, compressed_bytes=read, decoded_bytes=written,
                      compressed_sha256=compressed_hash.hexdigest(), decoded_sha256=decoded_hash.hexdigest(),
                      elapsed_s=monotonic() - started,
                      scope="Existing SQLite restored from verified compressed bytes. No new schema, SPD scan, full scenario load, topology compile, or production binding acceptance.")
        report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report))


if __name__ == "__main__":
    main()
