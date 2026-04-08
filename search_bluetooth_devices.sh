#!/bin/bash

helpFunction()
{
   echo ""
   echo "Usage: $0 -i '20:18:00:01:52:FC'"
   echo "Usage: $0 -i '51:FE:A5:D2:DA:A4'"
   exit 1 # Exit script after printing help
}

while getopts "i:" opt
do
   case "$opt" in
      i ) BTMACIN="$OPTARG" ;;
      ? ) helpFunction ;; # Print helpFunction in case parameter is non-existent
   esac
done

# Print helpFunction in case parameters are empty
if [ -z "$BTMACIN" ]
then
   helpFunction
fi

searchFor()
{
    local mac="$1"
    if bluealsa-aplay -L | grep -qi -- "$mac"; then
        echo "$mac"
        return 0
    else
        return 1
    fi
}

searchAndCheck() {
    local mac="$1"
    if searchFor "$mac"; then
        echo "Found $mac"
    else
        echo "Error: $mac not found in bluealsa-aplay -L output" >&2
        return 1
    fi
}

searchAndCheck "$BTMACIN" || exit 1

