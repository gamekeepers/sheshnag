"""
GPU Worker Daemon — Distributed Batch AI Compute Platform

A lightweight polling daemon that connects to the central control plane,
claims batch inference jobs, executes them via vLLM, and uploads results.
"""

__version__ = "0.1.0"
__author__ = "Vatsal-nk"
