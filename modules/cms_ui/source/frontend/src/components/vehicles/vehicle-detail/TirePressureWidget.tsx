import React from 'react';
import { Box, Container, Header, StatusIndicator } from '@cloudscape-design/components';

interface TirePressureData {
  tire_fl?: number;  // Front Left
  tire_fr?: number;  // Front Right
  tire_rl?: number;  // Rear Left
  tire_rr?: number;  // Rear Right
  tire_temp_max?: number;  // Max tire temperature
}

interface TirePressureWidgetProps {
  tirePressure: TirePressureData;
  lastUpdated?: string;
}

const TirePressureWidget: React.FC<TirePressureWidgetProps> = ({ tirePressure, lastUpdated }) => {
  const getTireStatus = (pressure: number | undefined): 'success' | 'warning' | 'error' => {
    if (!pressure) return 'error';
    if (pressure < 28) return 'error';    // Low pressure
    if (pressure < 30) return 'warning';  // Warning range
    return 'success';                     // Normal range (30-35 PSI)
  };

  const formatPressure = (pressure: number | undefined): string => {
    return pressure ? `${pressure.toFixed(1)} PSI` : 'N/A';
  };

  return (
    <Container
      header={
        <Header variant="h3" description="Current tire pressure readings">
          Tire Pressure Monitor
        </Header>
      }
    >
      <div style={{ position: 'relative', width: '100%', maxWidth: '300px', margin: '0 auto' }}>
        {/* Vehicle outline (top view) */}
        <svg
          width="100%"
          height="200"
          viewBox="0 0 200 300"
          style={{ display: 'block' }}
        >
          {/* Vehicle body */}
          <rect
            x="60"
            y="40"
            width="80"
            height="220"
            rx="15"
            ry="15"
            fill="none"
            stroke="#232f3e"
            strokeWidth="2"
          />
          
          {/* Front windshield */}
          <path
            d="M 70 50 L 130 50 L 125 65 L 75 65 Z"
            fill="none"
            stroke="#232f3e"
            strokeWidth="1"
          />
          
          {/* Tires */}
          {/* Front Left */}
          <rect x="35" y="60" width="20" height="40" rx="10" ry="10" fill="#545b64" stroke="#232f3e" strokeWidth="1" />
          {/* Front Right */}
          <rect x="145" y="60" width="20" height="40" rx="10" ry="10" fill="#545b64" stroke="#232f3e" strokeWidth="1" />
          {/* Rear Left */}
          <rect x="35" y="200" width="20" height="40" rx="10" ry="10" fill="#545b64" stroke="#232f3e" strokeWidth="1" />
          {/* Rear Right */}
          <rect x="145" y="200" width="20" height="40" rx="10" ry="10" fill="#545b64" stroke="#232f3e" strokeWidth="1" />
        </svg>

        {/* Tire pressure readings positioned around the vehicle */}
        <div style={{ position: 'absolute', top: '20px', left: '10px', textAlign: 'center', fontSize: '12px' }}>
          <div style={{ fontWeight: 'bold', marginBottom: '4px' }}>FL</div>
          <StatusIndicator type={getTireStatus(tirePressure.tire_fl)}>
            {formatPressure(tirePressure.tire_fl)}
          </StatusIndicator>
        </div>

        <div style={{ position: 'absolute', top: '20px', right: '10px', textAlign: 'center', fontSize: '12px' }}>
          <div style={{ fontWeight: 'bold', marginBottom: '4px' }}>FR</div>
          <StatusIndicator type={getTireStatus(tirePressure.tire_fr)}>
            {formatPressure(tirePressure.tire_fr)}
          </StatusIndicator>
        </div>

        <div style={{ position: 'absolute', bottom: '20px', left: '10px', textAlign: 'center', fontSize: '12px' }}>
          <div style={{ fontWeight: 'bold', marginBottom: '4px' }}>RL</div>
          <StatusIndicator type={getTireStatus(tirePressure.tire_rl)}>
            {formatPressure(tirePressure.tire_rl)}
          </StatusIndicator>
        </div>

        <div style={{ position: 'absolute', bottom: '20px', right: '10px', textAlign: 'center', fontSize: '12px' }}>
          <div style={{ fontWeight: 'bold', marginBottom: '4px' }}>RR</div>
          <StatusIndicator type={getTireStatus(tirePressure.tire_rr)}>
            {formatPressure(tirePressure.tire_rr)}
          </StatusIndicator>
        </div>
      </div>

      {/* Additional info */}
      <div style={{ marginTop: '16px', textAlign: 'center' }}>
        {tirePressure.tire_temp_max && (
          <Box variant="small" color="text-body-secondary">
            Max tire temperature: {tirePressure.tire_temp_max.toFixed(1)}°F
          </Box>
        )}
        {lastUpdated && (
          <Box variant="small" color="text-body-secondary">
            Last updated: {new Date(lastUpdated).toLocaleString()}
            {/* Show data source for debugging */}
            {process.env.NODE_ENV === 'development' && (
              <span> (Source: {(tirePressure as any).source || 'unknown'})</span>
            )}
          </Box>
        )}
        <Box variant="small" color="text-body-secondary" margin={{ top: 'xs' }}>
          Normal: 30-35 PSI • Warning: 28-30 PSI • Critical: &lt;28 PSI
        </Box>
      </div>
    </Container>
  );
};

export default TirePressureWidget;
