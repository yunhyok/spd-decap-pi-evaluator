#!/bin/sh
export PYTHONPATH=/home/claude/spd-decap-pi-evaluator/src
python run9.py --port Port19_SITE0 --freqs few --mesh h100_isl50
python run9.py --port Port19_SITE0 --freqs few --mesh h100
python run9.py --port Port19_SITE0 --freqs few --loops > /home/claude/work/exp9/loops19w.log 2>&1
