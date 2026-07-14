import React, { useRef, useState, useEffect } from "react";
import "amazon-connect-streams";
import { Container, Header, StatusIndicator, Box } from "@cloudscape-design/components";

export interface ConnectCCPProps {
  connectInstanceUrl: string;
  onContactConnecting?: (contact: connect.Contact) => void;
  onContactConnected?: (contact: connect.Contact) => void;
  onContactEnded?: (contact: connect.Contact) => void;
}

export const ConnectCCP: React.FC<ConnectCCPProps> = ({
  connectInstanceUrl,
  onContactConnecting,
  onContactConnected,
  onContactEnded,
}) => {
  const ccpRef = useRef<HTMLDivElement>(null);
  const [agentState, setAgentState] = useState<string>("Connecting...");
  const [initialized, setInitialized] = useState(false);

  useEffect(() => {
    if (!ccpRef.current || initialized) return;

    try {
      connect.core.initCCP(ccpRef.current, {
        ccpUrl: connectInstanceUrl,
        loginPopup: true,
        loginPopupAutoClose: true,
        loginUrl: connectInstanceUrl.replace('/ccp-v2', ''),
        softphone: { allowFramedSoftphone: true },
        pageOptions: { enableAudioDeviceSettings: true, enablePhoneTypeSettings: true },
      });

      connect.agent((agent) => {
        setAgentState(agent.getState().name);
        agent.onStateChange((stateChange) => {
          setAgentState(stateChange.newState);
        });
      });

      connect.contact((contact) => {
        contact.onConnecting(() => onContactConnecting?.(contact));
        contact.onConnected(() => onContactConnected?.(contact));
        contact.onEnded(() => onContactEnded?.(contact));
      });

      setInitialized(true);
    } catch (e) {
      console.error("Failed to initialize CCP:", e);
      setAgentState("Error - refresh page");
    }
  }, []);

  return (
    <Container header={<Header variant="h2">Contact Control Panel</Header>}>
      <style>{`
        #ccp-container iframe {
          width: 100% !important;
          height: 100% !important;
          border: none !important;
        }
      `}</style>
      <div id="ccp-container" ref={ccpRef} style={{ width: "100%", height: "460px" }} />
      <Box margin={{ top: "s" }}>
        <StatusIndicator type={agentState === "Available" ? "success" : agentState.includes("Error") ? "error" : "loading"}>
          {agentState}
        </StatusIndicator>
      </Box>
    </Container>
  );
};
