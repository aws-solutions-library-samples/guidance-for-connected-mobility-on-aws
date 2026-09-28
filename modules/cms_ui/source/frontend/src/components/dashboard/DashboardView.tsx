// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import {
 
  Container,
} from "@cloudscape-design/components";
import '../iot/iot-dashboard.css';
import './full-width-dashboard.css';
import { Content } from "./content";

export default function DashboardView() {
  return (
        <Container className="full-width-dashboard">
          <Content/>
        </Container>
              
  );
}
