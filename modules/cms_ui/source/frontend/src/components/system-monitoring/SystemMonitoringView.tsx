import React from 'react';
import { Container, Tabs } from '@cloudscape-design/components';
import DeviceStatusOverview from '../iot/DeviceStatusOverview';
import DeviceClientList from '../iot/DeviceClientList';
import DeviceTopicList from '../iot/DeviceTopicList';
import DeviceSubscriptionList from '../iot/DeviceSubscriptionList';
import DeviceRetainMessageList from '../iot/DeviceRetainMessageList';
import DeviceAlarmList from '../iot/DeviceAlarmList';
import DeviceRuleList from '../iot/DeviceRuleList';
import DeviceLogTrace from '../iot/DeviceLogTrace';
import OTAManagement from '../iot/OTAManagement';

const SystemMonitoringView: React.FC = () => {
  const [activeTabId, setActiveTabId] = React.useState('overview');

  const tabs = [
    {
      id: 'overview',
      label: 'Status Overview',
      content: <DeviceStatusOverview />
    },
    {
      id: 'connections',
      label: 'Connected Devices',
      content: <DeviceClientList />
    },
    {
      id: 'topics',
      label: 'Communication Channels',
      content: <DeviceTopicList />
    },
    {
      id: 'subscriptions',
      label: 'Subscriptions',
      content: <DeviceSubscriptionList />
    },
    {
      id: 'messages',
      label: 'Message Analytics',
      content: <DeviceRetainMessageList />
    },
    {
      id: 'alarms',
      label: 'System Alerts',
      content: <DeviceAlarmList />
    },
    {
      id: 'rules',
      label: 'Rules & Processing',
      content: <DeviceRuleList />
    },
    {
      id: 'logs',
      label: 'Diagnostics & Logging',
      content: <DeviceLogTrace />
    },
    {
      id: 'ota',
      label: 'OTA Updates',
      content: <OTAManagement />
    }
  ];

  return (
    <Container>
      <Tabs
        activeTabId={activeTabId}
        onChange={({ detail }) => setActiveTabId(detail.activeTabId)}
        tabs={tabs}
      />
    </Container>
  );
};

export default SystemMonitoringView;
