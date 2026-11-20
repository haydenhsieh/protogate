"""Host-side client for ProtoGate hardware."""

from .client import ProtoGate
from . import b822

__all__ = ["ProtoGate", "b822"]
