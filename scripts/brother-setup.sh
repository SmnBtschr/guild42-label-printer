#!/bin/bash
# brother-setup.sh
# Prepares the Brother QL-820NWBc USB connection on boot.
# Called by systemd as ExecStartPre before the Flask app starts.
#
# The ipp-usb daemon grabs the USB device on connect and prevents
# direct kernel access via /dev/usb/lp0. This script stops ipp-usb,
# reloads the usblp kernel module, and waits for the device node.

set -e

echo "[brother-setup] Stopping ipp-usb..."
systemctl stop ipp-usb 2>/dev/null || true

echo "[brother-setup] Reloading usblp kernel module..."
modprobe -r usblp 2>/dev/null || true
sleep 2
modprobe usblp

echo "[brother-setup] Waiting for /dev/usb/lp0..."
sleep 2

if [ -e /dev/usb/lp0 ]; then
    echo "[brother-setup] Device ready at /dev/usb/lp0."
else
    echo "[brother-setup] WARNING: /dev/usb/lp0 not found. Printer may not be connected."
fi
