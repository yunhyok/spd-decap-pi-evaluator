#!/bin/sh
set -x
for h in 5000 2000 1000 500 250; do python coarse.py --port Port18_SITE0 --h $h --variant B; done
