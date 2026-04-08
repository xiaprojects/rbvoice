#!/bin/bash
if [ -e "$3" ]; then
exec arecord -D $1 -f S16_LE -c 1 -r $2 -t raw |
  sox -t raw -b 16 -e signed -c 1 -r $2 - \
      -t raw -b 16 -e signed -c 1 -r $2 - \
      noisered $3 0.21
else
    exec arecord -D $1 -f S16_LE -c 1 -r $2 -t raw
fi

