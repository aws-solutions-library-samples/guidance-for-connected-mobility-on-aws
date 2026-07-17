// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import { useLocalStorage } from '@/components/commons/use-local-storage';
import { SplitPanel } from '@cloudscape-design/components';
import { useState } from 'react';
import Palette from './components/palette';
import { Content } from './content';
import { StoredWidgetPlacement } from './interfaces';

const splitPanelMaxSize = 360;

export default function VehicleDashboardView({ vehicle, notifications }: any) {
  const [splitPanelOpen, setSplitPanelOpen] = useState(false);
  const [splitPanelSize, setSplitPanelSize] = useLocalStorage(
    'React-ConfigurableDashboard-SplitPanelSize',
    360
  );
  const [layout, setLayout, resetLayout] =
    useLocalStorage<ReadonlyArray<StoredWidgetPlacement> | null>(
      'VehicleDashboard-widgets-layout',
      null
    );

  return (
    <>
      <Content
        layout={layout}
        setLayout={setLayout}
        resetLayout={resetLayout}
        setSplitPanelOpen={setSplitPanelOpen}
      />
      <SplitPanel 
        header='Add widgets' 
        closeBehavior='hide' 
        hidePreferencesButton={true}
        onSplitPanelToggle={({ detail }) => setSplitPanelOpen(detail.open)}
        onSplitPanelResize={event =>
          setSplitPanelSize(Math.min(event.detail.size, splitPanelMaxSize))
        }
      >
        <Palette items={[]} />
      </SplitPanel>
    </>
  );
}
