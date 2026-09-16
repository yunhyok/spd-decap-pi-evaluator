#!/bin/sh
export PYTHONPATH="$(cd "$(dirname "$0")/../../.." && pwd)/src"
python run9.py --port Port19_SITE0 --freqs few --mesh h100_isl50
python run9.py --port Port19_SITE0 --freqs few --mesh h100
python run9.py --port Port19_SITE0 --freqs few --loops > "${SPD_PI_WORK_DIR:-/home/claude/work}/exp9/loops19w.log" 2>&1
