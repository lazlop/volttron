import os
import subprocess
import sys
import shutil 

def run_arp_scan(interface):
    """
    Run arp-scan on the specified interface to find all devices on the local network.
    Returns a list of IP addresses.
    """
    try:
        result = subprocess.run(
            ["arp-scan", "--interface", interface, "--localnet"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True
        )
        # Parse IP addresses from the output
        ips = []
        for line in result.stdout.splitlines():
            if line and line[0].isdigit():  # IP lines start with digits
                ip = line.split()[0]
                ips.append(ip)
        return ips
    except subprocess.CalledProcessError as e:
        print(f"Error running arp-scan: {e.stderr}", file=sys.stderr)
        return []

def run_bacnet_scan(ip):
    """
    Run bacnet_scan.py for the given IP address.
    """
    try:
        values = subprocess.run(["python3", "bacnet_scan.py","--address", ip], check=True)
        print(values)
    except subprocess.CalledProcessError as e:
        print(f"Error scanning {ip}: {e}", file=sys.stderr)

def main():
    if len(sys.argv) != 2:
        print("Usage: python network_scan.py <network-interface>", file=sys.stderr)
        sys.exit(1)
    
    interface = sys.argv[1]
    
    # Check if arp-scan is installed
    if not shutil.which("arp-scan"):
        print("Error: arp-scan is not installed. Please install it using your package manager.", file=sys.stderr)
        sys.exit(1)
    
    # Check if bacnet_scan.py exists
    if not os.path.isfile("bacnet_scan.py"):
        print("Error: bacnet_scan.py not found in the current directory.", file=sys.stderr)
        sys.exit(1)
    
    print(f"Scanning the local network on interface {interface}...")
    ips = run_arp_scan(interface)
    
    if not ips:
        print("No devices found on the local network.")
        return
    
    print("Running bacnet_scan.py against the following IPs:")
    for ip in ips:
        print(f"- {ip}")
        run_bacnet_scan(ip)
    
    print("Scanning complete.")

if __name__ == "__main__":
    main()