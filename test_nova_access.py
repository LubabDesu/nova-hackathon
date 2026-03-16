#!/usr/bin/env python3
"""Test if Nova Act is accessible with current AWS credentials."""

import os
import sys
from pathlib import Path

# Load .env (handle export prefix)
env_path = Path(__file__).parent / ".env"
if env_path.exists():
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                # Remove 'export ' prefix if present
                if line.startswith('export '):
                    line = line[7:]
                key, value = line.split('=', 1)
                os.environ[key] = value

print("=" * 60)
print("NOVA ACT ACCESS TEST")
print("=" * 60)

# Check AWS credentials
credentials_set = all([
    os.getenv('AWS_ACCESS_KEY_ID'),
    os.getenv('AWS_SECRET_ACCESS_KEY'),
    os.getenv('AWS_DEFAULT_REGION')
])

if credentials_set:
    print("✅ AWS credentials found")
    print(f"   Region: {os.getenv('AWS_DEFAULT_REGION')}")
else:
    print("❌ AWS credentials missing")
    sys.exit(1)

# Try to import and use Nova Act
try:
    print("\n📦 Checking Nova Act SDK...")
    from nova_act import NovaAct
    print("✅ Nova Act SDK installed")
    
    # Try to initialize (this will validate credentials)
    print("\n🔑 Testing AWS access for Nova Act...")
    print("   (This may take a moment...)")
    
    import asyncio
    
    async def test_access():
        try:
            # Just initialize, don't actually browse
            browser = NovaAct(headless=True)
            await browser.start()
            await browser.stop()
            return True, None
        except Exception as e:
            return False, str(e)
    
    success, error = asyncio.run(test_access())
    
    if success:
        print("✅ Nova Act is accessible!")
        print("\n" + "=" * 60)
        print("You're all set to build the Japan restaurant booking!")
        print("=" * 60)
    else:
        print(f"❌ Nova Act access failed: {error}")
        print("\n" + "=" * 60)
        print("DIAGNOSTIC STEPS:")
        print("=" * 60)
        print("1. Check if Bedrock is enabled in your AWS account:")
        print("   https://us-east-1.console.aws.amazon.com/bedrock/home")
        print("\n2. Request Nova Act access at:")
        print("   https://nova.amazon.com/act")
        print("\n3. Verify your IAM user has Bedrock permissions:")
        print("   - bedrock:InvokeModel")
        print("   - bedrock:InvokeModelWithResponseStream")
        print("   - nova:StartActSession")
        
except ImportError as e:
    print(f"❌ Nova Act SDK import error: {e}")
    print("\nInstall with:")
    print("   cd backend && pip install nova-act")
    
except Exception as e:
    print(f"❌ Unexpected error: {e}")
