"""Concurrent startup readers share one bounded version inventory probe."""
from concurrent.futures import ThreadPoolExecutor
import subprocess
import threading
import time

import capability_registry as registry


def test_cold_inventory_is_shared_and_concurrency_is_bounded(monkeypatch):
    monkeypatch.setattr(registry, '_INVENTORY_CACHE', None)
    lock = threading.Lock()
    calls, active, maximum = [], 0, 0
    def detect(name):
        nonlocal active, maximum
        with lock:
            calls.append(name)
            active += 1
            maximum = max(maximum, active)
        time.sleep(.03)
        with lock:
            active -= 1
        return registry.Capability(name, 'fixture', None, False, None, 'fixture', 'fixture')
    monkeypatch.setattr(registry, 'detect', detect)
    with ThreadPoolExecutor(max_workers=2) as readers:
        first, second = list(readers.map(lambda _: registry.inventory(), range(2)))
    assert len(calls) == len(registry.SPECS)
    assert 1 < maximum <= 8
    assert [item['id'] for item in first] == list(registry.SPECS)
    assert first == second
    first[0]['detail'] = 'changed by reader'
    assert registry.inventory()[0]['detail'] == 'fixture'


def test_multiple_executable_candidates_share_one_timeout_budget(monkeypatch):
    clock, calls = [0.0], []
    monkeypatch.setattr(registry.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(registry, 'executable_candidates', lambda _: ['/fixture/one', '/fixture/two'])
    def timeout(args, **kwargs):
        calls.append(kwargs['timeout'])
        clock[0] += kwargs['timeout']
        raise subprocess.TimeoutExpired(args, kwargs['timeout'])
    monkeypatch.setattr(registry.subprocess, 'run', timeout)
    value = registry.detect('httpx')
    assert value.available is False
    assert calls == [8]
