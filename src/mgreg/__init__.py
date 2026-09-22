"""mgreg — lightweight model governance registry.

A SQLite-backed registry where every model carries a model card, a
NIST AI RMF-style risk assessment, an approval workflow, and a
tamper-evident, hash-chained audit trail of every change.

The package deliberately has zero third-party dependencies: the CLI, the
storage layer, and the read-mostly web dashboard are all stdlib.
"""

__version__ = "0.1.0"
