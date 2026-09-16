#!/bin/sh
export PYTHONPATH="$(cd "$(dirname "$0")/../../.." && pwd)/src"
set -x
python run9.py --port Port18_SITE0 --freqs few
python run9.py --port Port7_SITE0 --freqs few
python run9.py --port Port14_SITE0 --freqs ladder --loops
python run9.py --port Port16_SITE0 --freqs few
(ulimit -v 6000000; python run9.py --port Port19_SITE0 --freqs few --gnd s3 --loops)
(ulimit -v 6000000; python run9.py --port Port19_SITE0 --freqs few --gnd s3scaled --loops)
python run9.py --port Port19_SITE0 --freqs ladder --nonewidth 10
python run9.py --port Port19_SITE0 --freqs ladder --nonewidth 60
python run9.py --port Port19_SITE0 --freqs ladder --nonewidth drop
