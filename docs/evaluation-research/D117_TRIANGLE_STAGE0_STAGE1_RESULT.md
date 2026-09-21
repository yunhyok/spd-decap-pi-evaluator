# SPD Decap PI Evaluator v0.23.1 — D117 Triangle Stage0/Stage1 checkpoint

Documentation-only reconciliation of sealed research evidence. This document does not authorize production use, shipping, dependency adoption, a solver run, or any new execution.

**Current gate:** `ACCEPT_C0 / ACCEPT_C1_NO_TRIANGLE_PREPARATION / STOP_C1_CONTROLLED_BUFFER_MODEL_INCOMPLETE / OPAQUE_TRIANGLE_NATIVE_FEASIBILITY_UNKNOWN / C1_NOT_AUTHORIZED`.

## Current truth (read first)

| checkpoint | sealed result | interpretation |
|---|---:|---|
| Stage0 canonical whole-tree | **135 files / 6,067,214 bytes / `1d33cb88152de9720468c85fe23dee341b53e97bd4e0f8c6050f941bd53ad78f`** | accepted W0-clipped, geometry-only research pilot |
| Stage1 | **79 files / 861,531 bytes / `b779f8e4636accfa00cbdf09f5ffbad1e0102b9c90f8292252b35592b3d3af8e`** | STOP; focused tests failed before Triangle |
| Stage1R | **41 files / 540,270 bytes / `401e95c16e35032de2c3ba913e5d227353cae0071c9e2d7130eac5533277e486`** | STOP; superseded correction checkpoint |
| Stage1S | **11 files / 419,880 bytes / `f06af2f738c7d6081c2c2a2306ec3b3a07f88c11cb2bb993446b847e4fd014cc`** | correct sealed result; static STOP because replay-receipt pin is missing |
| Stage1T | **12 files / 421,754 bytes / `a938cc4f6216afcd7d74456fa6a830a8465f79acfd8fb70e5da3b3e8e1b0cf7b`** | one node guard invocation stopped before child because the Stage0 aggregate pin was wrong; no pytest or Triangle |
| Stage1U | **11 files / 445,691 bytes / `1a23214a7f25bbf4b87b8710e08f1549b95bdfaaba11dfe06d8b99b543e3797a`** | sealed immutable STOP |
| Stage1V | **10 files / 446,779 bytes / `9d75095d41e0a0158d6a9913b41a7a2e80aa51b9285e6717bdb59f4c0b51d244`** | sealed STOP; no execution receipts |
| Stage1W | **4 files / 305,589 bytes / `b705ba473bd78047a383545ae0037b1486c53a982620370806f02b54bb31f154`** | sealed static STOP; no node, pytest, or Triangle |
| Stage1X | **9 files / 328,274 bytes / `a68c1c03bd45e8b7d086fa42aff6d327b58f57fd62ae6f88d96d35cca54eb02a`** | sealed STOP at replay-01 3-D mesh-quality gate |
| Stage1Y | **5 files / 42,936 bytes / `3506422224ea8f19ed3b06e319da9f18eba5575980719e9d4c90c2b17299d7d4`** | permanently sealed STOP before CreateProcessW; no geometry compute |
| Stage1Z | **6 files / 30,740 bytes / `fefa548586511314a051c942c43bb2f1a5dc33e25ca837bf7144cbfeb052fc77`** | permanently sealed stdlib STOP; recovered sealed-Y failure, no geometry compute |
| Stage1AA | **5 files / 33,518 bytes / `ddcc15b1f01eaf651226f9b20cf74a69c43e29e3b4705a83e421f2d89deafb33`** | bounded diagnostic captured; no accepted mesh or solver |
| Stage1AB | **4 files / 26,153 bytes / `4740bc3a13ef3983c4c3433933f6dbffd34db5828111170ca98c6d3fdc913d7e`** | q15 feasible under geometry gates only; no accepted mesh or solver |
| Stage1AC | **4 files / 275,472 bytes / `0bb7a67e8db18f30e5d360220fff1d947990aa793f2b58a733bf4f8388b1691a`** | historical pre-execution q15 source/C0/cap/test contract snapshot; extended and superseded in the same artifact root by the immutable Stage1AD diagnostic; no canonical mesh or solver |
| Stage1AD | **11 files / 5,179,731 bytes / `bde7af7bd47f87611e6a7f06915f6b5587efc35eab18fed8534311e0c13b7116`** | sealed immutable STOP diagnostic after replay-01 solely because the LF-only controller gate rejected Windows CRLF; replay-02/03 not run; no solver; superseded by accepted Stage1AE |
| Stage1AE | **15 files / 14,992,904 bytes / `8c67611395e5a924cc9e1dfafc6a590113aa576192d2b8bf4b812e54b60551b4`** | accepted q15 full-cell264 geometry-only evidence from three identical serial replays; no solver or shipping authorization |
| Stage2A C0 (cell258) | **accepted cap-02 root; exact file identities below** | `ACCEPT_C0`; geometry-only PSLG census and boundary-chain evidence; no-Triangle preparation is accepted, while controlled lifetime remains incomplete and C1 unauthorized |

Stage0 inventory remains **14,049 bytes / `e7ca09ba3e9a649135e5be6b773fdfc4d762588c3d9e6c8baca1808eb7858923`**, with 134 self-excluding rows. The old `a7e3df484f3be9ac73690f3a758e7fdc3b35220d2a831e5ccd434cc9244a405e` aggregate assertion is unsupported; it is not evidence that the sealed artifact later mutated.

Stage1's old `49be1171e50ffd71c860afdb03891e9e17bfc3f5469f708b58af7a029fb2d5d2` aggregate assertion is unsupported. Stage1R's old `685a...` assertion is unsupported. These historical assertions are retained only for audit transparency and are not evidence of later mutation.

Stage1S is the correct 11-file checkpoint, but remains static STOP: its required replay-receipt pin is missing.

Stage1T made one authorized node-guard invocation and stopped before its child because the Stage0 aggregate pin was wrong. It produced no pytest or Triangle execution.

Stage1U made one authorized node command. It invoked only the Python parser and exited `1` on an unmatched `)` at guard line 329; there was no top-level/main, pytest, Triangle, marker, receipt, or replay. Approval was stale: approval mtime preceded the guard change by **30.567 s** and pinned **51091 / `047b4afa...`**, while the actual files were **51208 / `2b3f0c65...`**. U is sealed immutable.

Stage1V had no approval, node, module, pytest, Triangle, marker, receipt, or replay. Static compile/JSON/manifest/U checks passed, but Luna modified repository source/test after declaring freeze. Guard pins are stale versus current source **36495 / `37f0be93...`** and test **17073 / `cef1d8d...`**. V is sealed STOP.

Stage1W never ran node, pytest, or Triangle. HQ caught, after Sol approval, that `STARTF_USESTDHANDLES` had invalid stdin and that the replay extra-artifact gate was incomplete. W was abandoned, not edited or retried.

Stage1X's static approval was **5,962 B / `7c98629c...`**. Its node receipt was **6,920 B / `6c40e88d...`**, PASS 1 test in runner elapsed **.625 s**; its suite receipt was **7,592 B / `0bf1f48b...`**, PASS 32 tests in **1.11 s**. Replay-01 receipt was **7,166 B / `233c948d...`** and STOP after **78.344 s**, child exit `2`, with exact stderr `SPD Decap PI Evaluator v0.23.1 STOP_STAGE1: Refusal: aspect ratio exceeds 8\r\n`. Job total was **1**, active **0**, peak commit **125,870,080**, peak working set **106,291,200**; all caps held and governed inputs were unchanged. Exactly one Triangle API call returned; all intervening result, 2-D, coverage, chain, and cap gates passed before `_certify_3d` stopped. There is no output directory, canonical artifact, or mesh receipt. Replay-02/03 were not run; retained captures are **0 B stdout** and **77 B stderr**.

The controller reason `artifact root mutated by replay child` is secondary/overwritten: postflight expected output files even after the child STOP. The child stderr above is the actual technical stop. The exact aspect value, index, vertices, and whether the offending face was planar or sidewall are unknown from sealed evidence and must not be inferred. The input sidewall-precheck maximum **4.076657...** cannot resolve post-Triangle boundary subdivision.

Stage1Y's final controller was **34,759 bytes / `9718ffe992c8b923e128ef164248a006e279f6a9cf7c70888928c4b3f3ca072e`**. The initial approval was **8,060 bytes / `f5319f702874f7c961a528e825f137211d067bcc83744c9cdf0b2367b812bc88`**; public command #1 stopped before its marker because approval `PYTHONPATH` contained `Lib/site-packages` while the runtime used `Lib\site-packages`, so child/Triangle were zero. Corrected approval was **8,061 bytes / `f0ab6aa8de4a029f835226befad17dbfdfc7e6d44ff6c70d7ce5c810d5a261a8`**, and a read-only actual `_validate_approval()` process passed.

Public command #2 created a **116-byte / `298090a1d8f4315da5a6e0631962cc606c11c7d4609b2b8a56dee7caadd67961`** marker, then stopped before `CreateProcessW` with `'int' object has no attribute 'value'` at `os.set_handle_inheritable(int(h.value), True)`. The final Y tree is the **5-file / 42,936-byte / `3506422224ea8f19ed3b06e319da9f18eba5575980719e9d4c90c2b17299d7d4`** pin. stdout and stderr captures were each empty, SHA-256 `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`; no diagnostic, receipt, temp, or process was produced, and child/Triangle/build/certifier were all zero. No accepted mesh, canonical artifact, or solver exists. X is unchanged at **9 files / 328,274 bytes / `a68c1c03bd45e8b7d086fa42aff6d327b58f57fd62ae6f88d96d35cca54eb02a`**.

### Stage1Z stdlib diagnostic evidence and recovery

Stage1Z made one public runner invocation using `subprocess.run`; the private Y child was invoked once and Triangle was loaded zero times. Its final six-file tree is **30,740 bytes / `fefa548586511314a051c942c43bb2f1a5dc33e25ca837bf7144cbfeb052fc77`**. The sealed artifacts are: runner **24,403 / `fa3b96ad0336c83cde30bf2276ba0aee1ca1192c44040e7697521269b2a3c67b`**, approval **369 / `1a3e6e0c7abdb4e15cc2e5f6a3cf77573f0806e930415514c332079bb7a495ff`**, marker **116 / `94720b542621691827f80b770ab1efe4a400f9ddc811747268569205ae32175b`**, diagnostic **215 / `ad2afa1982aab376de9f7fbb1c311f6ab31018c017831b940dc0fc1683179f4e`**, child stderr **78 / `2e5b10ce73003e16216f0aba5fd7c69c35168a8dbc905109fdc4cad09a8bebe2`**, and receipt **5,559 / `5f8eafeb2433ce9401fc9fc1e586748416e14ed76e6091ebcdd2cb587df1dd07`**.

The controller receipt is `STOP_STAGE1Z_POSTFLIGHT_FAILED`, elapsed **0.5309999999590218 s**, child return **2**, no timeout, stdout **0 B**, and before/after snapshots equal; X/Y trees and all governed inputs were unchanged. Child stderr was exactly `STOP_STAGE1Y: module 'd117_wrapper_stage1y' has no attribute 'TRIANGLE_SITE'`. No solver, canonical artifact, mesh receipt, or matching process exists. Stage1Z is permanently sealed with no retry.

Root cause: Y correctly moved loader ownership to `stage0.load_triangle_site` but passed nonexistent `wrapper.TRIANGLE_SITE`; the pinned wrapper defines `stage0.AUTHORIZED_SITE`. Static self-check, Sol, and HQ checked the loader but missed this cross-module argument symbol; HQ shares responsibility for that omission. At the Stage1Z checkpoint, the cell264 aspect cause remained unknown.

At the Stage1Z checkpoint, successor governance required reusing the Z stdlib launcher behavior and not executing sealed Y `_child`; that historical constraint is superseded by the Stage1AD recovery decision below. Exact pre-run assertions still must prove `stage0.AUTHORIZED_SITE` exists and `wrapper.TRIANGLE_SITE` is absent, and Sol must trace every external attribute to its defining module before approval.

### Stage1AA bounded diagnostic evidence

Stage1AA made one exact public pinned-Python run with `-B -P -s`. Its five-file tree is **33,518 bytes / `ddcc15b1f01eaf651226f9b20cf74a69c43e29e3b4705a83e421f2d89deafb33`**. The sealed files are: runner **20,866 / `8c878b68...`**, approval **372 / `d21beb9a...`**, marker **116 / `8d3b5da...`**, diagnostic **7,859 / `da43ad1f...`**, and receipt **4,305 / `aca2454e...`**.

The successful receipt elapsed **75.31200000000001 s**, status `STOP_STAGE1AA_DIAGNOSTIC_CAPTURED`, reasons empty, and `before==after`. It recorded one `build_pslg`, one Triangle call (`pq8Cz`, version `20250106`), and one original certifier; the call returned **25,274 vertices / 33,834 2-D triangles / 18,052 segments**. No accepted mesh, canonical artifact, mesh receipt, solver, network access, or lingering Python exists. The orchestration wrapper discrepancy is recorded without inference: it reported exit `1` with no output, while the successful receipt and runner control flow ended at its deliberate return-2 STOP; there was no retry.

The first planar-bottom offender was face **1108**, aspect **8.63588956262811**; the worst planar-bottom face was **64450**, aspect **11.248926610835994**. Counts were bottom **33,834 / 85 offenders**, top **33,834 / 85**, sidewall **36,104 / 0**, unexpected **0 / 0**. Aspect bins were `<=4`: **74,760**; `(4,8]`: **28,842**; `(8,16]`: **170**; higher: **0**. The **170** 3-D planar offender faces are mirrored top/bottom representations of **85** offending 2-D triangles: the root cause is planar Triangle quality, not sidewall extrusion.

HQ's malformed pre-edit hash handoff was caught fail-closed by Luna with no changes, then corrected once.

### Stage1AB q15 feasibility evidence

Stage1AB root: `D:\SPD-Decap-PI-Evaluator-W7\e2f219e71d8c8a397009f72242cce10d78cfc7ab\260904-d117-triangle-cell264-stage1ab-q15-probe-01`. Using the same name|size|sha256 LF-row algorithm, its final deterministic tree is **4 files / 26,153 bytes / `4740bc3a13ef3983c4c3433933f6dbffd34db5828111170ca98c6d3fdc913d7e`**. The sealed files are: runner **16,989 / `bbfe8e6b853da795f73cf2eff99b063960adbefc3c61cd8f0a7bfa71bdab628d`**, approval **360 / `5e63b8d6c80f897f46c1ed75332dc33152d54606e9023beaf7584d95e6293a4c`**, marker **116 / `3f9f678ec582c454d2d02a990ac4080828626d32cb6de98ec00feb2ded42faaf`**, and receipt **8,688 / `5634150852df9f10c6b74f7c13e5d210b2007d0ad61a2cc26d1925c276d20f2b`**.

Exactly one exact pinned-Python normal run occurred, with no retry or self-check during normal execution; exit **0**. Outer elapsed was **92.925 s**, receipt elapsed **92.36000000000013 s**, and no matching Python lingered. Receipt status is `PASS_STAGE1AB_Q15_FEASIBLE`, whose pass meaning is q15 feasibility under geometry gates only. `before==after` is true; the sealed Stage1AA tree remained **5 files / 33,518 bytes / `ddcc15b1f01eaf651226f9b20cf74a69c43e29e3b4705a83e421f2d89deafb33`**, and repo HEAD/tracked-only status was unchanged.

Triangle version was **20250106**, with `requested_call_count=1` and `real_api_call_count=1`; Stage0 legacy requested `pq8Cz`, but the proxy's effective call was `pq15Cz`. The certificate reported **31,448 2-D vertices / 46,182 2-D triangles / 62,896 3-D vertices / 128,468 faces / 18,052 boundary edges**, max aspect **6.058387859105211**, min angle **14.689946624865808**, symmetric-difference area **0**, triangle-area error **6.4373016357421875e-06**, and volume error **0.00013828277587890625**; all existing geometry gates passed.

`accepted_mesh=false`, `canonical_written=false`, `mesh_receipt_written=false`, and `solver_executed=false`. The evidence boundary is preserved: the certificate remains legacy-labeled `pq8Cz`, and full-wrapper validation is only a cross-check, not q15 provenance acceptance. No canonical mesh or solver result exists.

At the Stage1AB checkpoint, `q15` was a supported candidate. The next coherent phase was the smallest proper source/C0/cap/test contract update that made q15 explicit under the same aspect `<=8` and all existing gates, followed by separately approved canonical evidence; that phase had not begun. Stage1AB restored useful geometry compute—about **93 s** after one bounded reviewed runner—with no serial controller micro-stage.

### Stage1AC q15 source/C0/cap/test contract evidence

Stage1AC root: `D:\SPD-Decap-PI-Evaluator-W7\e2f219e71d8c8a397009f72242cce10d78cfc7ab\260904-d117-triangle-cell264-stage1ac-q15-contract-01`. Its **4-file tree / 275,472 bytes / `0bb7a67e8db18f30e5d360220fff1d947990aa793f2b58a733bf4f8388b1691a`** is the historical pre-execution snapshot; Stage1AD extended and superseded it in that same artifact root for an immutable diagnostic STOP, and Stage1AE is the accepted successor in a new root below. The sealed files are: runner **2,356 / `568bc3e2e1e9321a58507454d3a3501fe4d8321df3892217f3e96c57eb0c13ff`**, C0 JSON **268,209 / `ad6ec33da60f0f5c1dca232423efa3fa6ed90b863046fff8862df252b3df287a`**, C0 text **140 / `11a90da033ac171d64a61913fc07b9ddd450932d3632d3f3c987f955cbb2a313`**, and cap **4,767 / `a2810203bef1199d23854c849acce8a1f5f470d58328c391247e9eef2eaa94de`**.

One C0 run only exited **0** with wall time **1.240 s**, status `PASS_STAGE1_C0_CENSUS`; there was no self-check, retry, Triangle, or solver. New C0 input/geometry/split/PSLG/topology/proof/lower-bound sections exactly equal the predecessor; Triangle modules before/after were empty, pyc counts were unchanged, and the Stage0 pin was updated. The updated pins are Stage0 **25,832 / `f43a4dd3ab531b75c2f8a450406ddb70bf92cf47a94e67f2dbcede207b9c4bdd`**, Stage0 test **10,948 / `2f1c45c45d9509b20ff04a45a8cefbfb5388bdc212d103aa84abe2a1fce7a898`**, wrapper **36,557 / `d99078b06522cb84b9a2662186c620652556d189106a5c54b6d0086c969f0096`**, and cell264 test **17,151 / `77cf8ac2c5486f868db3623f58201906a0a4c2009c0a6404615acc20a5214cd0`**.

q15 is one constant used by the actual Triangle call, certificate, payload, cap, and wrapper validator; aspect `<=8`, minimum angle `>7.5`, and every cap/gate/license boundary are unchanged. At the Stage1AC checkpoint, no `pq8` remained in its four source/test files. The focused command `pinned-python -B -m pytest -p no:cacheprovider -s tests/test_d117_triangle_quality_mesh.py tests/test_d117_triangle_cell264.py` collected **46**, passed **46**, failed **0**, with no warnings; pytest elapsed **1.17 s**, shell elapsed **2.241 s**.

Sol caught and rejected two pre-execution evidence defects—first a tautological q15 test assertion, then stale cap evidence `stage0_files_edited=false`; Luna corrected both minimally before tests, and the final cap says `true` with `stage0_artifact_edited=false`. One accidental prohibited broad-status diagnostic emitted a user-owned untracked filename; it was not opened, touched, or staged, and the command was not repeated.

No canonical accepted mesh, replay output, solver, cell258, aggregate, or PowerSI result existed at the Stage1AC snapshot. Stage1AD then attempted replay-01 and remains an immutable diagnostic STOP; Stage1AE subsequently accepted three serial replays in a new root, making its matching canonical files and mesh receipts the first accepted Stage1 output. At that historical snapshot no solver, cell258, aggregate, or PowerSI result existed; the accepted Stage2A C0 cell258 census is recorded above and remains geometry-only.

The approximately **21:57–22:40 KST** effort produced zero geometry-compute seconds: a new 34.8 KB controller plus static corrections and approvals. This was orchestration/implementation inefficiency, not unavoidable physics validation. The avoidable causes were a duplicated Win32 controller instead of the proven Stage1X handle path, manual approval serialization before actual validation, and a self-check/Sol review that missed the runtime HANDLE representation.

Y and Z are permanently sealed with no retry. The Stage1AC-era guidance to prefer the proven X controller path or unchanged Z stdlib launcher is historical; Stage1AD's recovery decision was executed as Stage1AE. Approval must pass its actual validator, and Sol must compare the end-to-end result against a proven sibling.

### Stage1AD q15 replay-01 diagnostic and CRLF STOP evidence

Stage1AD uses the exact artifact root `D:\SPD-Decap-PI-Evaluator-W7\e2f219e71d8c8a397009f72242cce10d78cfc7ab\260904-d117-triangle-cell264-stage1ac-q15-contract-01`, extending and superseding the Stage1AC pre-execution snapshot. Its sealed artifacts are: adapter **4,705 bytes / `6dcd93bb94f1392293b05b5b019a152a329f7369b401d2daad6a5460c3ae4715`**, approval **6,315 bytes / `192c859c03b13b46b9191e111eb76384ab03fcf60722a10030a0c90ceadad36d`**, STOP receipt **7,707 bytes / `48ee0939f89f89a546aa20ba8e0cd801e3cb444c8730624846d76b9ba1a354de`**, stdout capture **76 bytes / `f7961b0a552d7f2b76146f7b65cb4d04c17590cb29af0aafbfbd305fd9d233a4`**, stderr capture **0 bytes / `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`**, diagnostic canonical **4,876,848 bytes / `aef077d200c6dd4d8f0bfd284ce7a3cdc0bc89a29d41b38378e4d7fd5fa17829`**, and diagnostic mesh receipt **8,608 bytes / `14a3b1ef1042d4fe149ffef969b4c205e345ce26cd00523c1d7f4bd16d3b666e`**.

Exactly one replay occurred (replay-01 only). The child `exit_code=0`; the governing receipt status is `STOP`, reason `replay stdout/stderr contract failed`, elapsed **85.015 s**, `timeout=false`, `stop_reason=null`, Job total **1** / active **0**, peak commit **219,152,384**, peak working set **198,430,720**, artifact peak **5,172,024**; all caps were held, governed `before==after`, and no matching process lingered. Replay-02/03 were forbidden after this STOP and were not run; no solver executed.

The exact stdout was the single correct success marker ending in Windows CRLF bytes `0D0A`; stderr was empty. The pinned Stage1X controller accepted only LF (`0A`). Stage1X never reached a positive success marker because its replay stopped earlier on aspect, so containment was proven but the positive stdout contract remained latent and unproven.

The diagnostic mesh receipt is not canonical accepted evidence. It reports q15 with **31,448 2-D vertices, 46,182 2-D triangles, 62,896 3-D vertices, and 128,468 faces**; max aspect **6.058387859105211**, minimum angle **14.689946624865808**, symmetric difference **0**; all listed geometry, topology, finite-coordinate/face, duplicate, incidence, orientation, Euler, and volume gates passed; `solver_executed=false`. Determinism remains unproven because replay-02/03 were forbidden.

Before execution, Luna max wrote the adapter. Independent Sol review rejected cached-pyc/TOCTOU loading and required execution of the exact hashed bytes. The corrected self-check was `PASS` in **0.106339 s** with the 5-file manifest unchanged. The initial approval JSON was malformed and Root Sol rejected it before execution. The corrected approval passed PowerShell/static validation; separately, Python `_approval()` preflight was `PASS` in **0.255230 s** with the 6-file manifest unchanged. Maximum effort does not replace Sol verification.

### Stage1AE q15 three-replay acceptance evidence

Stage1AE root: `D:\SPD-Decap-PI-Evaluator-W7\e2f219e71d8c8a397009f72242cce10d78cfc7ab\260904-d117-triangle-cell264-stage1ae-q15-replay-01`. The initial pre-execution tree was **6 files / 313,327 bytes / `fd8dc70a1b1bf8941509a48304ea6d11a46e8b0b02fdac1620ee60f35230bb82`**; the final tree was **15 files / 14,992,904 bytes / `8c67611395e5a924cc9e1dfafc6a590113aa576192d2b8bf4b812e54b60551b4`**. The sealed controller is **28,683 bytes / `0f029364f3543e393a7e2c5962f8d4425901416e3a68867b3ceda83b981157d5`**, runner **5,225 bytes / `1a01458298ddf09cdbc57a902467b5113a09ea6074f329024fb847ad47321918`**, and approval **6,303 bytes / `20082d4273d30d6b95cf59ec573c509cdfd2ce936f348765434ee87a00575db7`**.

All three serial replay receipts recorded `PASS`, with child `exit_code=0`, `timeout=false`, Job total **1** / active **0**, governed `before==after`, the exact 76-byte CRLF success marker (`f7961b0a552d7f2b76146f7b65cb4d04c17590cb29af0aafbfbd305fd9d233a4`), and empty stderr. Replay receipts and timings were: replay-01 **7,724 bytes / `9b419c23fb55757806500425ba7d86323e0d2344e6741b2b5847966929f0ab96` / 85.813 s**; replay-02 **7,736 bytes / `d1ea7b65f9e8d3a9638d73dcc5517e950512c1356888cfd2ca1d7b72401a36a3` / 86.687 s**; replay-03 **7,749 bytes / `4605e4ed1fcfd47cc1de193ab5189ad442bcecafc2f83b81f4d929dd05b92af6` / 86.078 s**. Artifact peaks were **5,198,859 / 10,092,039 / 14,985,231** bytes; peak commit **220,360,704 / 221,265,920 / 220,246,016** bytes; peak working set **200,556,544 / 201,527,296 / 200,638,464** bytes (replays 01/02/03 respectively).

Across the three replays, the three canonical files were byte-identical (4,876,848 bytes / `aef077d200c6dd4d8f0bfd284ce7a3cdc0bc89a29d41b38378e4d7fd5fa17829` each) and the three mesh receipts were byte-identical (8,608 bytes / `14a3b1ef1042d4fe149ffef969b4c205e345ce26cd00523c1d7f4bd16d3b666e` each); each receipt's output manifest matched. The Stage1AD geometry facts are therefore accepted by three identical replays as deterministic, bounded q15 full-cell264 geometry-only evidence. No temp residue remained in the artifact root, every replay receipt recorded Job active processes 0, and no solver ran.

The initial ProcessStartInfo outer observation ended before the late replay-01 receipt appeared; no retry occurred. Replays 02/03 used direct exec sessions. This timing race is an orchestration lesson, not a scientific failure.

## Accepted Cell258 Stage2A C0 and current gate

`ACCEPT_C0 / ACCEPT_C1_NO_TRIANGLE_PREPARATION / STOP_C1_CONTROLLED_BUFFER_MODEL_INCOMPLETE / OPAQUE_TRIANGLE_NATIVE_FEASIBILITY_UNKNOWN / C1_NOT_AUTHORIZED` is the current Cell258 gate. The former `STOP_C1_MEMORY_MODEL_UNPROVEN` label was refined, not cleared. The C0 run and no-Triangle preparation are geometry-only evidence; they are not Triangle meshing, extrusion, a solver run, or authorization for C1. The detailed checkpoint is maintained in the [WP1 Cell258 C1 preparation checkpoint](D117_WP1_STATIC_RESOURCE_FEASIBILITY.md#cell258-c1-preparation-checkpoint).

The immutable accepted root is
`D:\SPD-Decap-PI-Evaluator-W7\e2f219e71d8c8a397009f72242cce10d78cfc7ab\260904-d117-triangle-cell258-stage2a-c0-cap-02`.
Its hash-bound files are:

| file | bytes | SHA-256 |
|---|---:|---|
| `stage2a_c0_census.py` | 40,274 | `169d70e2d4aaf23bfdb3b9fbd209380ca77ed7ed09e09f5c89c75f32a30f3452` |
| `stage2a_c0_controller.py` | 114,544 | `fb9278f099eddcf363f36ee76f99eb647525a8fa6931b67546486f37a6fa4c02` |
| `stage2a_c0_static_approval.json` | 9,030 | `3d02662d59aa514bfbf64710119509e25d951b08bf4f3bb0cc6f21805378159e` |
| C0 JSON output | 5,448,871 | `5ae752680b63686f90dd0d80037b337bba0da71f3bd9d6d755a9e94365f9ac60` |
| C0 text output | 142 | `b4b93f13737c460f5c76272afb614312021904f679419c1fd652fed17e226d20` |
| C0 run receipt | 26,767 | `ceba8bce28329ac858c0466c948839a87ba0d35ef4934277ef06e7d2c6ea1f57` |

The governed input pins were Stage0 `25,832 B / f43a4dd3ab531b75c2f8a450406ddb70bf92cf47a94e67f2dbcede207b9c4bdd`,
D103 `204,735 B / 4ea63cf86f6b1f4e8d56eead82033606cd2a2f9b6fae1b14e857a51e4c0749f9`,
D104 receipt `19,728 B / bf3d965281f3fd09cf6be26cbd49cca22b4b3085f265ba859349e8091754263b`,
and `cell_0258.wkb` `2,426,237 B / 1d894ff46db6fdf1e1662d1ae9d45cbb6676b5d002c0357c27c74f9f1e4835e1`.

The receipt records `PASS`, elapsed **2.813 s**, child exit **0**, Job total/active
**1/0**, peak commit **285,188,096 B**, observed working-set peak
**264,499,200 B**, and maximum observed artifact **5,612,916 B / 7 items**;
governed pre/post, output, hash, and resource gates passed. Cell258 is ordinal
258, `Signal$L28(DGND)` (source layer raw/ordinal **54**, conductor), island
`spd-surface-island:ea4bc44349ce103beab4dca3`, area
**8,476,333,524.145388 um2**, bbox **[-49700,-49700,49700,49700]**, one
Polygon component (exterior ring=1), **2,049 rings**, **H=2,048**, and thickness **35 um**. The raw ring
count is **R=149,078 = exterior 8 + holes 149,070**; source thickness is
**35.0 um** and conductivity is **59,590,000**.

Process-efficiency evaluation: actual C0 compute was **2.813 s**. The two
cap-01 attempts stopped on controller boundary defects rather than geometry;
consolidating the corrected contract in cap-02 avoided adding more micro-stages
without changing the scientific scope.

The splitter step is **140 um**. It returned **S=153,246** split vertices and
segments, marker count **149,078**, marker-value sum **S**, and exactly
`{1:149070, 16:3, 32:1, 1024:4}` parts-to-original-edge distribution; all
**2,048** hole points were strictly inside; edge flags were
`corner=true, curve=false, edge=true`. Thus
`T=S+2H-2=157,340`, `Vclosed=2S=306,492`, and
`Fclosed=4S+4H-4=621,172`, within planar V/T caps **400,000/600,000** and
derived closed V/F caps **800,000/1,608,188**. The canonical PSLG is
**12,057,453 B / `1350d4d9ddb046f24e5e1bf7eae80df68fca0cbd7fdbf0dc690028e85e2e970b`**;
the canonical lower-bound estimate is **7,421,359 B**. The canonical bytes were
not retained separately, so that identity is receipt-attested and must be
recomputed and matched before any C1 work.

All `geometry_only`, `research_only`, and `non_shipped` flags are true. Triangle
extension/module load/call, extrusion, solver, FasterCap, and network counts are
zero; pyc counts were unchanged (artifact root **0**, repository **225**). The
outer orchestration record reports the sole Process.Start/PID **26700** and no
retry, but a read-only PowerShell `$PID` assignment interrupted sealing of the
outer exit/stdout/stderr and launcher-count observation. Those outer fields must
not be inferred; the cryptographically bound controller receipt remains PASS.
Cap-01 remains immutable STOP evidence; cap-02 is the accepted C0 evidence.

### Cell258 C1 preparation gate (not authorized)

The accepted no-Triangle preparation checkpoint, exact implementation/test
identities, bounded-array statuses, derived canonical cap, and remaining
prerequisites are maintained in the [WP1 Cell258 C1 preparation checkpoint](D117_WP1_STATIC_RESOURCE_FEASIBILITY.md#cell258-c1-preparation-checkpoint).
The current gate is `ACCEPT_C0 / ACCEPT_C1_NO_TRIANGLE_PREPARATION / STOP_C1_CONTROLLED_BUFFER_MODEL_INCOMPLETE / OPAQUE_TRIANGLE_NATIVE_FEASIBILITY_UNKNOWN / C1_NOT_AUTHORIZED`; the former `STOP_C1_MEMORY_MODEL_UNPROVEN` label was refined, not cleared. No C1 execution, solver, extrusion, replay, or production activity is authorized by this document.

## Evidence and interpretation

The accepted Stage0 result is the D115C W0-clipped intersection at cell264: a non-empty, boundary-contacting geometry-only pilot. Its detailed mesh, replay, supply-chain, and legal evidence remain in the sealed receipt and companion artifacts referenced by the prior D117 research records; this checkpoint records the corrected whole-tree aggregate above rather than duplicating those documents.

Stage1's immutable focused-test run was `python.exe -B -m pytest -p no:cacheprovider -s tests/test_d117_triangle_cell264.py`: 32 collected, 31 passed, 1 failed, exit `1`, with zero Triangle loads and zero mesh calls. The failure exposed the shared root cause: strict `Path.resolve` raised `FileNotFoundError` for an intentionally absent D103 path before normalization to `Refusal`; the CLI's `OSError` catch still fail-closes. The root-cause fix remains **Path.resolve OSError -> Refusal**. Stage1AE reused the byte-identical Stage1AC C0 and cap artifacts, while its approval separately pinned the execution-time source `tools/research/d117_triangle_cell264.py` (36,555 bytes / `2d3ca4178043a9e96bab9021081bf1fad6c605a238fe9054a38922a39b525dde`) and cell264 test `tests/test_d117_triangle_cell264.py` (17,149 bytes / `421b7eeb09b091e254e66b80cd39a8c34f5ea4f0748e047d131f07b83321c95d`) under `C:\Users\User\Documents\ChatGPT\SPD Decap PI Evaluator\`.

## Checkpoint evaluation

1. Parallel decomposition helped with independent backend/source/resource audits and Stage0 tree forensics; independent recomputation caught false digest claims.
2. It became inefficient for R–V: serial micro-stages and a roughly 50 KB bespoke guard created more verification surface than the cell264 pilot. Repeated handoff, freeze, and ordering errors were coordination failures.
3. Independent judgment: Sol-supervised Luna remains better than Luna-only for risky execution, but hierarchy alone is insufficient. Sol must own the executable immutable acceptance gate, with approval strictly after the final files and checks. Stage1Y confirms that duplicated controller work and approval ceremony can consume the window without any geometry computation.
Actual node/suite/replay execution totaled about **80 s** for X; Y added zero geometry-compute seconds. Stage1X restored direct technical feedback to the record, and Stage1AE completed the reviewed three-replay acceptance after the CRLF gate correction.

The scientific position is updated: the exact full-cell264 cause class is planar Triangle quality, not sidewall extrusion; Stage1AD's q15 geometry generation passed substantive geometry/resource gates and STOPped only on the strict LF-vs-Windows-CRLF transport mismatch, while Stage1AE accepted the same geometry and gates across three identical serial replays. Canonical mesh and determinism are accepted only for this bounded geometry-only result. No solver, aggregate, or PowerSI work has begun. Cell258 Stage2A C0 and its no-Triangle preparation are accepted as bounded evidence; the authoritative current gate and next prerequisites are in the [WP1 Cell258 C1 preparation checkpoint](D117_WP1_STATIC_RESOURCE_FEASIBILITY.md#cell258-c1-preparation-checkpoint), and no C1 execution is authorized by these documents.

## Recovery decision

4. Stage1AD's root remains failed and immutable: do not retry it or reuse its diagnostic output as canonical evidence. Its reviewed recovery was executed in Stage1AE as a governed copy whose only substantive acceptance-gate change was the exact LF/CRLF marker allowlist; all three serial replay receipts passed with matching canonical and mesh-receipt hashes, unchanged governed inputs, no temp residue in the artifact root, and Job active processes 0. Stage1AE is accepted only as the bounded full-cell264 geometry-only result. Cell258 Stage2A C0 and no-Triangle preparation are separately accepted in the cap-02 root above; the controlled-lifetime model remains incomplete and C1 remains unauthorized. Do not run the solver, FasterCap, aggregate, network access, global install, or production/shipping work.
5. Reset remains manual. Displayed 0% usage is not a reason to stop while calls work; an actual rate-limit or tool failure stops the run, preserves evidence, and is reported.

This edit itself changed documentation only; no tests, Triangle, mesh, build, network, or artifact-producing commands were run.
