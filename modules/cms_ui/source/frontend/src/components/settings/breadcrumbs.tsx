// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import { BreadcrumbGroupProps } from "@cloudscape-design/components";
import { UI_ROUTES } from "../../utils/constants";

export const breadcrumbsItems: BreadcrumbGroupProps.Item[] = [
  {
    text: "Home",
    href: UI_ROUTES.ROOT,
  },
  {
    text: "Settings",
    href: UI_ROUTES.SETTINGS,
  },
];