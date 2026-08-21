import time
import pytest
from daemon.concurrency import AIMDConcurrencyController

def test_aimd_initialization():
    controller = AIMDConcurrencyController(initial_concurrency=4, min_concurrency=2, max_concurrency=10)
    assert controller.current_concurrency == 4

def test_aimd_additive_increase():
    controller = AIMDConcurrencyController(initial_concurrency=2, max_concurrency=10)
    
    # At concurrency 2, it should take 2 successes to increase
    controller.on_success(latency=0.5)
    assert controller.current_concurrency == 2
    
    controller.on_success(latency=0.5)
    assert controller.current_concurrency == 3
    
    # At concurrency 3, it should take 3 successes to increase
    controller.on_success(latency=0.5)
    controller.on_success(latency=0.5)
    assert controller.current_concurrency == 3
    
    controller.on_success(latency=0.5)
    assert controller.current_concurrency == 4

def test_aimd_multiplicative_decrease(monkeypatch):
    controller = AIMDConcurrencyController(initial_concurrency=8, min_concurrency=2)
    
    # Mock time so we can bypass the cooldown
    class MockTime:
        def __init__(self):
            self.t = 100.0
        def time(self):
            return self.t
            
    mock_time = MockTime()
    monkeypatch.setattr(time, "time", mock_time.time)
    
    # Simulate an overload error
    controller.on_failure("HTTP 429 Too Many Requests")
    assert controller.current_concurrency == 4
    
    # Should not decrease again immediately due to cooldown
    controller.on_failure("HTTP 500 Overloaded")
    assert controller.current_concurrency == 4
    
    # Advance time past cooldown
    mock_time.t += 6.0
    controller.on_failure("Connection timeout")
    assert controller.current_concurrency == 2
    
    # Advance time past cooldown, should hit floor
    mock_time.t += 6.0
    controller.on_failure("Timeout")
    assert controller.current_concurrency == 2

def test_aimd_latency_degradation():
    controller = AIMDConcurrencyController(initial_concurrency=10, latency_threshold_sec=5.0)
    
    # A successful request, but took way too long (over threshold)
    # Should act as a multiplicative decrease
    controller.on_success(latency=6.0)
    
    assert controller.current_concurrency == 5

def test_aimd_ignores_user_errors():
    controller = AIMDConcurrencyController(initial_concurrency=4)
    
    # Normal user errors (e.g. bad request, invalid format) should not scale down capacity
    controller.on_failure("Invalid JSON schema")
    assert controller.current_concurrency == 4

def test_aimd_reset():
    controller = AIMDConcurrencyController(initial_concurrency=4, max_concurrency=10)
    
    # Do some successes
    controller.on_success(latency=0.5)
    controller.on_success(latency=0.5)
    
    # Reset with new limits
    controller.reset(new_initial=12, new_max=16)
    
    assert controller.current_concurrency == 12
    assert controller.max_concurrency == 16
    assert controller._success_count == 0

    # Ensure it respects the new max
    controller.reset(new_initial=20, new_max=16)
    assert controller.current_concurrency == 16
