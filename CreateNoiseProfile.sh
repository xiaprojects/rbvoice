#!/bin/bash
# This script is used to create the SOX profile for Noise reduction
# Noise reduction is automatially applied if the file exists
# Noise reduction is usefull when you have a alternator noise or engine noise to be removed
# Using a good COMM setup and good headphones there is no need to apply this
for i in 16000 22500 32000 44100; do
arecord -f S16_LE -r $i -c 1 -t wav -d 3 $1/noise-${i}.wav
sox -c 1 $1/noise-${i}.wav -n noiseprof $1/noise-${i}.prof
done

