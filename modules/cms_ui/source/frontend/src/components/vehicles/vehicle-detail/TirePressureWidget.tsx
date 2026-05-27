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
            rx="20"
            ry="20"
            fill="#e8edf1"
            stroke="#232f3e"
            strokeWidth="2"
          />
          
          {/* Hood */}
          <path d="M 70 55 L 130 55 L 125 75 L 75 75 Z" fill="#d1d9e0" stroke="#232f3e" strokeWidth="1.5" />
          
          {/* Rear window */}
          <path d="M 75 225 L 125 225 L 130 245 L 70 245 Z" fill="#d1d9e0" stroke="#232f3e" strokeWidth="1.5" />
          
          {/* Center line */}
          <line x1="100" y1="75" x2="100" y2="225" stroke="#c5cdd5" strokeWidth="1" strokeDasharray="4 4" />

          {/* Front Left Wheel */}
          <rect x="32" y="55" width="24" height="50" rx="6" fill="#2d3436" stroke="#1a1a2e" strokeWidth="1.5" />
          <ellipse cx="44" cy="80" rx="8" ry="8" fill="#545b64" stroke="#6c757d" strokeWidth="1" />
          <ellipse cx="44" cy="80" rx="3" ry="3" fill="#adb5bd" />
          <line x1="44" y1="72" x2="44" y2="74" stroke="#adb5bd" strokeWidth="1" />
          <line x1="44" y1="86" x2="44" y2="88" stroke="#adb5bd" strokeWidth="1" />
          <line x1="36" y1="80" x2="38" y2="80" stroke="#adb5bd" strokeWidth="1" />
          <line x1="50" y1="80" x2="52" y2="80" stroke="#adb5bd" strokeWidth="1" />

          {/* Front Right Wheel */}
          <rect x="144" y="55" width="24" height="50" rx="6" fill="#2d3436" stroke="#1a1a2e" strokeWidth="1.5" />
          <ellipse cx="156" cy="80" rx="8" ry="8" fill="#545b64" stroke="#6c757d" strokeWidth="1" />
          <ellipse cx="156" cy="80" rx="3" ry="3" fill="#adb5bd" />
          <line x1="156" y1="72" x2="156" y2="74" stroke="#adb5bd" strokeWidth="1" />
          <line x1="156" y1="86" x2="156" y2="88" stroke="#adb5bd" strokeWidth="1" />
          <line x1="148" y1="80" x2="150" y2="80" stroke="#adb5bd" strokeWidth="1" />
          <line x1="162" y1="80" x2="164" y2="80" stroke="#adb5bd" strokeWidth="1" />

          {/* Rear Left Wheel */}
          <rect x="32" y="195" width="24" height="50" rx="6" fill="#2d3436" stroke="#1a1a2e" strokeWidth="1.5" />
          <ellipse cx="44" cy="220" rx="8" ry="8" fill="#545b64" stroke="#6c757d" strokeWidth="1" />
          <ellipse cx="44" cy="220" rx="3" ry="3" fill="#adb5bd" />
          <line x1="44" y1="212" x2="44" y2="214" stroke="#adb5bd" strokeWidth="1" />
          <line x1="44" y1="226" x2="44" y2="228" stroke="#adb5bd" strokeWidth="1" />
          <line x1="36" y1="220" x2="38" y2="220" stroke="#adb5bd" strokeWidth="1" />
          <line x1="50" y1="220" x2="52" y2="220" stroke="#adb5bd" strokeWidth="1" />

          {/* Rear Right Wheel */}
          <rect x="144" y="195" width="24" height="50" rx="6" fill="#2d3436" stroke="#1a1a2e" strokeWidth="1.5" />
          <ellipse cx="156" cy="220" rx="8" ry="8" fill="#545b64" stroke="#6c757d" strokeWidth="1" />
          <ellipse cx="156" cy="220" rx="3" ry="3" fill="#adb5bd" />
          <line x1="156" y1="212" x2="156" y2="214" stroke="#adb5bd" strokeWidth="1" />
          <line x1="156" y1="226" x2="156" y2="228" stroke="#adb5bd" strokeWidth="1" />
          <line x1="148" y1="220" x2="150" y2="220" stroke="#adb5bd" strokeWidth="1" />
          <line x1="162" y1="220" x2="164" y2="220" stroke="#adb5bd" strokeWidth="1" />
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
