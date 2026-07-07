import os
from typing import Callable, Tuple


def is_idle(max_load: float = 1.0, probe: Callable[[], Tuple[float, ...]] = os.getloadavg) -> bool:
    """Return True when system is considered idle using the 1-minute load average.

    probe must be a callable that returns a tuple (like os.getloadavg()).
    Tuples are required; lists or other sequence types are rejected with TypeError.
    Scalars are not supported and will raise TypeError. If the load cannot be
    determined due to OSError, AttributeError, or IndexError, the function
    fails safe and returns True.
    """
    try:
        result = probe()
        # Only accept tuple-style results matching os.getloadavg()
        if not isinstance(result, tuple):
            raise TypeError("probe must return a tuple like os.getloadavg()")
        load = float(result[0])
        return load <= float(max_load)
    except (OSError, AttributeError, IndexError):
        # fail-safe: if we can't determine load, allow running
        return True
