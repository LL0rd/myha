from myha_gateway.auth.passwords import format_table, measure_argon2


def test_measure_argon2_times_every_combination() -> None:
    # Tiny memory costs keep the test fast; the defaults are measured on the real hardware.
    timings = measure_argon2(memory_costs_kib=(8, 16, 32), rounds=1)
    assert [(t.time_cost, t.memory_cost_kib) for t in timings] == [
        (time_cost, memory) for time_cost in (2, 3, 4) for memory in (8, 16, 32)
    ]
    assert all(t.ms > 0 for t in timings)
    table = format_table(timings).splitlines()
    assert len(table) == 9 and table[0].startswith("argon2id t=2 m=8 KiB: ")
