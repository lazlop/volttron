#!/bin/bash

# Check if the user provided an interface as an argument
if [ -z "$1" ]; then
    echo "Usage: $0 <network-interface>"
    exit 1
fi

INTERFACE=$1

# Check if arp-scan is installed
if ! command -v arp-scan &> /dev/null; then
    echo "Error: arp-scan is not installed. Install it using your package manager (e.g., apt, yum)."
    exit 1
fi

if [ ! -f "bacnet_scan.py" ]; then
    echo "Error: bacnet_scan.py not found in the current directory."
    exit 1
fi

# Run arp-scan to get all IPs on the local network
echo "Scanning the local network on interface $INTERFACE..."
IP_LIST=$(arp-scan --interface=$INTERFACE --localnet | awk '/^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+/ {print $1}')

if [ -z "$IP_LIST" ]; then
    echo "No devices found on the local network."
    exit 0
fi

# Run bacnet_scan.py against each IP
echo "Running bacnet_scan.py against each IP..."
for IP in $IP_LIST; do
    echo "Scanning IP: $IP"
    python bacnet_scan.py --address "$IP"
done

echo "Scanning complete."
