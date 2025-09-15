#!/usr/bin/env python3
"""
Consolidate Python dependencies across the workspace
"""
import os
import subprocess
from pathlib import Path

def consolidate_python_deps():
    """Remove duplicate virtual environments and consolidate dependencies"""
    workspace_root = Path(__file__).parent
    
    # Remove duplicate .venv directories (keep root one)
    duplicate_venvs = [
        workspace_root / "lib" / ".venv",
        workspace_root / "modules" / "cms_ui" / ".venv"
    ]
    
    for venv_path in duplicate_venvs:
        if venv_path.exists():
            print(f"Removing duplicate venv: {venv_path}")
            subprocess.run(["rm", "-rf", str(venv_path)])
    
    print("✅ Virtual environment consolidation complete")
    print("💡 Use the root .venv for all Python work")

if __name__ == "__main__":
    consolidate_python_deps()
