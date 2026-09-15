#!/bin/sh
set -x
python run6.py --port Port18_SITE0 --stats
python run6.py --port Port18_SITE0
for D in 200 400 1500; do python run6.py --port Port18_SITE0 --dmax $D; done
