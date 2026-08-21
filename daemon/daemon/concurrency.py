import time
from threading import Lock
from daemon.log import get_logger

logger = get_logger(__name__)

class AIMDConcurrencyController:
    """
    Additive-Increase/Multiplicative-Decrease (AIMD) Adaptive Concurrency Controller.
    
    Like TCP Congestion Control, it dynamically scales the allowed concurrency
    by probing the runtime for capacity, increasing linearly on success and
    backing off multiplicatively on failure or high latency.
    """
    
    def __init__(
        self, 
        initial_concurrency: int = 4, 
        min_concurrency: int = 1, 
        max_concurrency: int = 128,
        latency_threshold_sec: float = 60.0
    ):
        self._current = max(min_concurrency, min(initial_concurrency, max_concurrency))
        self.min_concurrency = min_concurrency
        self.max_concurrency = max_concurrency
        self.latency_threshold_sec = latency_threshold_sec
        
        self._success_count = 0
        self._last_decrease_time = 0.0
        self._decrease_cooldown_sec = 5.0  # Wait before halving again
        self._lock = Lock()

    @property
    def current_concurrency(self) -> int:
        with self._lock:
            return self._current

    def reset(self, new_initial: int, new_max: int) -> None:
        """Reset the controller with new limits (e.g., from runtime capacity discovery)."""
        with self._lock:
            self.max_concurrency = new_max
            self._current = max(self.min_concurrency, min(new_initial, self.max_concurrency))
            self._success_count = 0
            logger.info(f"AIMD reset: concurrency={self._current}, max={self.max_concurrency}")

    def on_success(self, latency: float) -> None:
        """Called when a prompt completes successfully."""
        with self._lock:
            if latency > self.latency_threshold_sec:
                # Treat extremely slow responses as a capacity warning
                self._handle_overload_locked("high_latency")
                return

            self._success_count += 1
            
            # Additive Increase: Require `current` successes to increase by 1
            # (TCP congestion avoidance phase behavior)
            if self._success_count >= self._current:
                if self._current < self.max_concurrency:
                    self._current += 1
                    logger.debug(f"AIMD Increase: concurrency scaled up to {self._current}")
                self._success_count = 0

    def on_failure(self, error_msg: str) -> None:
        """Called when a prompt fails, potentially due to overload."""
        # Only back off on overload/timeout indicators, not user prompt validation errors
        is_overload = any(
            x in error_msg.lower() 
            for x in ["timeout", "429", "500", "overloaded", "capacity", "connection", "rate"]
        )
        if is_overload:
            with self._lock:
                self._handle_overload_locked(error_msg)

    def _handle_overload_locked(self, reason: str) -> None:
        """Multiplicative decrease logic."""
        now = time.time()
        if now - self._last_decrease_time > self._decrease_cooldown_sec:
            old_val = self._current
            self._current = max(self.min_concurrency, self._current // 2)
            self._success_count = 0
            self._last_decrease_time = now
            logger.warning(
                f"AIMD Decrease: concurrency scaled down from {old_val} to {self._current} "
                f"due to {reason}"
            )
