import React, { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import {
  Container,
  Header,
  SpaceBetween,
  Box,
  ExpandableSection,
  Badge,
  KeyValuePairs,
  StatusIndicator,
  Button,
  Link,
} from "@cloudscape-design/components";

export interface ConversationTurn {
  role: "customer" | "assistant";
  content: string;
  timestamp: string;
}

export interface HandoverContext {
  contactId: string;
  customerId?: string;
  customerName?: string;
  vin?: string;
  intent?: string;
  summary?: string;
  sentiment?: string;
  transcript: ConversationTurn[];
}

interface HandoverContextPanelProps {
  contact: connect.Contact | null;
  onViewVehicle?: (vin: string) => void;
}

function extractHandoverContext(contact: connect.Contact): HandoverContext {
  const attrs = contact.getAttributes();
  const transcript: ConversationTurn[] = [];

  const rawTranscript = attrs?.["nova_sonic_transcript"]?.value;
  if (rawTranscript) {
    try {
      const parsed = JSON.parse(rawTranscript);
      if (Array.isArray(parsed)) {
        transcript.push(...parsed);
      }
    } catch {
      transcript.push({ role: "assistant", content: rawTranscript, timestamp: new Date().toISOString() });
    }
  }

  return {
    contactId: contact.getContactId(),
    customerId: attrs?.["driverId"]?.value || attrs?.["customer_id"]?.value,
    customerName: attrs?.["driverName"]?.value || attrs?.["customer_name"]?.value,
    vin: attrs?.["vin"]?.value,
    intent: attrs?.["reason"]?.value || attrs?.["detected_intent"]?.value,
    summary: attrs?.["summary"]?.value || attrs?.["conversation_summary"]?.value,
    sentiment: attrs?.["severity"]?.value || attrs?.["customer_sentiment"]?.value,
    transcript,
  };
}

export const HandoverContextPanel: React.FC<HandoverContextPanelProps> = ({ contact, onViewVehicle }) => {
  const [context, setContext] = useState<HandoverContext | null>(null);
  const navigate = useNavigate();

  useEffect(() => {
    if (!contact) {
      setContext(null);
      return;
    }
    setContext(extractHandoverContext(contact));
  }, [contact]);

  if (!context) {
    return (
      <Container header={<Header variant="h2">Handover Context</Header>}>
        <Box textAlign="center" color="text-status-inactive" padding="l">
          No active contact — waiting for incoming call from Nova Sonic.
        </Box>
      </Container>
    );
  }

  return (
    <Container
      header={
        <Header
          variant="h2"
          actions={
            context.vin ? (
              <Button variant="primary" onClick={() => navigate(`/vehicles/management/${context.vin}`)}>
                View Vehicle
              </Button>
            ) : undefined
          }
        >
          Nova Sonic Handover
        </Header>
      }
    >
      <SpaceBetween size="m">
        <KeyValuePairs
          columns={3}
          items={[
            { label: "Customer", value: context.customerName || (context.customerId ? `Driver (${context.customerId})` : "Driver") },
            {
              label: "VIN",
              value: context.vin ? (
                <Link onFollow={() => navigate(`/vehicles/management/${context.vin}`)}>{context.vin}</Link>
              ) : "—",
            },
            { label: "Intent", value: context.intent ? <Badge color="blue">{context.intent}</Badge> : "—" },
            {
              label: "Severity",
              value: context.sentiment ? (
                <StatusIndicator
                  type={context.sentiment === "P0" ? "error" : context.sentiment === "P1" ? "warning" : "info"}
                >
                  {context.sentiment}
                </StatusIndicator>
              ) : "—",
            },
          ]}
        />

        {context.summary && (
          <Box variant="p">
            <strong>AI Summary:</strong> {context.summary}
          </Box>
        )}

        <ExpandableSection headerText={`Conversation transcript (${context.transcript.length} turns)`} defaultExpanded>
          <SpaceBetween size="xs">
            {context.transcript.map((turn, i) => (
              <Box
                key={i}
                padding="xs"
                variant="div"
                color={turn.role === "customer" ? "text-body-secondary" : "text-status-info"}
              >
                <strong>{turn.role === "customer" ? "🧑 Customer" : "🤖 Nova Sonic"}:</strong>{" "}
                {turn.content}
              </Box>
            ))}
          </SpaceBetween>
        </ExpandableSection>
      </SpaceBetween>
    </Container>
  );
};
