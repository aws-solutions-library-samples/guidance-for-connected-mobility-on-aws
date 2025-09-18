#!/usr/bin/env python3
"""
Parallel Integration - Run IoT and Flink integrations simultaneously
"""

import subprocess
import threading
import os
import sys

def run_script(script_name, description):
    """Run integration script and capture output"""
    try:
        print(f"🚀 Starting {description}...")
        result = subprocess.run([
            'python3', script_name
        ], capture_output=True, text=True, cwd=os.path.dirname(__file__))
        
        if result.returncode == 0:
            print(f"✅ {description} completed")
            return True
        else:
            print(f"❌ {description} failed: {result.stderr}")
            return False
    except Exception as e:
        print(f"❌ {description} error: {e}")
        return False

def main():
    deployment_stage = os.environ.get('DEPLOYMENT_STAGE', 'dev')
    print(f"🔄 Running parallel integration for {deployment_stage}")
    
    # Run both integrations in parallel
    results = {}
    threads = []
    
    def run_iot_integration():
        results['iot'] = run_script('complete_integration.py', 'IoT-MSK Integration')
    
    def run_flink_integration():
        results['flink'] = run_script('working_integration.py', 'Flink-MSK Integration')
    
    # Start both threads
    iot_thread = threading.Thread(target=run_iot_integration)
    flink_thread = threading.Thread(target=run_flink_integration)
    
    iot_thread.start()
    flink_thread.start()
    
    # Wait for completion
    iot_thread.join()
    flink_thread.join()
    
    # Check results
    success = all(results.values())
    
    if success:
        print("🎉 All integrations completed successfully!")
    else:
        print("❌ Some integrations failed")
        sys.exit(1)

if __name__ == "__main__":
    main()
