import React, { useState, useRef, useEffect } from 'react';
import { Box, Button, Container, Header } from '@cloudscape-design/components';
import { useAuth } from '../../auth/useAuth';
import { getVsaApiEndpoint } from '../../config/api';

// Allow only safe inline formatting tags; strip everything else.
const ALLOWED_TAGS = /^(strong|em|h1|h2|h3|li|ul|br)$/i;

// R8 XSS mitigation: the agent `result` text is NOT a trusted source —
// it can echo the user's own prompt, KB content, or free-form tool output,
// any of which can carry attacker-planted attributes. Even on allowed
// tags, inline event-handler attributes (onmouseover, onclick, onfocus,
// tabindex+onfocus, style with javascript:) survive into
// dangerouslySetInnerHTML and fire on user interaction. To prevent this,
// we re-emit allowed tags WITHOUT any attributes — and drop disallowed
// tags entirely.
//
// Exported for unit testing.
export const sanitizeHtml = (html: string): string => {
  return html.replace(/<(\/?)([a-z][a-z0-9]*)\b[^>]*>/gi, (_match, slash, tag) =>
    ALLOWED_TAGS.test(tag) ? `<${slash}${tag.toLowerCase()}>` : ''
  );
};

// Exported for unit testing.
export const formatMarkdown = (text: string): string => {
  const raw = text
    .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.*?)\*/g, '<em>$1</em>')
    .replace(/^# (.*$)/gm, '<h1>$1</h1>')
    .replace(/^## (.*$)/gm, '<h2>$1</h2>')
    .replace(/^### (.*$)/gm, '<h3>$1</h3>')
    .replace(/^\* (.*$)/gm, '<li>$1</li>')
    .replace(/(<li>.*<\/li>)/s, '<ul>$1</ul>')
    .replace(/\n/g, '<br>');
  return sanitizeHtml(raw);
};

interface AssistantResponse {
  result: string;
  sessionId: string;
  classification?: string | null;
  vin?: string | null;
  citations?: Array<string | { source: string; score?: number }>;
}

const DEMO_CHIPS = [
  'My brake warning light just came on and the pedal feels soft.',
  'What\'s the most common DTC across our fleet this month?',
  'Book service for this vehicle next Tuesday.',
];

const STYLES = `
  @keyframes ellipsis {
    0% { content: '.'; }
    33% { content: '..'; }
    66% { content: '...'; }
  }
  @keyframes bounce {
    0%, 80%, 100% { transform: translateY(0); opacity: 0.5; }
    40% { transform: translateY(-6px); opacity: 1; }
  }
  .cvx-dot {
    display: inline-block;
    width: 7px; height: 7px;
    border-radius: 50%;
    background: #666;
    margin: 0 2px;
    animation: bounce 1.2s infinite ease-in-out;
  }
  .cvx-dot:nth-child(2) { animation-delay: 0.2s; }
  .cvx-dot:nth-child(3) { animation-delay: 0.4s; }
`;

function formatTime(d: Date): string {
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

export const ChatAgent: React.FC<{ onClose?: () => void; vin?: string }> = ({ onClose, vin }) => {
  const [messages, setMessages] = useState<string[]>([]);
  const [citations, setCitations] = useState<string[][]>([]);
  const [timestamps, setTimestamps] = useState<Date[]>([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [sessionId, setSessionId] = useState(() => {
    const existing = sessionStorage.getItem('chatSessionId');
    if (existing) return existing;
    const next = `session_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
    sessionStorage.setItem('chatSessionId', next);
    return next;
  });
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const auth = useAuth();

  const vsaEndpoint = getVsaApiEndpoint();

  useEffect(() => {
    if (!auth.isAuthenticated) {
      sessionStorage.removeItem('chatSessionId');
    }
  }, [auth.isAuthenticated]);

  useEffect(() => {
    const handleStorageChange = () => {
      const current = sessionStorage.getItem('chatSessionId');
      if (!current && sessionId) {
        setMessages([]);
        setInput('');
        setCitations([]);
        setTimestamps([]);
        const next = `session_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
        sessionStorage.setItem('chatSessionId', next);
        setSessionId(next);
        console.log('🔄 Chat session reset');
      }
    };
    window.addEventListener('storage', handleStorageChange);
    const interval = setInterval(handleStorageChange, 1000);
    return () => {
      window.removeEventListener('storage', handleStorageChange);
      clearInterval(interval);
    };
  }, [sessionId]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, loading]);

  const send = async (overrideText?: string) => {
    const text = (overrideText ?? input).trim();
    if (!text || loading) return;

    if (!vsaEndpoint) {
      setMessages(prev => [...prev, 'Agent: Assistant is not available in this environment.']);
      setCitations(prev => [...prev, []]);
      setTimestamps(prev => [...prev, new Date()]);
      return;
    }

    setLoading(true);
    setMessages(prev => [...prev, `You: ${text}`]);
    setCitations(prev => [...prev, []]);
    setTimestamps(prev => [...prev, new Date()]);
    if (!overrideText) setInput('');

    try {
      const base = vsaEndpoint.endsWith('/') ? vsaEndpoint.slice(0, -1) : vsaEndpoint;
      const body: Record<string, unknown> = { prompt: text, sessionId };
      if (vin) body.vin = vin;

      const res = await fetch(`${base}/assistant/chat`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...auth.getAuthHeaders(),
        },
        body: JSON.stringify(body),
      });

      if (!res.ok) {
        const resText = await res.text().catch(() => '');
        console.warn(`ChatAgent: HTTP ${res.status}`, resText.slice(0, 200));
        setMessages(prev => [...prev, 'Error: Something went wrong — please try again in a moment.']);
        setCitations(prev => [...prev, []]);
        setTimestamps(prev => [...prev, new Date()]);
        return;
      }

      const data: AssistantResponse = await res.json();
      const result = typeof data.result === 'string' ? data.result : JSON.stringify(data.result ?? '');
      setMessages(prev => [...prev, `Agent: ${result}`]);
      setCitations(prev => [...prev, (data.citations ?? []).map(c => typeof c === 'string' ? c : c.source)]);
      setTimestamps(prev => [...prev, new Date()]);
      if (data.sessionId && data.sessionId !== sessionId) {
        setSessionId(data.sessionId);
        sessionStorage.setItem('chatSessionId', data.sessionId);
      }
    } catch (err) {
      setMessages(prev => [...prev, 'Error: Could not reach the assistant — check your connection and retry.']);
      setCitations(prev => [...prev, []]);
      setTimestamps(prev => [...prev, new Date()]);
    } finally {
      setLoading(false);
    }
  };

  // Build demo chips, substituting VIN in the booking chip when available
  const chips = DEMO_CHIPS.map((c, i) =>
    i === 2 && vin ? c.replace('this vehicle', vin) : c
  );

  let agentMsgCount = 0;

  if (!vsaEndpoint) {
    return (
      <Container>
        <Header variant="h3" actions={onClose && <Button variant="icon" iconName="close" onClick={onClose} />}>
          CVX Assistant
        </Header>
        <Box padding="l" color="text-status-inactive">
          Assistant is not available in this environment.
        </Box>
      </Container>
    );
  }

  return (
    <Container>
      <style>{STYLES}</style>
      <div style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
        {/* Header */}
        <div style={{ display: 'flex', alignItems: 'center', padding: '12px 16px', borderBottom: '1px solid #e0e0e0', gap: '10px' }}>
          <div style={{
            width: 36, height: 36, borderRadius: '50%',
            background: 'linear-gradient(135deg, #0073bb 0%, #00a1c9 100%)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            color: '#fff', fontWeight: 700, fontSize: 15, flexShrink: 0,
          }}>
            C
          </div>
          <div style={{ flex: 1 }}>
            <div style={{ fontWeight: 600, fontSize: 15 }}>CVX Assistant</div>
            <div style={{ fontSize: 12, color: '#666' }}>Connected Vehicle Experience</div>
          </div>
          {onClose && <Button variant="icon" iconName="close" onClick={onClose} />}
        </div>

        {/* Message area */}
        <div style={{ flex: 1, overflowY: 'auto', padding: '16px', maxHeight: 400 }}>

          {/* Welcome / empty state */}
          {messages.length === 0 && !loading && (
            <div style={{ textAlign: 'center', padding: '24px 16px' }}>
              <div style={{ fontSize: 32, marginBottom: 8 }}>🚗</div>
              <div style={{ fontWeight: 600, fontSize: 15, marginBottom: 4 }}>How can I help with your fleet today?</div>
              <div style={{ fontSize: 13, color: '#666', marginBottom: 20 }}>
                Ask about vehicle health, diagnostics, or book a service appointment.
              </div>
              {/* Suggested chips */}
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8, alignItems: 'center' }}>
                {chips.map((chip, i) => (
                  <button
                    key={i}
                    onClick={() => { setInput(chip); send(chip); }}
                    style={{
                      maxWidth: 420, width: '100%',
                      padding: '8px 14px', borderRadius: 18,
                      border: '1px solid #0073bb', background: '#f0f8ff',
                      color: '#0073bb', fontSize: 13, cursor: 'pointer',
                      textAlign: 'left', lineHeight: 1.4,
                      transition: 'background 0.15s',
                    }}
                    onMouseOver={e => (e.currentTarget.style.background = '#dceefb')}
                    onMouseOut={e => (e.currentTarget.style.background = '#f0f8ff')}
                  >
                    {chip}
                  </button>
                ))}
              </div>
            </div>
          )}

          {/* Messages */}
          {messages.map((msg, i) => {
            const safeMsg = typeof msg === 'string' ? msg : JSON.stringify(msg);
            const isAgent = safeMsg.startsWith('Agent:');
            const isError = safeMsg.startsWith('Error:');
            const content = isAgent
              ? safeMsg.substring(7)
              : safeMsg.substring(safeMsg.indexOf(':') + 2);

            let msgCitations: string[] = [];
            if (isAgent) {
              msgCitations = citations[agentMsgCount] ?? [];
              agentMsgCount++;
            }

            const ts = timestamps[i];

            return (
              <div key={i} style={{ marginBottom: 4 }}>
                {/* Agent label */}
                {isAgent && (
                  <div style={{ fontSize: 11, color: '#888', marginBottom: 3, paddingLeft: 2 }}>CVX Assistant</div>
                )}
                <div style={{ display: 'flex', justifyContent: isAgent || isError ? 'flex-start' : 'flex-end', marginBottom: 2 }}>
                  <div style={{
                    maxWidth: '75%',
                    padding: '10px 14px',
                    borderRadius: isAgent || isError ? '4px 18px 18px 18px' : '18px 4px 18px 18px',
                    backgroundColor: isError
                      ? '#fce8e6'
                      : isAgent
                      ? '#f0f4f8'
                      : '#0073bb',
                    color: isAgent || isError ? '#1a1a1a' : '#fff',
                    fontSize: 14, lineHeight: 1.5, wordBreak: 'break-word',
                    boxShadow: '0 1px 3px rgba(0,0,0,0.08)',
                  }}>
                    {isAgent ? (
                      <div dangerouslySetInnerHTML={{ __html: formatMarkdown(content) }} />
                    ) : (
                      <div>{content}</div>
                    )}
                  </div>
                </div>
                {/* Timestamp */}
                {ts && (
                  <div style={{ fontSize: 11, color: '#aaa', textAlign: isAgent || isError ? 'left' : 'right', paddingLeft: isAgent ? 2 : 0, paddingRight: isAgent ? 0 : 2, marginBottom: 10 }}>
                    {formatTime(ts)}
                  </div>
                )}
                {msgCitations.length > 0 && (
                  <div style={{ paddingLeft: 4, marginBottom: 10 }}>
                    <span style={{ fontSize: 11, color: '#666', fontWeight: 600 }}>Sources: </span>
                    {msgCitations.map((c, ci) => (
                      <span key={ci} style={{ fontSize: 11, color: '#666', marginRight: 6 }}>{c}</span>
                    ))}
                  </div>
                )}
              </div>
            );
          })}

          {/* Thinking indicator */}
          {loading && (
            <div style={{ marginBottom: 4 }}>
              <div style={{ fontSize: 11, color: '#888', marginBottom: 3, paddingLeft: 2 }}>CVX Assistant</div>
              <div style={{ display: 'flex', justifyContent: 'flex-start', marginBottom: 12 }}>
                <div style={{
                  padding: '12px 16px', borderRadius: '4px 18px 18px 18px',
                  backgroundColor: '#f0f4f8', boxShadow: '0 1px 3px rgba(0,0,0,0.08)',
                  display: 'flex', alignItems: 'center', gap: 4,
                }}>
                  <span style={{ fontSize: 13, color: '#555', marginRight: 4 }}>Assistant is thinking</span>
                  <span className="cvx-dot" />
                  <span className="cvx-dot" />
                  <span className="cvx-dot" />
                </div>
              </div>
            </div>
          )}

          <div ref={messagesEndRef} />
        </div>

        {/* Input area */}
        <div style={{ padding: '12px 16px', borderTop: '1px solid #e0e0e0', display: 'flex', flexDirection: 'column', gap: 8 }}>
          <div style={{ display: 'flex', gap: 8, alignItems: 'flex-end' }}>
            <textarea
              value={input}
              onChange={e => setInput(e.target.value)}
              onKeyDown={e => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault();
                  if (!loading && input.trim()) send();
                }
              }}
              disabled={loading}
              rows={2}
              placeholder="Ask about your vehicle, safety events, or fleet data…"
              style={{
                flex: 1, padding: '8px 12px',
                border: '1px solid #ccc', borderRadius: 8,
                fontSize: 14, fontFamily: 'inherit',
                resize: 'none', outline: 'none',
                opacity: loading ? 0.6 : 1,
                transition: 'border-color 0.15s',
              }}
              onFocus={e => { e.currentTarget.style.borderColor = '#0073bb'; }}
              onBlur={e => { e.currentTarget.style.borderColor = '#ccc'; }}
            />
            <Button variant="primary" onClick={() => send()} disabled={loading || !input.trim()}>
              {loading ? 'Sending…' : 'Send'}
            </Button>
          </div>
          {loading && (
            <div style={{ fontSize: 12, color: '#888', textAlign: 'center' }}>
              Response may take up to 25s on first request…
            </div>
          )}
        </div>
      </div>
    </Container>
  );
};
