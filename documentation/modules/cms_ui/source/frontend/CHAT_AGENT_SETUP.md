# Fleet Chat Agent Integration

## Overview
This integration adds a chat agent to the help sidebar of the Connected Mobility Fleet Console, powered by Amazon Bedrock AgentCore Runtime.

## Setup Instructions

### 1. Install Dependencies
```bash
cd /path/to/workspace/modules/fleet-manager/source/frontend
yarn add @aws-sdk/client-bedrock-agent-runtime
```

### 2. Configure Agent ARN
Update the agent ARN in `ChatAgent.tsx` if needed:
```typescript
agentArn = 'arn:aws:bedrock-agentcore:us-east-1:470296731304:runtime/kb_gateway_strands_agent_new-YhydBo5coC'
```

### 3. Ensure AWS Credentials
The chat agent uses the existing authentication system. Ensure your AWS credentials have permissions for:
- `bedrock-agentcore:InvokeAgent`
- Access to the specific agent ARN

### 4. Test the Integration
1. Start the development server: `yarn start`
2. Navigate to any page in the application
3. Click the chat icon (contact icon) in the page header
4. The chat agent should open in the right sidebar

## Features

### Chat Interface
- **Conversational UI**: Clean chat interface with user/assistant message bubbles
- **Session Management**: Each chat session gets a unique ID for conversation continuity
- **Real-time Responses**: Streaming responses from the Bedrock agent
- **Error Handling**: Graceful error handling with user-friendly messages

### Agent Capabilities
Based on the notebook analysis, the agent can help with:
- Safety events and alerts analysis
- Vehicle performance data queries
- Fleet management insights
- Customer feedback analysis
- Maintenance recommendations
- Data visualization and trends

### Integration Points
- **Help Button**: Accessible from every page via the header help icon
- **Sidebar Panel**: Uses existing Cloudscape AppLayout tools panel
- **Authentication**: Leverages existing AWS authentication
- **Responsive**: Works across different screen sizes

## Architecture

### Components
- `ChatAgent.tsx`: Main chat interface component
- `help-panel.tsx`: Updated to include chat agent hooks
- `App.tsx`: Integration with AppLayout tools panel

### AWS Services
- **Bedrock AgentCore Runtime**: Powers the conversational AI
- **Knowledge Base**: Provides fleet-specific information
- **Memory Service**: Maintains conversation context

### Security
- Uses existing AWS credentials from authentication system
- Agent ARN is configurable for different environments
- Session IDs are unique per chat instance

## Customization

### Styling
The chat interface uses Cloudscape Design System components and can be customized by modifying the styles in `ChatAgent.tsx`.

### Agent Configuration
- Update `agentArn` prop to use different agents
- Modify `sessionId` generation for different session strategies
- Customize welcome message and capabilities list

### Error Handling
Error messages can be customized in the `invokeAgent` function catch blocks.

## Troubleshooting

### Common Issues
1. **Authentication Errors**: Ensure AWS credentials have Bedrock permissions
2. **Agent Not Responding**: Verify the agent ARN is correct and active
3. **Network Issues**: Check AWS region configuration matches agent deployment

### Debug Mode
Enable console logging by uncommenting debug statements in `ChatAgent.tsx`.

## Future Enhancements
- Message history persistence
- File upload support
- Voice input/output
- Custom agent training
- Multi-language support
