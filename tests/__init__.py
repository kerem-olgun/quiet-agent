"""Unit tests. They use fakes for the audio layer, so they change no sound and run on any OS.

Run them from the repository root with:  python -m unittest -v
"""
import os
import sys

_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)
