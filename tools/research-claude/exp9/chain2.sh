#!/bin/sh
# EXP-9 run chain (diagnostics). PIDs waited on are the runs already in flight when this was started.
export PYTHONPATH=/home/claude/spd-decap-pi-evaluator/src
while kill -0 6719 2>/dev/null; do sleep 5; done
python run9.py --port Port19_SITE0 --freqs few --nonewidth 60
python run9.py --port Port19_SITE0 --freqs few --nonewidth drop
while kill -0 6584 2>/dev/null; do sleep 10; done
(ulimit -v 6500000; python run9.py --port Port19_SITE0 --freqs few --gnd s3 --loops)
(ulimit -v 6500000; python run9.py --port Port19_SITE0 --freqs few --gnd s3scaled --loops)
python run9.py --port Port14_SITE0 --freqs few --loops
python run9.py --port Port16_SITE0 --freqs few
