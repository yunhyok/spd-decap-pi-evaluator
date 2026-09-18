"""A1 prototype: decap placement search on top of `spd_pi_engine` (docs/engine/APPS_PLAN_2026-09-18.md).

`search.py` is the library (`parse_mask`, `mask_from_full`, `backward_eliminate`, `validate`),
`main.py` the CLI.  Nothing here touches the engine numerics; the engine is imported, never
modified.
"""

__all__ = ["search"]
