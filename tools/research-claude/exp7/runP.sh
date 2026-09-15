#!/bin/sh
set -x
for p in Port1_SITE0 Port19_SITE0; do for h in 1000 5000; do python coarse.py --port $p --h $h --variant B; done; done
python coarse.py --port Port18_SITE0 --h 1000 --variant C
