"""Toy adapter dependency for isolated tests, NOT the shared execution kernel.

Only the fixture vocabulary is supported. Real conformance requires the separate
explicitly selected external-contract integration suite.
"""
VALID_ARTIFACT_TYPES = frozenset('ExecutionContract RunState MemoryCheckpoint EvalResult ReviewPacket StateTransition ExecutionPlan'.split())
REQUIRED_DRY_RUN_ARTIFACT_TYPES = ('RunState', 'MemoryCheckpoint', 'EvalResult', 'ReviewPacket')
LIFECYCLE_ALIASES = {'needs_human_approval': 'needs_review'}


def normalize_lifecycle_state(state):
    if not isinstance(state, str) or state not in ('needs_review', 'needs_human_approval'):
        raise ValueError('unsupported lifecycle state in synthetic test dependency')
    return LIFECYCLE_ALIASES.get(state, state)
