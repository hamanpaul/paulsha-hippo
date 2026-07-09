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


def _read_meminfo(path: str = "/proc/meminfo") -> dict[str, int]:
    """Parse /proc/meminfo into {field: kB}. Raises OSError if unreadable."""
    info: dict[str, int] = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            key, _, rest = line.partition(":")
            parts = rest.split()
            if parts and parts[0].isdigit():
                info[key.strip()] = int(parts[0])
    return info


def has_mem_headroom(
    min_fraction: float = 0.20,
    probe: Callable[[], dict[str, int]] = _read_meminfo,
) -> bool:
    """True when MemAvailable / MemTotal > min_fraction (physical RAM, swap-free).

    MemAvailable 為可用實體 RAM 估計，本質不計 swap。讀不到/欄位缺失時 fail-safe 回 True。
    """
    try:
        info = probe()
        total = float(info["MemTotal"])
        avail = float(info["MemAvailable"])
        if total <= 0:
            return True
        return (avail / total) > float(min_fraction)
    except (OSError, KeyError, ValueError, TypeError):
        return True
