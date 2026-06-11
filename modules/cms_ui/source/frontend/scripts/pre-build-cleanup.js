#!/usr/bin/env node
/**
 * Pre-build cleanup script
 * Removes hardcoded endpoints and ensures clean build environment
 */

import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

console.log('🧹 Running pre-build cleanup...');

// Files to remove (no longer needed)
const filesToRemove = [
  'public/api-config.js',
  'test-api-config.js'
];

// Directories to clean
const directoriesToClean = [
  'build',
  'dist',
  '.vite',
  'node_modules/.cache'
];

// Environment files to validate
const envFiles = [
  '.env.local',
  '.env.development', 
  '.env.apigateway'
];

// Old endpoint pattern to detect
const OLD_ENDPOINT_PATTERN = /hdme9a5hwe\.execute-api\.us-east-1\.amazonaws\.com/g;

function removeFile(filePath) {
  if (fs.existsSync(filePath)) {
    fs.unlinkSync(filePath);
    console.log(`✅ Removed: ${filePath}`);
  }
}

function removeDirectory(dirPath) {
  if (fs.existsSync(dirPath)) {
    fs.rmSync(dirPath, { recursive: true, force: true });
    console.log(`✅ Cleaned: ${dirPath}`);
  }
}

function validateFile(filePath) {
  if (fs.existsSync(filePath)) {
    const content = fs.readFileSync(filePath, 'utf8');
    if (OLD_ENDPOINT_PATTERN.test(content)) {
      console.log(`⚠️  WARNING: Old endpoint found in ${filePath}`);
      return false;
    }
  }
  return true;
}

// Change to frontend directory
const frontendDir = path.resolve(__dirname, '..');
process.chdir(frontendDir);

// 1. Remove unnecessary files
console.log('\n📁 Removing unnecessary files...');
filesToRemove.forEach(removeFile);

// 2. Clean build directories
console.log('\n🗂️  Cleaning build directories...');
directoriesToClean.forEach(removeDirectory);

// 3. Validate environment files
console.log('\n🔍 Validating environment files...');
let allValid = true;
envFiles.forEach(file => {
  if (!validateFile(file)) {
    allValid = false;
  }
});

// 4. Build process is ready
console.log('\n✅ Build preparation completed');

// 5. Create/update .gitignore to prevent committing build artifacts
const gitignoreContent = `
# Build artifacts that should not be committed
build/
dist/
.vite/
node_modules/.cache/

# Environment files with potentially sensitive data
.env.local
.env.production

# Old API config files (no longer needed)
public/api-config.js
test-api-config.js
`;

const gitignorePath = '.gitignore';
if (!fs.existsSync(gitignorePath)) {
  fs.writeFileSync(gitignorePath, gitignoreContent);
  console.log('✅ Created .gitignore');
} else {
  const existingContent = fs.readFileSync(gitignorePath, 'utf8');
  if (!existingContent.includes('public/api-config.js')) {
    fs.appendFileSync(gitignorePath, '\n# Prevent old API config files\npublic/api-config.js\ntest-api-config.js\n');
    console.log('✅ Updated .gitignore');
  }
}

if (allValid) {
  console.log('\n✅ Pre-build cleanup completed successfully!');
  process.exit(0);
} else {
  console.log('\n❌ Pre-build cleanup found issues. Please fix before building.');
  process.exit(1);
}
