// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React, { useState, useEffect, useContext } from 'react';
import {
  Table,
  Button,
  SpaceBetween,
  Pagination,
  TextFilter,
  StatusIndicator,
  Box,
  Link,
  Header,
  Modal,
  Form,
  FormField,
  Input,
  Select,
  Flashbar
} from '@cloudscape-design/components';
import { useNavigate } from 'react-router-dom';
import { ApiContext } from '@/api/provider';
import { getRuntimeConfig } from '@/config/api';
import { 
  ListDriversCommand, 
  CreateDriverCommand, 
  UpdateDriverCommand, 
  DeleteDriverCommand,
  ListFleetsCommand 
} from '@/api/fleet-management-client';

interface Driver {
  driverId: string;
  firstName: string;
  lastName: string;
  email: string;
  phone?: string;
  licenseNumber: string;
  licenseExpiry?: string;
  status: 'active' | 'inactive';
  fleetId?: string;
  createdAt?: string;
  updatedAt?: string;
}

const DriversView: React.FC = () => {
  const navigate = useNavigate();
  const api = useContext(ApiContext);
  const [drivers, setDrivers] = useState<Driver[]>([]);
  const [fleets, setFleets] = useState<any[]>([]);
  const [selectedItems, setSelectedItems] = useState<Driver[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [filteringText, setFilteringText] = useState('');
  const [currentPage, setCurrentPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [showEditModal, setShowEditModal] = useState(false);
  const [showDeleteModal, setShowDeleteModal] = useState(false);
  const [editingDriver, setEditingDriver] = useState<Driver | null>(null);
  const [notifications, setNotifications] = useState<any[]>([]);
  
  const [formData, setFormData] = useState({
    firstName: '',
    lastName: '',
    email: '',
    phone: '',
    licenseNumber: '',
    licenseExpiry: '',
    fleetId: '',
    status: 'active'
  });

  const addNotification = (notification: any) => {
    setNotifications(prev => [...prev, { ...notification, id: Date.now() }]);
  };

  const removeNotification = (id: number) => {
    setNotifications(prev => prev.filter(n => n.id !== id));
  };

  const fetchDrivers = async () => {
    try {
      setIsLoading(true);
      const response = await fetch(`${getRuntimeConfig().apiEndpoint}api/v1/drivers?_t=${Date.now()}`);
      const data = await response.json();
      setDrivers(data.drivers || []);
    } catch (error: any) {
      addNotification({
        type: 'error',
        content: `Failed to fetch drivers: ${error.message}`,
        dismissible: true,
        onDismiss: () => {}
      });
    } finally {
      setIsLoading(false);
    }
  };

  const fetchFleets = async () => {
    try {
      const response = await fetch(`${getRuntimeConfig().apiEndpoint}api/v1/fleets?_t=${Date.now()}`);
      const data = await response.json();
      setFleets(data.fleets || []);
    } catch (error) {
      console.error('Failed to fetch fleets:', error);
    }
  };

  useEffect(() => {
    fetchDrivers();
    fetchFleets();
  }, []);

  const handleCreate = async () => {
    try {
      const response = await fetch(`${getRuntimeConfig().apiEndpoint}api/v1/drivers`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ entry: formData })
      });
      
      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }
      
      addNotification({
        type: 'success',
        content: 'Driver created successfully',
        dismissible: true,
        onDismiss: () => {}
      });
      setShowCreateModal(false);
      resetForm();
      fetchDrivers();
    } catch (error: any) {
      addNotification({
        type: 'error',
        content: `Failed to create driver: ${error.message}`,
        dismissible: true,
        onDismiss: () => {}
      });
    }
  };

  const handleEdit = async () => {
    if (!editingDriver) return;
    
    try {
      const response = await fetch(`${getRuntimeConfig().apiEndpoint}api/v1/drivers/${editingDriver.driverId}`, {
        method: 'PUT',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ entry: formData })
      });
      
      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }
      
      addNotification({
        type: 'success',
        content: 'Driver updated successfully',
        dismissible: true,
        onDismiss: () => {}
      });
      setShowEditModal(false);
      setEditingDriver(null);
      resetForm();
      fetchDrivers();
    } catch (error: any) {
      addNotification({
        type: 'error',
        content: `Failed to update driver: ${error.message}`,
        dismissible: true,
        onDismiss: () => {}
      });
    }
  };

  const handleDelete = async () => {
    try {
      for (const driver of selectedItems) {
        const response = await fetch(`${getRuntimeConfig().apiEndpoint}api/v1/drivers/${driver.driverId}`, {
          method: 'DELETE'
        });
        
        if (!response.ok) {
          throw new Error(`HTTP error! status: ${response.status}`);
        }
      }
      addNotification({
        type: 'success',
        content: `Successfully deleted ${selectedItems.length} driver${selectedItems.length > 1 ? 's' : ''}`,
        dismissible: true,
        onDismiss: () => {}
      });
      setShowDeleteModal(false);
      setSelectedItems([]);
      fetchDrivers();
    } catch (error: any) {
      addNotification({
        type: 'error',
        content: `Failed to delete driver${selectedItems.length > 1 ? 's' : ''}: ${error.message}`,
        dismissible: true,
        onDismiss: () => {}
      });
    }
  };

  const resetForm = () => {
    setFormData({
      firstName: '',
      lastName: '',
      email: '',
      phone: '',
      licenseNumber: '',
      licenseExpiry: '',
      fleetId: '',
      status: 'active'
    });
  };

  const openEditModal = (driver: Driver) => {
    setEditingDriver(driver);
    setFormData({
      firstName: driver.firstName,
      lastName: driver.lastName,
      email: driver.email,
      phone: driver.phone || '',
      licenseNumber: driver.licenseNumber,
      licenseExpiry: driver.licenseExpiry || '',
      fleetId: driver.fleetId || '',
      status: driver.status
    });
    setShowEditModal(true);
  };

  const filteredDrivers = drivers.filter(driver =>
    `${driver.firstName} ${driver.lastName}`.toLowerCase().includes(filteringText.toLowerCase()) ||
    driver.email.toLowerCase().includes(filteringText.toLowerCase()) ||
    driver.licenseNumber.toLowerCase().includes(filteringText.toLowerCase())
  );

  const paginatedDrivers = filteredDrivers.slice(
    (currentPage - 1) * pageSize,
    currentPage * pageSize
  );

  const fleetOptions = fleets.map(fleet => ({
    label: fleet.name,
    value: fleet.id || fleet.fleetId
  }));

  const columnDefinitions = [
    {
      id: 'name',
      header: 'Driver Name',
      cell: (item: Driver) => (
        <Link 
          href={`#/drivers/${item.driverId}`}
          onFollow={(e) => {
            e.preventDefault();
            navigate(`/drivers/${item.driverId}`);
          }}
        >
          {item.firstName} {item.lastName}
        </Link>
      ),
      sortingField: 'firstName'
    },
    {
      id: 'email',
      header: 'Email',
      cell: (item: Driver) => item.email,
      sortingField: 'email'
    },
    {
      id: 'phone',
      header: 'Phone',
      cell: (item: Driver) => item.phone || '-',
      sortingField: 'phone'
    },
    {
      id: 'licenseNumber',
      header: 'License Number',
      cell: (item: Driver) => item.licenseNumber,
      sortingField: 'licenseNumber'
    },
    {
      id: 'status',
      header: 'Status',
      cell: (item: Driver) => (
        <StatusIndicator
          type={item.status === 'active' ? 'success' : 'stopped'}
        >
          {item.status.charAt(0).toUpperCase() + item.status.slice(1)}
        </StatusIndicator>
      ),
      sortingField: 'status'
    },
    {
      id: 'fleetId',
      header: 'Fleet',
      cell: (item: Driver) => {
        const fleet = fleets.find(f => (f.id || f.fleetId) === item.fleetId);
        return fleet ? fleet.name : (item.fleetId || '-');
      },
      sortingField: 'fleetId'
    }
  ];

  return (
    <SpaceBetween size="l">
      <Flashbar items={notifications} />
      
      <Table
        columnDefinitions={columnDefinitions}
        items={paginatedDrivers}
        loading={isLoading}
        selectedItems={selectedItems}
        onSelectionChange={({ detail }) => setSelectedItems(detail.selectedItems)}
        selectionType="multi"
        ariaLabels={{
          selectionGroupLabel: "Items selection",
          allItemsSelectionLabel: ({ selectedItems }) =>
            `${selectedItems.length} ${selectedItems.length === 1 ? "item" : "items"} selected`,
          itemSelectionLabel: ({ selectedItems }, item) => {
            const isItemSelected = selectedItems.filter(i => i.driverId === item.driverId).length;
            return `${item.firstName} ${item.lastName} is ${isItemSelected ? "" : "not"} selected`;
          }
        }}
        header={
          <Header
            variant="h2"
            counter={`(${filteredDrivers.length})`}
            actions={
              <SpaceBetween size="xs" direction="horizontal">
                <Button onClick={() => setShowCreateModal(true)}>
                  Add Driver
                </Button>
                <Button 
                  disabled={selectedItems.length !== 1} 
                  onClick={() => openEditModal(selectedItems[0])}
                >
                  Edit
                </Button>
                <Button 
                  disabled={selectedItems.length === 0} 
                  onClick={() => setShowDeleteModal(true)}
                >
                  Delete
                </Button>
              </SpaceBetween>
            }
          >
            Drivers
          </Header>
        }
        filter={
          <TextFilter
            filteringText={filteringText}
            onChange={({ detail }) => setFilteringText(detail.filteringText)}
            filteringPlaceholder="Search drivers..."
            countText={`${filteredDrivers.length} ${filteredDrivers.length === 1 ? 'match' : 'matches'}`}
          />
        }
        pagination={
          <Pagination
            currentPageIndex={currentPage}
            pagesCount={Math.ceil(filteredDrivers.length / pageSize)}
            onChange={({ detail }) => setCurrentPage(detail.currentPageIndex)}
          />
        }
        empty={
          <Box textAlign="center" color="inherit">
            <b>No drivers</b>
            <Box padding={{ bottom: "s" }} variant="p" color="inherit">
              No drivers to display.
            </Box>
            <Button onClick={() => setShowCreateModal(true)}>Add Driver</Button>
          </Box>
        }
      />

      {/* Create Driver Modal */}
      <Modal
        visible={showCreateModal}
        onDismiss={() => { setShowCreateModal(false); resetForm(); }}
        header="Add Driver"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => { setShowCreateModal(false); resetForm(); }}>
                Cancel
              </Button>
              <Button variant="primary" onClick={handleCreate}>
                Create
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <Form>
          <SpaceBetween size="m">
            <FormField label="First Name">
              <Input
                value={formData.firstName}
                onChange={({ detail }) => setFormData(prev => ({ ...prev, firstName: detail.value }))}
              />
            </FormField>
            <FormField label="Last Name">
              <Input
                value={formData.lastName}
                onChange={({ detail }) => setFormData(prev => ({ ...prev, lastName: detail.value }))}
              />
            </FormField>
            <FormField label="Email">
              <Input
                value={formData.email}
                onChange={({ detail }) => setFormData(prev => ({ ...prev, email: detail.value }))}
                type="email"
              />
            </FormField>
            <FormField label="Phone">
              <Input
                value={formData.phone}
                onChange={({ detail }) => setFormData(prev => ({ ...prev, phone: detail.value }))}
              />
            </FormField>
            <FormField label="License Number">
              <Input
                value={formData.licenseNumber}
                onChange={({ detail }) => setFormData(prev => ({ ...prev, licenseNumber: detail.value }))}
              />
            </FormField>
            <FormField label="License Expiry">
              <Input
                value={formData.licenseExpiry}
                onChange={({ detail }) => setFormData(prev => ({ ...prev, licenseExpiry: detail.value }))}
                type="date"
              />
            </FormField>
            <FormField label="Fleet">
              <Select
                selectedOption={fleetOptions.find(option => option.value === formData.fleetId) || null}
                onChange={({ detail }) => setFormData(prev => ({ ...prev, fleetId: detail.selectedOption?.value || '' }))}
                options={fleetOptions}
                placeholder="Select a fleet"
              />
            </FormField>
          </SpaceBetween>
        </Form>
      </Modal>

      {/* Edit Driver Modal */}
      <Modal
        visible={showEditModal}
        onDismiss={() => { setShowEditModal(false); setEditingDriver(null); resetForm(); }}
        header="Edit Driver"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => { setShowEditModal(false); setEditingDriver(null); resetForm(); }}>
                Cancel
              </Button>
              <Button variant="primary" onClick={handleEdit}>
                Update
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <Form>
          <SpaceBetween size="m">
            <FormField label="First Name">
              <Input
                value={formData.firstName}
                onChange={({ detail }) => setFormData(prev => ({ ...prev, firstName: detail.value }))}
              />
            </FormField>
            <FormField label="Last Name">
              <Input
                value={formData.lastName}
                onChange={({ detail }) => setFormData(prev => ({ ...prev, lastName: detail.value }))}
              />
            </FormField>
            <FormField label="Email">
              <Input
                value={formData.email}
                onChange={({ detail }) => setFormData(prev => ({ ...prev, email: detail.value }))}
                type="email"
              />
            </FormField>
            <FormField label="Phone">
              <Input
                value={formData.phone}
                onChange={({ detail }) => setFormData(prev => ({ ...prev, phone: detail.value }))}
              />
            </FormField>
            <FormField label="License Number">
              <Input
                value={formData.licenseNumber}
                onChange={({ detail }) => setFormData(prev => ({ ...prev, licenseNumber: detail.value }))}
              />
            </FormField>
            <FormField label="License Expiry">
              <Input
                value={formData.licenseExpiry}
                onChange={({ detail }) => setFormData(prev => ({ ...prev, licenseExpiry: detail.value }))}
                type="date"
              />
            </FormField>
            <FormField label="Fleet">
              <Select
                selectedOption={fleetOptions.find(option => option.value === formData.fleetId) || null}
                onChange={({ detail }) => setFormData(prev => ({ ...prev, fleetId: detail.selectedOption?.value || '' }))}
                options={fleetOptions}
                placeholder="Select a fleet"
              />
            </FormField>
            <FormField label="Status">
              <Select
                selectedOption={{ label: formData.status === 'active' ? 'Active' : 'Inactive', value: formData.status }}
                onChange={({ detail }) => setFormData(prev => ({ ...prev, status: detail.selectedOption?.value as 'active' | 'inactive' }))}
                options={[
                  { label: 'Active', value: 'active' },
                  { label: 'Inactive', value: 'inactive' }
                ]}
              />
            </FormField>
          </SpaceBetween>
        </Form>
      </Modal>

      {/* Delete Confirmation Modal */}
      <Modal
        visible={showDeleteModal}
        onDismiss={() => setShowDeleteModal(false)}
        header={`Delete ${selectedItems.length > 1 ? 'Drivers' : 'Driver'}`}
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setShowDeleteModal(false)}>
                Cancel
              </Button>
              <Button variant="primary" onClick={handleDelete}>
                Delete
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <Box>
          Are you sure you want to delete {selectedItems.length > 1 ? `${selectedItems.length} drivers` : `driver ${selectedItems[0]?.firstName} ${selectedItems[0]?.lastName}`}? 
          This action cannot be undone.
        </Box>
      </Modal>
    </SpaceBetween>
  );
};

export default DriversView;
