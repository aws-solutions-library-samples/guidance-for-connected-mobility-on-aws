// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0
//
// AddSubscriptionModal — stub UI for creating a new data-product subscription
// from within CMS.
//
// This is deliberately UI-only. The submit action builds an in-memory
// Subscription record and adds it to the session-scoped list (via
// mockData.addSubscription). No backend call, no persistence beyond a page
// refresh. When a real subscriptions-API is wired, the submit handler swaps
// its in-memory add for an API call — the form shape stays.

import React, { useMemo, useState } from 'react';
import {
  Alert,
  Box,
  Button,
  ColumnLayout,
  FormField,
  Input,
  Modal,
  Select,
  SpaceBetween,
  Badge,
} from '@cloudscape-design/components';
import {
  AvailableProduct,
  Tier,
  addSubscription,
  buildSubscriptionFromForm,
  getAvailableProducts,
} from './mockData';

interface Props {
  visible: boolean;
  onDismiss: () => void;
  onAdded: () => void;
}

const AddSubscriptionModal: React.FC<Props> = ({ visible, onDismiss, onAdded }) => {
  const [productId, setProductId] = useState<string | null>(null);
  const [tier, setTier] = useState<Tier | null>(null);
  const [vehiclesCapacity, setVehiclesCapacity] = useState<string>('25');

  const AVAILABLE_PRODUCTS = useMemo(() => getAvailableProducts(), [visible]);

  const product: AvailableProduct | undefined = useMemo(
    () => AVAILABLE_PRODUCTS.find((p) => p.productId === productId),
    [productId],
  );

  const tierOptions = useMemo(() => {
    if (!product) return [];
    return product.supportedTiers.map((t) => ({
      label: t,
      value: t,
    }));
  }, [product]);

  const productOptions = useMemo(
    () =>
      AVAILABLE_PRODUCTS.map((p) => ({
        label: p.productName,
        value: p.productId,
        description: p.producer,
      })),
    [AVAILABLE_PRODUCTS],
  );

  const capacityNum = Number(vehiclesCapacity);
  const capacityValid = Number.isFinite(capacityNum) && capacityNum >= 1 && capacityNum <= 1000;

  const canSubmit = product && tier && capacityValid;

  const reset = () => {
    setProductId(null);
    setTier(null);
    setVehiclesCapacity('25');
  };

  const handleSubmit = () => {
    if (!product || !tier) return;
    const sub = buildSubscriptionFromForm({
      product,
      tier,
      vehiclesCapacity: capacityNum,
    });
    addSubscription(sub);
    reset();
    onAdded();
  };

  const handleDismiss = () => {
    reset();
    onDismiss();
  };

  return (
    <Modal
      visible={visible}
      onDismiss={handleDismiss}
      header="Add subscription"
      size="medium"
      footer={
        <Box float="right">
          <SpaceBetween direction="horizontal" size="xs">
            <Button variant="link" onClick={handleDismiss}>Cancel</Button>
            <Button variant="primary" onClick={handleSubmit} disabled={!canSubmit}>
              Add subscription
            </Button>
          </SpaceBetween>
        </Box>
      }
    >
      <SpaceBetween size="l">
        <Alert type="info">
          New subscriptions start in the <b>Pending</b> state. Data begins flowing
          once the producer confirms and vehicles are enrolled.
        </Alert>

        <FormField
          label="Data product"
          description="Available data products from producers you can subscribe to."
        >
          <Select
            selectedOption={
              product
                ? { label: product.productName, value: product.productId, description: product.producer }
                : null
            }
            options={productOptions}
            placeholder="Choose a data product"
            onChange={({ detail }) => {
              const newProductId = detail.selectedOption.value ?? null;
              setProductId(newProductId);
              setTier(null); // reset tier when product changes
            }}
            expandToViewport
          />
        </FormField>

        {product && (
          <Box>
            <Box color="text-body-secondary" fontSize="body-s">
              {product.description}
            </Box>
            <Box padding={{ top: 'xs' }}>
              <SpaceBetween direction="horizontal" size="xs">
                <Badge>{product.totalSignals} signals available</Badge>
                {product.supportedTiers.map((t) => (
                  <Badge key={t} color="grey">{t}</Badge>
                ))}
              </SpaceBetween>
            </Box>
          </Box>
        )}

        <FormField
          label="Tier"
          description={
            product
              ? `Choose the tier this fleet will subscribe at. Higher tiers grant more signals.`
              : 'Choose a data product first.'
          }
        >
          <Select
            selectedOption={tier ? { label: tier, value: tier } : null}
            options={tierOptions}
            placeholder="Choose a tier"
            disabled={!product}
            onChange={({ detail }) => setTier((detail.selectedOption.value as Tier) ?? null)}
            expandToViewport
          />
        </FormField>

        <ColumnLayout columns={2}>
          <FormField
            label="Vehicle capacity"
            description="Maximum number of vehicles this subscription can cover. Enrol individual vehicles later from the subscription detail page."
            errorText={
              capacityValid ? undefined : 'Capacity must be a whole number between 1 and 1000.'
            }
          >
            <Input
              value={vehiclesCapacity}
              onChange={({ detail }) => setVehiclesCapacity(detail.value)}
              type="number"
              inputMode="numeric"
            />
          </FormField>
          <div /> {/* spacer to keep the input to half-width */}
        </ColumnLayout>
      </SpaceBetween>
    </Modal>
  );
};

export default AddSubscriptionModal;
