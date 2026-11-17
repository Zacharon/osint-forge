# Shared contract adapter: local candidate

Capability `ledger.project/v1` is a read-only local inspection command. Humans use
`python -m runner.shared_contract --project-root <local-root> --contract <trusted-execution_contract.py>`;
agents invoke that same command or `project_ledger(root, load_contract(path))`.
Both use the same validation and saved ledger. JSON stdout is the projection;
exit 2 and stderr describe invalid input. No partial result is emitted on failure.
## Trusted code boundary

`--contract` explicitly selects trusted executable Python code, not passive JSON.
Only existing `.py` files are accepted. Loading executes that file with the
caller's privileges; no sandbox is provided. Do not select an untrusted source.
There is no machine-specific default or automatic private kernel discovery.

## Validation order

The ledger is `.kernel_runs/ledger/artifact_ledger.jsonl` under the selected root.
Inspection assumes a trusted local root whose ledger paths and artifact files
are not being changed concurrently. Resolved-path checks reject static escapes;
they are not an OS sandbox or an atomic filesystem snapshot.
The entire hash chain is checked before projection. All referenced artifact files
must exist within the root and match their recorded SHA256. `record_type` maps to
`artifact_type`, `artifact_path` remains explicit (null for logical records), and
`StateTransition.payload.to_status` maps through shared normalization. Artifact
review statuses use metadata.status for RunState, MemoryCheckpoint, EvalResult
and ReviewPacket. Missing and unsupported lifecycle states fail closed. Other
artifact types carry no inferred lifecycle; ExecutionPlan's plan_ready remains
source metadata. Original records are retained as immutable JSON strings, and raw
aliases remain separate from canonical values. Returned dictionaries are detached
snapshots; changing one cannot change the ledger or future projections.

The saved synthetic fixture is `tests/fixtures/shared_contract/`: six records plus
four referenced files. Tests copy its exact bytes into disposable local roots,
then exercise the real ledger reader and adapter. No fixture approval is an owner
decision. To inspect manually, copy that directory to a disposable root and copy
artifact_ledger.jsonl into `.kernel_runs/ledger/` there, then run the command above.
Self-contained tests use a deliberately limited toy Python dependency, not an
export of the private shared kernel. These tests prove adapter behavior only.
For real-contract integration, explicitly set `OSINT_FORGE_TEST_CONTRACT` to a
trusted external `.py` file, then run `python -m unittest discover -s tests`.
Without that variable the external suite reports a skip (NOT RUN). If set to a
missing or invalid contract, integration fails rather than falling back.
All aliases supplied by the selected real contract are checked in that suite.

MissionSupervisor remains the single mission orchestrator. It owns governed tool
execution, budget/PII gates, persistence and resumption; HumanReviewGate owns its
existing review boundary. This adapter only inspects evidence and never supervises,
executes or records a review decision. Operational completed/awaiting_review states
are not kernel lifecycle aliases. Vector memory remains a non-production spike;
this change starts no ingestion or production connection.

Feature gates (local candidate):

| Gate | Result and evidence |
|---|---|
| 1 Shared capabilities | PASS: CLI/API parity and matching invalid-state rejection in test_cli_parity_and_error. |
| 2 Shared state | PASS for read-only inspection: same saved bytes, fresh reads and detached snapshots in test_saved_six_records_coverage_alias_and_immutable_source. No writes. |
| 3 Scoped authority | PARTIAL: static resolved-path confinement and saved-file integrity checked; local OS file access is the boundary. No verified application principal, grants or expiry; copied actor/status values convey no authority. |
| 4 Human usability | PASS for local CLI: AI-free command, JSON output, actionable stderr and nonzero error code exercised. Human acceptance NOT RUN. |
| 5-7 Supervision/recovery/receipts | NOT APPLICABLE: inspection causes no user-data writes or external effects. |

Hosted, browser, device and live ingestion evidence: NOT RUN. Owner acceptance remains pending. The prior independent bounded recheck was
reported VERIFIED; these portability edits still require review; no production or release completion claim.

## Local validation

Final portable suite: `python -m unittest discover -s tests` ran 169 tests,
OK with 25 skips (24 existing optional/runtime skips and one external-contract
class skip). Explicit trusted real-contract selection ran 32 adapter tests,
all passing: 16 synthetic and 16 external integration cases. These are local
Python results, not hosted, browser, device or human acceptance.

Fixture attributes preserve artifact bytes on checkout because SHA256 values
bind exact saved bytes, including line endings. The JSONL fixture is synthetic and contains no private paths.
