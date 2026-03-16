#!/usr/bin/env python3
"""Quick smoke test for Nova Act with API key."""

import os
import sys
from pathlib import Path

# Load .env
env_path = Path(__file__).parent / ".env"
if env_path.exists():
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                if line.startswith('export '):
                    line = line[7:]
                key, value = line.split('=', 1)
                os.environ[key] = value

print("=" * 60)
print("NOVA ACT SMOKE TEST")
print("=" * 60)

# Check API key
api_key = os.getenv('NOVA_ACT_API_KEY')
if not api_key:
    print("❌ NOVA_ACT_API_KEY not found in .env")
    sys.exit(1)

print(f"✅ API key found: {api_key[:8]}...{api_key[-4:]}")

def smoke_test():
    """Quick test: navigate to Tabelog and take a screenshot."""
    try:
        from nova_act import NovaAct
        print("\n🚀 Initializing Nova Act...")
        
        # Initialize with API key (SYNC version)
        nova = NovaAct(
            starting_page="https://tabelog.com",
            nova_act_api_key=api_key,
            headless=False  # Show browser so you can see it
        )
        
        print("✅ Nova Act initialized")
        print("\n🌐 Starting browser and navigating to Tabelog.com...")
        
        # Start the session (this navigates to starting_page)
        nova.start()
        print("✅ Browser started and navigated to Tabelog")
        
        # Take a screenshot
        print("📸 Taking screenshot...")
        screenshot = nova.screenshot()
        print(f"✅ Screenshot captured ({len(screenshot)} bytes)")
        
        # Save screenshot
        screenshot_path = Path(__file__).parent / "nova_test_screenshot.png"
        import base64
        with open(screenshot_path, "wb") as f:
            f.write(base64.b64decode(screenshot))
        print(f"✅ Screenshot saved to {screenshot_path}")
        
        # Try a simple action
        print("\n🤖 Testing Nova Act action...")
        result = nova.act("What is the title of this webpage?")
        print(f"✅ Action result: {result}")
        
        # Close
        nova.stop()
        print("\n✅ Browser closed")
        
        print("\n" + "=" * 60)
        print("🎉 SMOKE TEST PASSED!")
        print("=" * 60)
        print("\nNova Act is working correctly!")
        print("Ready to start implementation.")
        
    except Exception as e:
        print(f"\n❌ Smoke test failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    return True

if __name__ == "__main__":
    success = smoke_test()
    sys.exit(0 if success else 1)
