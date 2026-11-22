# Independent review and repair

Initial independent H3 verdict: FAIL. Reviewer reproduced a rehashed RunState
with no artifact identity succeeding, and an external ledger-file symlink being
read successfully. Original 10 adapter and 25 runner tests passed despite both
defects; historical full-suite evidence was not independently rerun.

Repair cycle 1 (controller): require saved identity for RunState,
MemoryCheckpoint, EvalResult and ReviewPacket; resolve/confine the ledger read
target before delegating to the unchanged existing ledger reader. Logical
records and an absent local ledger retain their existing behavior.

Exact added regression command:
`python -B -m unittest discover -s tests -p test_shared_contract.py -v`
using the installed Python 3.14 executable documented in original evidence.
RED: 14 tests, 10 assertion failures (eight identity cases, two path escapes).
GREEN: 14 tests passed, including file and directory symlink escapes, missing
and null identities for all four saved-file types, and absent-ledger behavior.
Existing runner suite: 25 passed after repair. See SHARED_CONTRACT_REVIEW_RED.txt,
SHARED_CONTRACT_REVIEW_GREEN.txt and SHARED_CONTRACT_REVIEW_RUNNER.txt.

Fresh independent review pending. No shared kernel or existing ledger/harness
edits; local OS authority, live ingestion and owner acceptance limits remain.
