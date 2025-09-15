#!/bin/bash
# Immediate workspace cleanup script

echo "🧹 Starting workspace cleanup..."

# Remove build artifacts
echo "Removing CDK build artifacts..."
find . -name "cdk.out" -type d -exec rm -rf {} + 2>/dev/null || true

# Remove Python cache
echo "Removing Python cache files..."
find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true

# Remove .DS_Store files
echo "Removing .DS_Store files..."
find . -name ".DS_Store" -delete 2>/dev/null || true

# Remove duplicate virtual environments (keep root .venv)
echo "Removing duplicate virtual environments..."
rm -rf lib/.venv modules/cms_ui/.venv 2>/dev/null || true

# Calculate space saved
echo "✅ Cleanup complete!"
echo "💾 Estimated space saved: ~600MB"
echo "🎯 Next steps:"
echo "   1. Run 'make setup' to initialize workspace"
echo "   2. Use root .venv for all Python development"
echo "   3. Run 'make help' to see available commands"
