// Shared ESLint configuration for the workspace
module.exports = {
  root: true,
  extends: [
    'eslint:recommended',
    '@typescript-eslint/recommended',
    'react-app',
    'react-app/jest'
  ],
  parser: '@typescript-eslint/parser',
  plugins: ['@typescript-eslint'],
  rules: {
    // Add workspace-wide linting rules
    'no-console': 'warn',
    '@typescript-eslint/no-unused-vars': 'error'
  }
};
