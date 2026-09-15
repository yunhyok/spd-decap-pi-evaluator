#!/bin/sh
set -x
python run8.py --tag 260729 --port Port18_SITE0
python run8.py --tag 260804 --port Port18_SITE0
for p in Port14_SITE0 Port1_SITE0 Port19_SITE0 Port16_SITE0 Port7_SITE0; do python run8.py --tag 260729 --port $p; done
