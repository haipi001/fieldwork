"""Monitor outage retry timing, observable state, and responsive stop controls."""
import asyncio
from types import SimpleNamespace
import time

import pytest
import agent_audit
import agent_monitor_service as service
from tests.test_final import client


class ClockStop:
    def __init__(self):
        self.now = 0
        self.stopped = False
    def is_set(self):
        return self.stopped
    def set(self):
        self.stopped = True
    def wait(self, seconds):
        self.now += seconds
        return self.stopped


def test_monitor_failures_backoff_and_success_resets_interval(client, monkeypatch):
    clock = ClockStop()
    monkeypatch.setattr(service, 'time', SimpleNamespace(time=lambda: 1000 + clock.now, monotonic=lambda: clock.now))
    ticks, beats = [], []
    def step():
        ticks.append(clock.now)
        if len(ticks) <= 3:
            raise OSError('fixture collection failure')
        if len(ticks) == 5:
            clock.set()
    monkeypatch.setattr(service, 'heartbeat', lambda error=None, **fields: beats.append((error, fields)))
    service.run(clock, step=step)
    assert ticks == [0, 2, 6, 14, 16]
    assert any(error == 'OSError' and fields['failures'] == 3 and fields['retry_after'] == 1014 for error, fields in beats)
    assert beats[-1] == (None, {'service_id': None, 'failures': 0, 'retry_after': None})
    assert service.retry_delay(1000) == 60


def test_managed_stop_is_checked_during_backoff(client, monkeypatch):
    import monitor_process_control
    clock = ClockStop()
    monkeypatch.setattr(service, 'time', SimpleNamespace(time=lambda: 1000 + clock.now, monotonic=lambda: clock.now))
    monkeypatch.setattr(service, 'heartbeat', lambda *args, **kwargs: None)
    monkeypatch.setattr(monitor_process_control, 'should_stop', lambda _: clock.now >= 16)
    ticks = []
    def step():
        ticks.append(clock.now)
        raise OSError('fixture failure')
    service.run(clock, step=step, service_id='f' * 32)
    assert ticks == [0, 2, 6, 14]
    assert clock.now == 16  # Stops within the 16-second backoff, not at its end.


def test_backoff_heartbeat_is_not_reported_as_healthy_collection(client):
    with service.process_lock('service.lock'):
        service.heartbeat('OSError', failures=3, retry_after=time.time() + 8)
        value = service.status()
        assert value['running'] and value['state'] == 'backoff'
        assert value['failures'] == 3 and value['error'] == 'OSError'
        service.heartbeat()
        recovered = service.status()
        assert recovered['state'] == 'current'
        assert recovered['failures'] == 0 and recovered['retry_after'] is None


@pytest.mark.parametrize('fields', [{'failures': True}, {'failures': -1}, {'retry_after': float('inf')}, {'retry_after': time.time() + 3600}])
def test_malformed_retry_metadata_remains_unknown(client, fields):
    with service.process_lock('service.lock'):
        service.heartbeat('OSError', **fields)
        assert service.status()['state'] == 'starting'


def test_app_scheduler_uses_backoff_and_can_cancel(client, monkeypatch):
    delays, attempts = [], []
    monkeypatch.setattr(service, 'status', lambda: {'running': False})
    def scan():
        attempts.append(True)
        if len(attempts) < 3:
            raise OSError('fixture scheduler failure')
    async def sleep(delay):
        delays.append(delay)
        if len(delays) == 3:
            raise asyncio.CancelledError
    monkeypatch.setattr(agent_audit, 'scan_active_monitors', scan)
    monkeypatch.setattr(agent_audit, 'asyncio', SimpleNamespace(to_thread=asyncio.to_thread, sleep=sleep))
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(agent_audit.monitor_worker())
    assert delays == [2, 4, 2]


def test_expired_retry_is_degraded_until_success(client, monkeypatch):
    now = time.time()
    with service.process_lock('service.lock'):
        service.heartbeat('OSError', failures=2, retry_after=now + 4)
        monkeypatch.setattr(service, 'time', SimpleNamespace(time=lambda: now + 6))
        assert service.status()['state'] == 'degraded'


@pytest.mark.parametrize('error,failures,retry', [(None, 3, True), ('OSError', 3, False)])
def test_inconsistent_retry_state_is_not_healthy(client, error, failures, retry):
    with service.process_lock('service.lock'):
        service.heartbeat(error, failures=failures, retry_after=time.time() + 4 if retry else None)
        assert service.status()['state'] == 'starting'
