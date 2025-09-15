# Chat Session Clearing Easter Egg

## Overview
A hidden feature that allows users to clear their chat session with the AI agent without logging out and back in.

## How to Use
1. **Double-click** on the application title ("Connected Mobility Fleet Console") in the top navigation bar
2. The chat session will be cleared immediately
3. You'll see a brief green flash on the header as visual confirmation
4. The next time you open the chat agent, it will start with a fresh session

## What It Does
- Removes the `chatSessionId` from browser's sessionStorage
- Resets the chat conversation history
- Creates a new session ID for future chat interactions
- Provides visual feedback via header color change

## Technical Details
- **Trigger**: Double-click event on the TopNavigation wrapper div
- **Storage Key**: `chatSessionId` in sessionStorage
- **Visual Feedback**: Green background flash on header element
- **Console Log**: "🧹 Chat session cleared via easter egg"

## Use Cases
- Testing different conversation flows
- Starting fresh without full logout/login cycle
- Clearing sensitive conversation history
- Development and debugging purposes

## Implementation Files
- `App.tsx`: Contains the `clearChatSession` function and double-click handler
- `ChatAgent.tsx`: Listens for session changes and resets chat state accordingly
