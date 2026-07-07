# syncback gate package
# Task 1: keep exports minimal; higher-level evaluate_gate not exposed yet

from .gate import ConditionResult, GateVerdict, SYNC_MANIFEST, _check_schema_unextended

__all__ = [
    'ConditionResult',
    'GateVerdict',
    'SYNC_MANIFEST',
    '_check_schema_unextended',
]
