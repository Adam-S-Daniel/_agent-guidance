#!/usr/bin/env python3
"""PLACEHOLDER check: exit 0 only when greet.py prints the expected greeting.

Replace with a check that tests the outcome your skill should produce.
Standard library only; the scorer runs it with a minimal environment.
"""
import subprocess
import sys

done = subprocess.run([sys.executable, "greet.py"], capture_output=True, text=True, timeout=30)
sys.exit(0 if done.returncode == 0 and done.stdout.strip() == "Hello, world!" else 1)
