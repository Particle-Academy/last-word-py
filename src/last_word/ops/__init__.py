"""Document versions as ops: diff, reduce and the op schema.

The Python port of PHP last-word 0.6.3's `Ops\\` namespace (issue
Particle-Academy/last-word#2). The module-level entry points are
`last_word.diff`, `last_word.reduce`, `last_word.op_schema` and
`last_word.equivalent`; these classes are the peer-named building blocks.
"""

from .doc_diff import DocDiff
from .doc_op_schema import DocOpSchema
from .doc_reducer import DocReducer

__all__ = ["DocDiff", "DocOpSchema", "DocReducer"]
