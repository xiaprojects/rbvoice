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
VOSKMODEL=$(dirname $0)/../vosk-models/vosk-model-it-0.22
PIPERONNX=$(dirname $0)/../piper-models/it_IT-paola-medium.onnx
PIPERCONF=$(dirname $0)/../piper-models/it_IT-paola-medium.onnx.json
RECPATH=/opt/stratux/www/playback/
CASESPATH=/boot/firmware/rb/rbvoice-cases.it.json
# External pipe-play.sh and pipe-rec.sh
PATH=$(dirname $0):$PATH

exec python $(dirname $0)/rbvoice.py --rec $RECPATH --server $SERVER --out "$DEVOUT" --dev "$DEV" --model $VOSKMODEL --piper-model $PIPERONNX --piper-json $PIPERCONF --cases $CASESPATH

