import React, { useState, useRef, useEffect } from 'react';
import { Box, Button, Container, Header, Textarea } from '@cloudscape-design/components';
import { useSimpleAuth } from '../../auth/SimpleAuthProvider';
import { fromCognitoIdentityPool } from '@aws-sdk/credential-provider-cognito-identity';
import { CognitoIdentityClient } from '@aws-sdk/client-cognito-identity';

const formatMarkdown = (text: string) => {
  return text
    .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.*?)\*/g, '<em>$1</em>')
    .replace(/^# (.*$)/gm, '<h1>$1</h1>')
    .replace(/^## (.*$)/gm, '<h2>$1</h2>')
    .replace(/^### (.*$)/gm, '<h3>$1</h3>')
    .replace(/^\* (.*$)/gm, '<li>$1</li>')
    .replace(/(<li>.*<\/li>)/s, '<ul>$1</ul>')
    .replace(/\n/g, '<br>');
};

export const ChatAgent: React.FC<{ onClose?: () => void }> = ({ onClose }) => {
  const [messages, setMessages] = useState<string[]>([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [streamingText, setStreamingText] = useState('');
  const [sessionId, setSessionId] = useState(() => {
    // Get existing session ID from sessionStorage or create new one
    const existingSessionId = sessionStorage.getItem('chatSessionId');
    if (existingSessionId) {
      return existingSessionId;
    }
    const newSessionId = `session_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
    sessionStorage.setItem('chatSessionId', newSessionId);
    return newSessionId;
  });
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const simpleAuth = useSimpleAuth();

  // Clear session ID when user logs out
  useEffect(() => {
    // If user becomes unauthenticated, clear the session ID
    if (!simpleAuth.isAuthenticated) {
      sessionStorage.removeItem('chatSessionId');
    }
  }, [simpleAuth.isAuthenticated]);

  // Listen for session clearing events (easter egg)
  useEffect(() => {
    const handleStorageChange = () => {
      const currentSessionId = sessionStorage.getItem('chatSessionId');
      if (!currentSessionId && sessionId) {
        // Session was cleared, reset chat state
        setMessages([]);
        setInput('');
        setStreamingText('');
        const newSessionId = `session_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
        sessionStorage.setItem('chatSessionId', newSessionId);
        setSessionId(newSessionId);
        console.log('🔄 Chat session reset');
      }
    };

    // Listen for storage changes
    window.addEventListener('storage', handleStorageChange);
    
    // Also check periodically in case of same-tab changes
    const interval = setInterval(handleStorageChange, 1000);

    return () => {
      window.removeEventListener('storage', handleStorageChange);
      clearInterval(interval);
    };
  }, [sessionId]);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages, streamingText]);

  const send = async () => {
    if (!input.trim() || loading) return;
    
    setLoading(true);
    setMessages(prev => [...prev, `You: ${input}`]);
    const prompt = input;
    setInput('');
    setStreamingText('');

    try {
      const { BedrockAgentCoreClient, InvokeAgentRuntimeCommand } = await import('@aws-sdk/client-bedrock-agentcore');
      
      const runtimeConfig = (window as any).runtimeConfig?.awsCredentials;
      const credentials = runtimeConfig?.identityPoolId && simpleAuth.idToken ? 
        fromCognitoIdentityPool({
          client: new CognitoIdentityClient({ region: 'us-east-1' }),
          identityPoolId: runtimeConfig.identityPoolId,
          logins: { [`cognito-idp.us-east-1.amazonaws.com/${runtimeConfig.userPoolId}`]: simpleAuth.idToken }
        }) : undefined;

      const client = new BedrockAgentCoreClient({ region: 'us-east-1', credentials });
      const response = await client.send(new InvokeAgentRuntimeCommand({
        agentRuntimeArn: "arn:aws:bedrock-agentcore:us-east-1:470296731304:runtime/kb_gateway_strands_agent_new-YhydBo5coC",
        qualifier: "DEFAULT",
        payload: JSON.stringify({ prompt, actor_id: "user", session_id: sessionId })
      }));

      let text = 'No response';
      if (response.response) {
        if (response.response instanceof ReadableStream) {
          const reader = response.response.getReader();
          const decoder = new TextDecoder();
          let accumulatedText = '';
          
          while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            
            const chunk = decoder.decode(value, { stream: true });
            const lines = chunk.split('\n');
            
            for (const line of lines) {
              if (line.startsWith('data: ')) {
                try {
                  const jsonStr = line.substring(6);
                  const data = JSON.parse(jsonStr);
                  
                  if (data.type === 'text_delta' && data.text) {
                    accumulatedText += data.text;
                    setStreamingText(accumulatedText);
                  } else if (data.type === 'assistant_message' && data.message?.content?.[0]?.text) {
                    accumulatedText = data.message.content[0].text;
                    setStreamingText(accumulatedText);
                  } else if (data.type === 'event' && data.event?.contentBlockDelta?.delta?.text) {
                    accumulatedText += data.event.contentBlockDelta.delta.text;
                    setStreamingText(accumulatedText);
                  }
                } catch (e) {
                  // Skip invalid JSON lines
                }
              }
            }
          }
          
          text = accumulatedText || 'No response received';
        } else {
          text = String(response.response);
        }
        text = text.replace(/^"|"$/g, '').replace(/\\n/g, '\n');
      }
      setMessages(prev => [...prev, `Agent: ${text}`]);
      setStreamingText('');
    } catch (err) {
      setMessages(prev => [...prev, `Error: ${err}`]);
      setStreamingText('');
    } finally {
      setLoading(false);
    }
  };

  return (
    <Container>
      <style>
        {`
          @keyframes blink {
            0%, 50% { opacity: 1; }
            51%, 100% { opacity: 0; }
          }
        `}
      </style>
      <div style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
        <Header 
          variant="h3" 
          actions={onClose && <Button variant="icon" iconName="close" onClick={onClose} />}
        >
          Virtual Fleet Manager
        </Header>
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', padding: '16px' }}>
          <div style={{ 
            flex: 1,
            overflow: 'auto', 
            padding: '12px', 
            marginBottom: '12px', 
            maxHeight: '400px'
          }}>
            {messages.map((msg, i) => {
              const isAgent = msg.startsWith('Agent:');
              const content = isAgent ? msg.substring(7) : msg.substring(msg.indexOf(':') + 2);
              return (
                <div key={i} style={{ 
                  display: 'flex', 
                  justifyContent: isAgent ? 'flex-start' : 'flex-end',
                  marginBottom: '12px'
                }}>
                  <div style={{
                    maxWidth: '70%',
                    padding: '12px 16px',
                    borderRadius: '18px',
                    backgroundColor: isAgent ? '#e5e5ea' : '#007aff',
                    color: isAgent ? '#000' : '#fff',
                    fontSize: '14px',
                    lineHeight: '1.4',
                    wordWrap: 'break-word'
                  }}>
                    {isAgent ? (
                      <div dangerouslySetInnerHTML={{ __html: formatMarkdown(content) }} />
                    ) : (
                      <div>{content}</div>
                    )}
                  </div>
                </div>
              );
            })}
            {streamingText && (
              <div style={{ 
                display: 'flex', 
                justifyContent: 'flex-start',
                marginBottom: '12px'
              }}>
                <div style={{
                  maxWidth: '70%',
                  padding: '12px 16px',
                  borderRadius: '18px',
                  backgroundColor: '#e5e5ea',
                  color: '#000',
                  fontSize: '14px',
                  lineHeight: '1.4',
                  wordWrap: 'break-word',
                  border: '2px solid #007aff'
                }}>
                  <div dangerouslySetInnerHTML={{ __html: formatMarkdown(streamingText) }} />
                  <span style={{ animation: 'blink 1s infinite' }}>▋</span>
                </div>
              </div>
            )}
            {loading && !streamingText && (
              <div style={{ 
                display: 'flex', 
                justifyContent: 'flex-start',
                marginBottom: '12px'
              }}>
                <div style={{
                  padding: '12px 16px',
                  borderRadius: '18px',
                  backgroundColor: '#e5e5ea',
                  color: '#666',
                  fontSize: '14px',
                  fontStyle: 'italic'
                }}>
                  Agent is thinking...
                </div>
              </div>
            )}
            <div ref={messagesEndRef} />
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
            <textarea 
              value={input} 
              onChange={e => setInput(e.target.value)} 
              onKeyDown={e => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault();
                  if (!loading && input.trim()) {
                    send();
                  }
                }
              }}
              rows={3} 
              placeholder="Ask about safety events, fleet data, or vehicle analytics... (Press Enter to send, Shift+Enter for new line)"
              style={{
                width: '100%',
                padding: '8px',
                border: '1px solid #ccc',
                borderRadius: '4px',
                fontSize: '14px',
                fontFamily: 'inherit',
                resize: 'vertical'
              }}
            />
            <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
              <Button variant="primary" onClick={send} disabled={loading}>
                {loading ? 'Sending...' : 'Send'}
              </Button>
            </div>
          </div>
        </div>
      </div>
    </Container>
  );
};
