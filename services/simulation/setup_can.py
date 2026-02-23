#!/usr/bin/env python3
"""
Setup script for CMS Fleet Simulator CAN interface.
Detects platform and configures the appropriate virtual CAN bus.

Usage:
    python3 setup_can.py          # Auto-detect and setup
    python3 setup_can.py --check  # Check status only
    python3 setup_can.py --teardown  # Remove vcan interface (Linux only)
"""

import platform
import subprocess
import sys
import shutil
import argparse


REQUIRED_PACKAGES = ['python-can', 'cantools', 'msgpack']
VCAN_INTERFACE = 'vcan0'


def check_python_packages():
    """Check and install required Python packages."""
    missing = []
    for pkg in REQUIRED_PACKAGES:
        import_name = pkg.replace('-', '_').replace('python_can', 'can')
        try:
            __import__(import_name)
        except ImportError:
            missing.append(pkg)

    if missing:
        print(f"📦 Installing missing packages: {', '.join(missing)}")
        subprocess.check_call([sys.executable, '-m', 'pip', 'install'] + missing + ['-q'])
        print("✅ Packages installed")
    else:
        print("✅ All Python packages present")


def setup_linux():
    """Setup vcan0 on Linux."""
    print("🐧 Linux detected — setting up SocketCAN virtual interface")

    # Check if vcan module is available
    result = subprocess.run(['modinfo', 'vcan'], capture_output=True, text=True)
    if result.returncode != 0:
        print("❌ vcan kernel module not available. Install with:")
        print("   sudo apt-get install linux-modules-extra-$(uname -r)")
        return False

    # Load vcan module
    print(f"  Loading vcan kernel module...")
    subprocess.run(['sudo', 'modprobe', 'vcan'], check=True)

    # Check if vcan0 already exists
    result = subprocess.run(['ip', 'link', 'show', VCAN_INTERFACE],
                            capture_output=True, text=True)
    if result.returncode == 0:
        # Already exists — make sure it's up
        subprocess.run(['sudo', 'ip', 'link', 'set', VCAN_INTERFACE, 'up'], check=True)
        print(f"✅ {VCAN_INTERFACE} already exists and is up")
    else:
        # Create it
        subprocess.run(['sudo', 'ip', 'link', 'add', 'dev', VCAN_INTERFACE, 'type', 'vcan'], check=True)
        subprocess.run(['sudo', 'ip', 'link', 'set', VCAN_INTERFACE, 'up'], check=True)
        print(f"✅ {VCAN_INTERFACE} created and up")

    # Verify
    result = subprocess.run(['ip', '-details', 'link', 'show', VCAN_INTERFACE],
                            capture_output=True, text=True)
    print(f"  {result.stdout.strip().splitlines()[0]}")

    # Check for can-utils (optional but useful)
    if shutil.which('candump'):
        print("✅ can-utils available (candump, cansend, etc.)")
    else:
        print("ℹ️  can-utils not installed (optional). Install with: sudo apt-get install can-utils")

    return True


def setup_macos():
    """Setup UDP multicast virtual CAN on macOS."""
    print("🍎 macOS detected — using UDP multicast virtual CAN bus")
    print(f"  Interface: udp_multicast")
    print(f"  Channel: 239.0.0.1")
    print("  No kernel setup needed — works out of the box")

    # Quick test
    try:
        import can
        bus = can.Bus(interface='udp_multicast', channel='239.0.0.1')
        test_msg = can.Message(arbitration_id=0x000, data=b'\x00', is_extended_id=False)
        bus.send(test_msg)
        bus.shutdown()
        print("✅ UDP multicast CAN bus verified")
        return True
    except Exception as e:
        print(f"❌ CAN bus test failed: {e}")
        return False


def setup_windows():
    """Setup UDP multicast virtual CAN on Windows."""
    print("🪟 Windows detected — using UDP multicast virtual CAN bus")
    print(f"  Interface: udp_multicast")
    print(f"  Channel: 239.0.0.1")
    print("  No kernel setup needed — works out of the box")

    try:
        import can
        bus = can.Bus(interface='udp_multicast', channel='239.0.0.1')
        bus.shutdown()
        print("✅ UDP multicast CAN bus verified")
        return True
    except Exception as e:
        print(f"❌ CAN bus test failed: {e}")
        return False


def check_status():
    """Check current CAN interface status."""
    system = platform.system()
    print(f"Platform: {system} ({platform.machine()})")
    print(f"Python: {sys.version.split()[0]}")

    # Check packages
    for pkg in REQUIRED_PACKAGES:
        import_name = pkg.replace('-', '_').replace('python_can', 'can')
        try:
            mod = __import__(import_name)
            ver = getattr(mod, '__version__', '?')
            print(f"  ✅ {pkg} ({ver})")
        except ImportError:
            print(f"  ❌ {pkg} not installed")

    if system == 'Linux':
        result = subprocess.run(['ip', 'link', 'show', VCAN_INTERFACE],
                                capture_output=True, text=True)
        if result.returncode == 0:
            state = 'UP' if 'UP' in result.stdout else 'DOWN'
            print(f"  ✅ {VCAN_INTERFACE}: {state}")
            print(f"  Mode: socketcan")
        else:
            print(f"  ❌ {VCAN_INTERFACE} not found")
    else:
        print(f"  Mode: udp_multicast (239.0.0.1)")

    # Check DBC file
    import os
    dbc_path = os.path.join(os.path.dirname(__file__), 'can', 'cms-fleet.dbc')
    if os.path.exists(dbc_path):
        try:
            import cantools
            db = cantools.database.load_file(dbc_path)
            sig_count = sum(len(m.signals) for m in db.messages)
            print(f"  ✅ DBC: {db.messages.__len__()} messages, {sig_count} signals")
        except Exception as e:
            print(f"  ⚠️ DBC exists but failed to parse: {e}")
    else:
        print(f"  ❌ DBC not found at {dbc_path}")


def teardown_linux():
    """Remove vcan interface on Linux."""
    if platform.system() != 'Linux':
        print("Teardown only applies to Linux")
        return
    try:
        subprocess.run(['sudo', 'ip', 'link', 'set', VCAN_INTERFACE, 'down'], check=True)
        subprocess.run(['sudo', 'ip', 'link', 'delete', VCAN_INTERFACE], check=True)
        print(f"✅ {VCAN_INTERFACE} removed")
    except subprocess.CalledProcessError:
        print(f"ℹ️  {VCAN_INTERFACE} not found or already removed")


def main():
    parser = argparse.ArgumentParser(description='CMS Fleet Simulator CAN Setup')
    parser.add_argument('--check', action='store_true', help='Check status only')
    parser.add_argument('--teardown', action='store_true', help='Remove vcan interface')
    args = parser.parse_args()

    if args.check:
        check_status()
        return

    if args.teardown:
        teardown_linux()
        return

    print("🔧 CMS Fleet Simulator — CAN Interface Setup")
    print("=" * 50)

    # Install packages
    check_python_packages()
    print()

    # Platform-specific setup
    system = platform.system()
    if system == 'Linux':
        ok = setup_linux()
    elif system == 'Darwin':
        ok = setup_macos()
    elif system == 'Windows':
        ok = setup_windows()
    else:
        print(f"❌ Unsupported platform: {system}")
        ok = False

    print()
    if ok:
        print("🎉 Setup complete! Start the simulator with:")
        print("   python3 simulation_api.py")
        print()
        print("Then start a simulation via API:")
        print('   POST /api/simulation/start {"mode": "vcan", ...}')
        print('   POST /api/simulation/start {"mode": "mqtt_direct", ...}')
    else:
        print("❌ Setup incomplete — see errors above")

    print()
    check_status()


if __name__ == '__main__':
    main()
