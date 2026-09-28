// SPDX-License-Identifier: Apache-2.0

/**
 * Connect CCP opt-in: issue 2026-09-24-connect-login-popup-on-every-page-load.
 *
 * `initCCP` opens the Amazon Connect login popup whenever there is no live Connect
 * session. It used to run on mount for every connect-agent user, so a login window
 * popped up on every page load. The property under test is that the CCP starts ONLY
 * after the agent opts in (clicking the offline pill, or opening Agent Workspace),
 * and exactly once per page, owned by the app-wide panel.
 */

import React from "react";
import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("amazon-connect-streams", () => ({}));

import { CCPPanel } from "../CCPPanel";
import { ConnectCCP } from "../ConnectCCP";
import { CONNECT_OPT_IN_KEY, ConnectProvider } from "../ConnectContext";

let initCCP: ReturnType<typeof vi.fn>;
let setState: ReturnType<typeof vi.fn>;

function installFakeConnect() {
  const core: any = { initialized: false };
  initCCP = vi.fn(() => {
    core.initialized = true;
  });
  core.initCCP = initCCP;
  setState = vi.fn();
  const agent = {
    getName: () => "agent",
    getState: () => ({ name: "Available" }),
    onStateChange: vi.fn(),
    getAgentStates: () => [
      { type: "routable", name: "Available" },
      { type: "offline", name: "Offline" },
    ],
    setState,
  };
  (window as any).connect = {
    core,
    agent: vi.fn((cb: (a: any) => void) => cb(agent)),
    contact: vi.fn(),
    ContactType: { CHAT: "chat" },
    AgentStateType: { OFFLINE: "offline" },
  };
  // ConnectCCP references the global `connect` directly.
  (globalThis as any).connect = (window as any).connect;
}

const offlinePill = () => screen.getByRole("button", { name: /offline — go online/i });

beforeEach(() => {
  window.sessionStorage.clear();
  installFakeConnect();
});

afterEach(() => {
  window.sessionStorage.clear();
});

describe("CCPPanel opt-in", () => {
  it("does NOT start Connect on mount when the agent has not opted in", () => {
    render(
      <ConnectProvider panelEnabled>
        <CCPPanel />
      </ConnectProvider>,
    );
    expect(initCCP).not.toHaveBeenCalled();
    expect(offlinePill()).toBeInTheDocument();
    expect(window.sessionStorage.getItem(CONNECT_OPT_IN_KEY)).toBeNull();
  });

  it("starts Connect once when the agent clicks the offline pill, and remembers it", async () => {
    render(
      <ConnectProvider panelEnabled>
        <CCPPanel />
      </ConnectProvider>,
    );
    await userEvent.click(offlinePill());
    expect(initCCP).toHaveBeenCalledTimes(1);
    expect(initCCP.mock.calls[0][1]).toMatchObject({ loginPopup: true });
    expect(window.sessionStorage.getItem(CONNECT_OPT_IN_KEY)).toBe("1");
  });

  it("reconnects on mount when the agent already opted in this session", () => {
    window.sessionStorage.setItem(CONNECT_OPT_IN_KEY, "1");
    render(
      <ConnectProvider panelEnabled>
        <CCPPanel />
      </ConnectProvider>,
    );
    expect(initCCP).toHaveBeenCalledTimes(1);
  });

  it("treats any other stored value as not opted in", () => {
    window.sessionStorage.setItem(CONNECT_OPT_IN_KEY, "true");
    render(
      <ConnectProvider panelEnabled>
        <CCPPanel />
      </ConnectProvider>,
    );
    expect(initCCP).not.toHaveBeenCalled();
  });

  it("Go offline sets the Connect agent Offline and forgets the opt-in", async () => {
    window.sessionStorage.setItem(CONNECT_OPT_IN_KEY, "1");
    render(
      <ConnectProvider panelEnabled>
        <CCPPanel />
      </ConnectProvider>,
    );
    await userEvent.click(screen.getByRole("button", { name: /open fleet support agent panel/i }));
    await userEvent.click(screen.getByRole("button", { name: /go offline/i }));
    expect(setState).toHaveBeenCalledTimes(1);
    expect(setState.mock.calls[0][0]).toMatchObject({ type: "offline" });
    expect(window.sessionStorage.getItem(CONNECT_OPT_IN_KEY)).toBeNull();
    expect(offlinePill()).toBeInTheDocument();
  });
});

describe("Agent Workspace opt-in", () => {
  it("opening Agent Workspace starts Connect exactly once, in the app-wide panel", async () => {
    let ccpPanelContainer: Element | undefined;
    initCCP.mockImplementation((container: Element) => {
      (window as any).connect.core.initialized = true;
      ccpPanelContainer = container;
    });
    // Same tree order as App.tsx: the routed workspace renders BEFORE the panel,
    // so its effects run first. That ordering is what makes delegation racy.
    await act(async () => {
      render(
        <ConnectProvider panelEnabled>
          <ConnectCCP connectInstanceUrl="https://example.invalid/ccp-v2" />
          <CCPPanel />
        </ConnectProvider>,
      );
    });
    expect(initCCP).toHaveBeenCalledTimes(1);
    expect(ccpPanelContainer?.closest('[aria-label="Amazon Connect agent panel"]')).not.toBeNull();
    expect(window.sessionStorage.getItem(CONNECT_OPT_IN_KEY)).toBe("1");
  });

  it("falls back to its own CCP when no app-wide panel exists", () => {
    render(
      <ConnectProvider panelEnabled={false}>
        <ConnectCCP connectInstanceUrl="https://example.invalid/ccp-v2" />
      </ConnectProvider>,
    );
    expect(initCCP).toHaveBeenCalledTimes(1);
  });
});
