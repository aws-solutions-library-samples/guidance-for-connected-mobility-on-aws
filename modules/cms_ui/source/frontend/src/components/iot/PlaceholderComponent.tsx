import React from 'react';
import { Container, Header, Box } from '@cloudscape-design/components';

interface PlaceholderComponentProps {
  title: string;
  description: string;
}

const PlaceholderComponent: React.FC<PlaceholderComponentProps> = ({ title, description }) => {
  return (
    <Container header={<Header variant="h2">{title}</Header>}>
      <Box>
        <p>{description}</p>
        <p>This is a placeholder component that will be implemented with full AIOT functionality.</p>
      </Box>
    </Container>
  );
};

export default PlaceholderComponent;
