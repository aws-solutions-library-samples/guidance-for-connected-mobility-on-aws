import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { RealtimeDataService } from '../RealtimeDataService';

// Minimal WebSocket mock that records constructed URLs and lets tests drive
// lifecycle callbacks.
class MockWebSocket {
  static instances: MockWebSocket[] = [];
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSED = 3;
  url: string;
  readyState = 0;
  onopen: ((e?: any) => void) | null = null;
  onclose: ((e: any) => void) | null = null;
  onerror: ((e: any) => void) | null = null;
  onmessage: ((e: any) => void) | null = null;
  constructor(url: string) {
    this.url = url;
    MockWebSocket.instances.push(this);
  }
  close() {
    this.readyState = MockWebSocket.CLOSED;
    this.onclose?.({ code: 1006, reason: '' });
  }
  send() {}
}

beforeEach(() => {
  MockWebSocket.instances = [];
  (globalThis as any).WebSocket = MockWebSocket as any;
});
afterEach(() => {
  vi.useRealTimers();
});

describe('RealtimeDataService URL construction', () => {
  it('appends token and fleetId to the configured WS endpoint', () => {
    const svc = new RealtimeDataService('wss://abc.execute-api.us-west-2.amazonaws.com/live', {
      tokenProvider: () => 'tok123',
      fleetId: 'fleetA',
    });
    svc.connect().catch(() => {});
    const ws = MockWebSocket.instances[0];
    expect(ws.url.startsWith('wss://abc.execute-api.us-west-2.amazonaws.com/live?')).toBe(true);
    expect(ws.url).toContain('token=tok123');
    expect(ws.url).toContain('fleetId=fleetA');
  });

  it('omits fleetId for an all-fleet (admin) connection', () => {
    const svc = new RealtimeDataService('wss://x/live', { tokenProvider: () => 'tok' });
    svc.connect().catch(() => {});
    expect(MockWebSocket.instances[0].url).toContain('token=tok');
    expect(MockWebSocket.instances[0].url).not.toContain('fleetId=');
  });

  it('connects without a query string when there is no token or fleet', () => {
    const svc = new RealtimeDataService('wss://x/live', { tokenProvider: () => null });
    svc.connect().catch(() => {});
    expect(MockWebSocket.instances[0].url).toBe('wss://x/live');
  });

  it('fetches a FRESH token on reconnect (no stale-URL replay)', () => {
    vi.useFakeTimers();
    let n = 0;
    const tokenProvider = vi.fn(() => `tok${++n}`);
    const svc = new RealtimeDataService('wss://x/live', { tokenProvider, fleetId: 'f' });
    svc.connect().catch(() => {});
    expect(MockWebSocket.instances[0].url).toContain('token=tok1');
    // Simulate an abnormal close → schedules a reconnect.
    MockWebSocket.instances[0].onclose?.({ code: 1006, reason: '' });
    vi.advanceTimersByTime(5000);
    expect(MockWebSocket.instances.length).toBe(2);
    expect(MockWebSocket.instances[1].url).toContain('token=tok2');
  });
});
