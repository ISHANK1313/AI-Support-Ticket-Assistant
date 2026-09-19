from types import SimpleNamespace
import subprocess
import pytest
from src.provider import run_provider


def test_hard_deadline_kills_and_reaps_worker(monkeypatch):
    class Worker:
        returncode = -1
        killed = False
        calls = 0
        def communicate(self, *args, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise subprocess.TimeoutExpired('worker', 0.01)
            return '', ''
        def kill(self): self.killed = True
    worker = Worker()
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **kw: worker)
    settings = SimpleNamespace(gemini_api_key='test', gemini_model='test', gemini_embedding_model='test')
    with pytest.raises(TimeoutError):
        run_provider('generate', settings, 'prompt', timeout=0.01)
    assert worker.killed and worker.calls == 2


def test_sdk_failure_is_generic(monkeypatch):
    class Worker:
        returncode = 1
        def communicate(self, *a, **kw): return 'private provider detail', ''
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **kw: Worker())
    settings = SimpleNamespace(gemini_api_key='test', gemini_model='test')
    with pytest.raises(RuntimeError, match='^Provider unavailable$'):
        run_provider('generate', settings, 'prompt')
