#!/bin/bash
exec aplay -D $1 -f S16_LE -c 1 -r $2 -t raw