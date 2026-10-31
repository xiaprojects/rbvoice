#!/bin/bash

source "$(dirname $0)/activate"
# Example to search USB Audio
DEVINDEX=$(aplay -l|grep USB|cut -f1 -d:|cut -f2 -d " ")
# HW USB Dongle
#DEVOUT="plughw:${DEVINDEX},0"
# If you want to share the Audio with Chromium or Waydroid you shall set the .asoundrc
DEVOUT="default"
DEV="plughw:${DEVINDEX},0"

# Example using Bluetooth MAC
BTMACIN="20:18:00:01:52:FC"
BTMACOUT="51:FE:A5:D2:DA:A4"

#DEV="bluealsa:DEV=${BTMACIN},PROFILE=sco"
#DEVOUT="bluealsa:DEV=${BTMACOUT},PROFILE=a2dp"

wait_for_bt_input() {
    local mac="$1"
    while ! "$(dirname $0)/search_bluetooth_devices.sh" -i "$mac" >/dev/null 2>&1; do
        echo "Waiting for Bluetooth device $mac..."
        bluetoothctl <<< "power on; agent on; discoverable on; connect ${mac};"
        sleep 1
    done
    echo "Bluetooth device $mac found. Continuing..."
}

#wait_for_bt_input "$BTMACIN"
#wait_for_bt_input "$BTMACOUT"

# Example of remote server
SERVER="http://192.168.1.30"
SERVER="http://192.168.10.1"

# VOSK Models: https://alphacephei.com/vosk/models
# Piper Models: https://huggingface.co/rhasspy/piper-voices/tree/main/fr/fr_FR

# Example for FRENCH Language
# Full model for 4GB RAM
VOSKMODEL=$(dirname $0)/../vosk-models/vosk-model-fr-0.22
# Small model for 2GB RAM
#VOSKMODEL=$(dirname $0)/../vosk-models/vosk-model-small-fr-0.22
PIPERONNX=$(dirname $0)/../piper-models/fr_FR-mls-medium.onnx
PIPERCONF=$(dirname $0)/../piper-models/fr_FR-mls-medium.onnx.json                
CASESPATH=/boot/firmware/rb/rbvoice-cases.fr.json

# Example for ENGLISH Language
# make vosk-model MODEL=vosk-model-en-us-0.22 && make piper-model PIPER_VOICE=en/en_GB/alan/medium/en_GB-alan-medium
VOSKMODEL=$(dirname $0)/../vosk-models/vosk-model-en-us-0.22
#VOSKMODEL=$(dirname $0)/../vosk-models/vosk-model-small-en-us-0.15
PIPERONNX=$(dirname $0)/../piper-models/en_GB-alan-medium.onnx
PIPERCONF=$(dirname $0)/../piper-models/en_GB-alan-medium.onnx.json
CASESPATH=/boot/firmware/rb/rbvoice-cases.en.json

# Example for SPANISH Language
# make vosk-model MODEL=vosk-model-es-0.42 && make piper-model PIPER_VOICE=es/es_ES/davefx/medium/es_ES-davefx-medium
VOSKMODEL=$(dirname $0)/../vosk-models/vosk-model-es-0.42
#VOSKMODEL=$(dirname $0)/../vosk-models/vosk-model-small-es-0.42
PIPERONNX=$(dirname $0)/../piper-models/es_ES-davefx-medium.onnx
PIPERCONF=$(dirname $0)/../piper-models/es_ES-davefx-medium.onnx.json
CASESPATH=/boot/firmware/rb/rbvoice-cases.es.json

# Example for GERMAN Language
# make vosk-model MODEL=vosk-model-de-0.21 && make piper-model PIPER_VOICE=de/de_DE/thorsten/medium/de_DE-thorsten-medium
VOSKMODEL=$(dirname $0)/../vosk-models/vosk-model-de-0.21
#VOSKMODEL=$(dirname $0)/../vosk-models/vosk-model-small-de-0.15
PIPERONNX=$(dirname $0)/../piper-models/de_DE-thorsten-medium.onnx
PIPERCONF=$(dirname $0)/../piper-models/de_DE-thorsten-medium.onnx.json
CASESPATH=/boot/firmware/rb/rbvoice-cases.de.json

# Example for ITALIAN Language
VOSKMODEL=$(dirname $0)/../vosk-models/vosk-model-it-0.22
#VOSKMODEL=$(dirname $0)/../vosk-models/vosk-model-small-it-0.22
PIPERONNX=$(dirname $0)/../piper-models/it_IT-paola-medium.onnx
PIPERCONF=$(dirname $0)/../piper-models/it_IT-paola-medium.onnx.json
CASESPATH=/boot/firmware/rb/rbvoice-cases.it.json

# The last language block above wins
RECPATH=/opt/stratux/www/playback/
# External pipe-play.sh and pipe-rec.sh
PATH=$(dirname $0):$PATH

exec python $(dirname $0)/rbvoice.py --threshold 450 --rec $RECPATH --server $SERVER --out "$DEVOUT" --dev "$DEV" --model $VOSKMODEL --piper-model $PIPERONNX --piper-json $PIPERCONF --cases $CASESPATH

