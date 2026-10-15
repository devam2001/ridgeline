# TrendRider4hPIT.py
# TrendRider4h re-tuned on point-in-time in-sample pairs (candidate).
#
# Same entry, exit and sizing logic as TrendRider4h. Only the hyperopt
# parameters differ: they live in TrendRider4hPIT.json, produced by
#   python scripts/research.py hyperopt -s TrendRider4hPIT
# which tunes on the pairs that were actually the top 25 by volume at the
# start of the in-sample period. TrendRider4h's own parameters were tuned on a
# hand-picked list chosen with today's knowledge, so this is the fairer
# version; judge it on out-of-sample data only.

import sys
from pathlib import Path

_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from TrendRider4h import TrendRider4h  # noqa: E402


class TrendRider4hPIT(TrendRider4h):
    pass
