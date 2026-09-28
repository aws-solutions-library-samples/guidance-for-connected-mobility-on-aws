import React from "react";
import {
  Box,
  ColumnLayout,
  Container,
  Header,
  SpaceBetween,
  StatusIndicator,
} from "@cloudscape-design/components";
import { useAuth } from "../../auth/useAuth";

const ProfilePage: React.FC = () => {
  const auth = useAuth();
  const user = auth.user;

  return (
    <SpaceBetween size="l">
      <Container header={<Header variant="h2">Account Details</Header>}>
        <ColumnLayout columns={2} variant="text-grid">
          <SpaceBetween size="xxs">
            <Box variant="awsui-key-label">Email</Box>
            <Box>{user?.email || "—"}</Box>
          </SpaceBetween>
          <SpaceBetween size="xxs">
            <Box variant="awsui-key-label">Username</Box>
            <Box>{user?.username || user?.email?.split("@")[0] || "—"}</Box>
          </SpaceBetween>
          <SpaceBetween size="xxs">
            <Box variant="awsui-key-label">Role</Box>
            <Box>{user?.role || "Fleet Manager"}</Box>
          </SpaceBetween>
          <SpaceBetween size="xxs">
            <Box variant="awsui-key-label">Status</Box>
            <StatusIndicator type="success">Active</StatusIndicator>
          </SpaceBetween>
        </ColumnLayout>
      </Container>
    </SpaceBetween>
  );
};

export default ProfilePage;
