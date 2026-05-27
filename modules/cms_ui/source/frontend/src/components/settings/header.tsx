// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import { Header, HeaderProps } from "@cloudscape-design/components";
import { breadcrumbsItems } from "./breadcrumbs";

export function SettingsHeader(props: HeaderProps) {
  return (
    <Header
      variant="h1"
      description="Configure application settings and preferences"
      {...props}
      breadcrumbs={breadcrumbsItems}
    >
      Settings
    </Header>
  );
}