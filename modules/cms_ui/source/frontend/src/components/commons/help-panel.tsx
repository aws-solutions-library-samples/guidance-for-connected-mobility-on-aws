// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React, { createContext, useContext } from "react";
import { ChatAgent } from "./ChatAgent";

const HelpPanelContext = createContext<
  ((newContent: React.ReactNode) => void) | null
>(null);

export const HelpPanelProvider = HelpPanelContext.Provider;

export function useHelpPanel() {
  const ctx = useContext(HelpPanelContext);
  if (!ctx) {
    throw new Error("Missing HelpPanelProvider");
  }
  return ctx;
}

export function useChatAgent() {
  const setHelpContent = useHelpPanel();
  
  return {
    /**
     * Open the assistant. `initialPrompt` makes it answer immediately instead of
     * opening an empty box — the dashboard's Fleet Intelligence panel passes a
     * suggested question so a click is a question, not a text field.
     */
    openChat: (initialPrompt?: string) => {
      setHelpContent(<ChatAgent initialPrompt={initialPrompt} />);
    },
    closeChat: () => {
      setHelpContent(null);
    }
  };
}
