"""Password hashing; for now only the S0 timing that picks the Argon2id parameters (E20)."""

import itertools
import statistics
import time
from dataclasses import dataclass

from argon2 import PasswordHasher

_SAMPLE = "x" * 16  # fixed 16-character input; the cost does not depend on its content


@dataclass(frozen=True)
class Argon2Timing:
    time_cost: int
    memory_cost_kib: int
    ms: float


def measure_argon2(
    time_costs: tuple[int, ...] = (2, 3, 4),
    memory_costs_kib: tuple[int, ...] = (19456, 47104, 65536),
    parallelism: int = 1,
    rounds: int = 3,
) -> list[Argon2Timing]:
    """Median hashing time of ``rounds`` runs for every time/memory combination."""
    timings = []
    for time_cost, memory_cost in itertools.product(time_costs, memory_costs_kib):
        hasher = PasswordHasher(
            time_cost=time_cost, memory_cost=memory_cost, parallelism=parallelism
        )
        runs = []
        for _ in range(rounds):
            start = time.perf_counter()
            hasher.hash(_SAMPLE)
            runs.append((time.perf_counter() - start) * 1000)
        timings.append(Argon2Timing(time_cost, memory_cost, statistics.median(runs)))
    return timings


def format_table(timings: list[Argon2Timing]) -> str:
    """One line per combination."""
    return "\n".join(
        f"argon2id t={t.time_cost} m={t.memory_cost_kib} KiB: {t.ms:.1f} ms" for t in timings
    )
