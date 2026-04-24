"""Prober package for Stage 5 liveness and exit-IP checks."""

from .service import run_probe_cycle

__all__ = ["run_probe_cycle"]
