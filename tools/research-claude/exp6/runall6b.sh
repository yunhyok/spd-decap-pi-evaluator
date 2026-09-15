#!/bin/sh
set -x
python run6.py --port Port18_SITE0 --stats
python run6.py --port Port1_SITE0 --stats
python run6.py --port Port19_SITE0 --stats
for p in Port1_SITE0 Port19_SITE0; do
  python run6.py --port $p
  python run6.py --port $p --dmax 400
  python run6.py --port $p --dmax 1500
done
