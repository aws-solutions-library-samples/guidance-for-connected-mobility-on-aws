# Enhanced Fleet Dashboard

## Overview

The enhanced fleet dashboard provides comprehensive real-time monitoring and management capabilities for your connected vehicle fleet. It displays key performance indicators, safety events summary, maintenance alerts summary, and customizable widgets.

## Features

### 1. Real-time Metrics
- **Total Vehicles**: Shows total vehicle count with active/connected status
- **Average Driver Score**: Displays fleet-wide driver performance metrics
- **Safety Events**: Real-time safety event monitoring with severity indicators
- **Maintenance Alerts**: Proactive maintenance scheduling and critical alerts

### 2. Summary Views
- **Safety Events Summary**: Key metrics and recent events overview
- **Maintenance Alerts Summary**: Critical alerts and cost estimates
- **Quick Actions**: Links to detailed views for comprehensive management

### 3. Fleet Filtering
- Filter data by specific fleet or view all fleets
- Time range filtering (1 hour to 1 year)
- Real-time data updates every 30 seconds

### 4. Interactive Features
- Links to detailed safety events and maintenance alerts pages
- Real-time metric updates
- Customizable dashboard widgets for specific fleets

## Components

### DashboardMetrics.tsx
Main dashboard component that orchestrates all metrics and summary displays.

**Props:**
- None (uses context for fleet selection)

**Features:**
- Real-time data fetching
- Fleet-based filtering
- Time range selection
- Auto-refresh capabilities
- Summary views for safety events and maintenance alerts

## Data Sources

### API Endpoints
- `/realtime/vehicles` - Vehicle status and metrics
- `/safety-events` - Safety event data
- `/maintenance-alerts` - Maintenance alert data

### Fallback Data
If API endpoints are unavailable, the components generate realistic sample data to ensure the dashboard remains functional during development and testing.

## Dashboard Structure

### 1. Filters Section
- Time range selector (1 hour to 1 year)
- Fleet filter dropdown
- Refresh button for manual updates

### 2. Key Metrics Cards
- **Total Vehicles**: Count with connectivity status
- **Average Driver Score**: Performance rating with quality badge
- **Safety Events**: Count with severity indicator
- **Maintenance Alerts**: Count with urgency indicator

### 3. Safety Events Summary
- **Event Type Breakdown**: Hard braking, lane departure, speeding, etc.
- **Recent Events Table**: Last 5 safety events with vehicle, event type, and time
- **Quick Actions**: Link to detailed safety events page

### 4. Maintenance Alerts Summary
- **Alert Type Breakdown**: Engine, brake, tire, battery, oil maintenance
- **Critical Alerts Table**: Top 5 critical maintenance alerts with cost estimates
- **Quick Actions**: Link to detailed maintenance alerts page

### 5. Customizable Widgets
- Available for specific fleet selections
- Drag-and-drop widget management
- Fleet-specific performance metrics

## Styling

The dashboard uses AWS CloudScape Design System components with custom CSS enhancements:
- Responsive design for mobile and desktop
- Color-coded severity indicators
- Loading states and real-time updates
- Clean, professional interface

## Usage

```tsx
import { DashboardMetrics } from './DashboardMetrics';

function Dashboard() {
  return (
    <div>
      <DashboardMetrics />
    </div>
  );
}
```

## Integration

The dashboard integrates with:
- Fleet selection context
- User authentication
- Real-time data services
- Navigation routing to detailed pages
- Alert management systems

## Performance

- Auto-refresh every 30 seconds
- Efficient data filtering
- Optimized re-renders with React hooks
- Lightweight summary views

## Navigation

- **Safety Events**: Links to `/alerts/safety` for detailed safety event management
- **Maintenance Alerts**: Links to `/alerts/maintenance` for detailed maintenance scheduling
- **Fleet Management**: Access to fleet-specific customizable widgets

## Accessibility

- ARIA labels for screen readers
- Keyboard navigation support
- High contrast color schemes
- Semantic HTML structure

## Future Enhancements

- Real-time WebSocket connections
- Advanced analytics integration
- Predictive maintenance insights
- Mobile-optimized views
- Custom alert thresholds
