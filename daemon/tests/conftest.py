import pytest


@pytest.fixture(autouse=True)
def _no_host_cuda_mask():
    """A CUDA_VISIBLE_DEVICES set on the test host would filter mocked GPUs.

    A private MonkeyPatch keeps the shared `monkeypatch` fixture's teardown
    after the module fixtures that read what tests patched through it.
    """
    with pytest.MonkeyPatch.context() as mp:
        mp.delenv("CUDA_VISIBLE_DEVICES", raising=False)
        yield
