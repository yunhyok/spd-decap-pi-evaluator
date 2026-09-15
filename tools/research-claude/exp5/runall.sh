#!/bin/sh
# EXP-5 run order (sequential, memory-bounded)
set -x
python pipeline.py --tag 260729 --port Port18_SITE0 --freqset sweep
python pipeline.py --tag 260804 --port Port18_SITE0 --freqset sweep
for p in Port16_SITE0 Port19_SITE0 Port14_SITE0 Port7_SITE0 Port1_SITE0; do
  python pipeline.py --tag 260729 --port $p --freqset ladder
done
