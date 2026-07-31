"""Load BeatNet in offline mode without requiring microphone support."""

from __future__ import annotations

import importlib.util
import sys
import types


def _install_offline_pyaudio_stub() -> None:
    if importlib.util.find_spec("pyaudio") is not None or "pyaudio" in sys.modules:
        return

    module = types.ModuleType("pyaudio")
    module.paFloat32 = 1

    class PyAudio:
        def __init__(self, *args, **kwargs):
            raise RuntimeError(
                "PyAudio is unavailable. Intent-MGSV uses BeatNet in offline mode; "
                "install PyAudio only if microphone streaming is required."
            )

    module.PyAudio = PyAudio
    sys.modules["pyaudio"] = module


_install_offline_pyaudio_stub()

from BeatNet.BeatNet import BeatNet  # noqa: E402

__all__ = ["BeatNet"]
