"""
CAN Bus Writer — sends CAN frames to a bus interface.
Auto-detects platform: socketcan on Linux, udp_multicast on macOS.
"""

import can
import os
import platform
import subprocess
import sys
from typing import List, Optional


def detect_interface() -> tuple:
    """Detect best available CAN interface for this platform.
    Returns (interface_type, channel)."""
    system = platform.system()

    if system == 'Linux':
        # Check if vcan0 exists
        try:
            result = subprocess.run(['ip', 'link', 'show', 'vcan0'],
                                    capture_output=True, text=True)
            if result.returncode == 0:
                return 'socketcan', 'vcan0'
        except FileNotFoundError:
            pass
        # vcan0 doesn't exist — try to create it automatically
        print("⚠️  vcan0 not found, attempting to create...")
        if setup_vcan('vcan0'):
            return 'socketcan', 'vcan0'
        print("⚠️  vcan0 creation failed. Run setup_vcan() or:")
        print("    sudo modprobe vcan && sudo ip link add dev vcan0 type vcan && sudo ip link set vcan0 up")
        return 'socketcan', 'vcan0'

    # macOS / other — use UDP multicast (cross-platform virtual CAN)
    return 'udp_multicast', '239.0.0.1'


def setup_vcan(interface: str = 'vcan0') -> bool:
    """Set up virtual CAN interface on Linux. Uses sudo if not root, direct if NET_ADMIN."""
    if platform.system() != 'Linux':
        print(f"ℹ️  vcan not available on {platform.system()}, using udp_multicast instead")
        return False
    try:
        is_root = os.getuid() == 0
        prefix = [] if is_root else ['sudo']

        # modprobe may not be in container — skip if not found (host kernel has vcan on ECS AMI)
        try:
            subprocess.run(prefix + ['modprobe', 'vcan'], check=False, capture_output=True)
        except FileNotFoundError:
            pass  # modprobe not available in container, vcan module loaded on host

        subprocess.run(prefix + ['ip', 'link', 'add', 'dev', interface, 'type', 'vcan'],
                       check=True, capture_output=True)
        subprocess.run(prefix + ['ip', 'link', 'set', interface, 'up'], check=True)
        print(f"✅ {interface} created and up")
        return True
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or b'').decode()
        if 'File exists' in stderr:
            try:
                subprocess.run(prefix + ['ip', 'link', 'set', interface, 'up'], check=True)
                print(f"✅ {interface} already exists, brought up")
                return True
            except: pass
        print(f"❌ Failed to create {interface}: {e} stderr={stderr}")
        return False


class CANBusWriter:
    def __init__(self, interface: str = None, channel: str = None):
        """Initialize CAN bus writer.
        If interface/channel not specified, auto-detects platform."""
        if interface is None:
            interface, default_channel = detect_interface()
            channel = channel or default_channel

        self.interface = interface
        self.channel = channel
        self.bus = None

    def open(self):
        """Open the CAN bus connection. Creates vcan interface if needed."""
        print(f"🔌 Opening CAN bus: interface={self.interface}, channel={self.channel}")
        if self.interface == 'socketcan' and self.channel.startswith('vcan'):
            setup_vcan(self.channel)
        self.bus = can.Bus(interface=self.interface, channel=self.channel,
                          receive_own_messages=(self.interface == 'socketcan'))
        print(f"✅ CAN bus open")
        return self

    def close(self):
        """Close the CAN bus connection."""
        if self.bus:
            self.bus.shutdown()
            self.bus = None
            print("🔌 CAN bus closed")

    def send(self, frames: List[can.Message]):
        """Send a list of CAN frames to the bus."""
        if not self.bus:
            raise RuntimeError("Bus not open. Call open() first.")
        for frame in frames:
            self.bus.send(frame)

    def send_one(self, frame: can.Message):
        """Send a single CAN frame."""
        if not self.bus:
            raise RuntimeError("Bus not open. Call open() first.")
        self.bus.send(frame)

    def receive(self, timeout: float = 1.0) -> Optional[can.Message]:
        """Receive a single CAN frame (for testing/monitoring)."""
        if not self.bus:
            raise RuntimeError("Bus not open. Call open() first.")
        return self.bus.recv(timeout=timeout)

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *args):
        self.close()


if __name__ == '__main__':
    # Quick self-test
    iface, ch = detect_interface()
    print(f"Platform: {platform.system()}")
    print(f"Interface: {iface}, Channel: {ch}")

    with CANBusWriter() as writer:
        # Send a test frame
        test_frame = can.Message(arbitration_id=0x100, data=b'\x01\x02\x03\x04',
                                 is_extended_id=False)
        writer.send_one(test_frame)
        print(f"📤 Sent test frame: {test_frame}")

        # Try to receive it back
        rx = writer.receive(timeout=1.0)
        if rx:
            print(f"📥 Received: {rx}")
        else:
            print("⏳ No frame received (expected on some interfaces)")
