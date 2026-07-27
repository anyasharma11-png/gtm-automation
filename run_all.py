"""
run_all.py

Runs the full system in one command:
  1. funnel.py             -- New Prospect Activation motion
                              (Apollo prospecting -> score -> route -> draft)
  2. reengagement_pipeline.py -- Funnel Re-engagement motion
                              (stalled deals -> 3-touch cadence)

These stay as two separate motions internally (different triggers, different
cadences, matching the GTM plan's own structure) but run together here so
there's one command for daily/scheduled use instead of two.

Run: python3 run_all.py
"""

import subprocess
import sys

print("#" * 70)
print("# RUNNING FULL PIPELINE: New Prospect Activation + Re-engagement")
print("#" * 70)

print("\n" + "=" * 70)
print("PART 1: New Prospect Activation (funnel.py)")
print("=" * 70)
result1 = subprocess.run([sys.executable, "funnel.py"])

print("\n" + "=" * 70)
print("PART 2: Funnel Re-engagement (reengagement_pipeline.py)")
print("=" * 70)
result2 = subprocess.run([sys.executable, "reengagement_pipeline.py"])

print("\n" + "#" * 70)
print("# FULL RUN COMPLETE")
print("#" * 70)

if result1.returncode != 0 or result2.returncode != 0:
    print("One or both parts exited with an error -- check the output above.")
    sys.exit(1)
