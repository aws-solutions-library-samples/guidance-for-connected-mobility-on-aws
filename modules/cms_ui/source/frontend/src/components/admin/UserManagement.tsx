import React, { useState, useEffect, useCallback } from 'react';
import {
  Table, Button, SpaceBetween, Header, Modal, FormField, Input, Select, Box,
  StatusIndicator, Badge, ButtonDropdown, Alert, Multiselect
} from '@cloudscape-design/components';
import { useAuth } from '../../auth/useAuth';
import { getRuntimeConfig } from '../../config/api';

interface User {
  username: string;
  email: string;
  status: string;
  enabled: boolean;
  groups: string[];
  fleetIds: string;
  vehicleIds: string;
  createdAt: string;
}

const ROLE_OPTIONS = [
  { label: 'Platform Admin', value: 'platform-admin' },
  { label: 'Fleet Operator', value: 'fleet-operator' },
  { label: 'Fleet Viewer', value: 'fleet-viewer' },
  { label: 'Connect Agent', value: 'connect-agent' },
  { label: 'Product Engineer', value: 'product-engineer' },
];

export default function UserManagement() {
  const { getIdToken } = useAuth();
  const [users, setUsers] = useState<User[]>([]);
  const [fleets, setFleets] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [showEdit, setShowEdit] = useState(false);
  const [showPassword, setShowPassword] = useState(false);
  const [selectedUser, setSelectedUser] = useState<User | null>(null);
  const [alert, setAlert] = useState<{ type: 'success' | 'error'; msg: string } | null>(null);
  const [form, setForm] = useState({ email: '', group: 'fleet-operator', fleetIds: '', vehicleIds: '', tempPassword: '' });
  const [passwordForm, setPasswordForm] = useState({ tempPassword: '' });

  const apiBase = (window as any).runtimeConfig?.apiEndpoint || getRuntimeConfig().apiEndpoint || '';

  const hdrs = useCallback(() => {
    const token = getIdToken() || sessionStorage.getItem('idToken') || localStorage.getItem('idToken');
    return { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) };
  }, [getIdToken]);

  const fetchUsers = useCallback(async () => {
    setLoading(true);
    try {
      const resp = await fetch(`${apiBase}api/v1/users`, { headers: hdrs() });
      const data = await resp.json();
      setUsers(data.users || []);
    } catch (e) { console.error(e); }
    setLoading(false);
  }, [apiBase, hdrs]);

  const fetchFleets = useCallback(async () => {
    try {
      const resp = await fetch(`${apiBase}api/v1/fleets`, { headers: hdrs() });
      const data = await resp.json();
      setFleets(data.fleets || []);
    } catch (e) { console.error(e); }
  }, [apiBase, hdrs]);

  useEffect(() => { fetchUsers(); fetchFleets(); }, []);

  const doAction = async (username: string, action: string, extra: Record<string, any> = {}) => {
    try {
      const resp = await fetch(`${apiBase}api/v1/users/${encodeURIComponent(username)}`, {
        method: 'PUT', headers: hdrs(),
        body: JSON.stringify({ action, ...extra }),
      });
      const data = await resp.json();
      if (resp.ok) {
        setAlert({ type: 'success', msg: data.message });
        fetchUsers();
      } else {
        setAlert({ type: 'error', msg: data.error });
      }
    } catch (e: any) { setAlert({ type: 'error', msg: e.message }); }
  };

  const createUser = async () => {
    try {
      const resp = await fetch(`${apiBase}api/v1/users`, {
        method: 'POST', headers: hdrs(), body: JSON.stringify(form),
      });
      const data = await resp.json();
      if (resp.ok) {
        setAlert({ type: 'success', msg: `User ${form.email} created` });
        setShowCreate(false);
        setForm({ email: '', group: 'fleet-operator', fleetIds: '', vehicleIds: '', tempPassword: '' });
        fetchUsers();
      } else { setAlert({ type: 'error', msg: data.error }); }
    } catch (e: any) { setAlert({ type: 'error', msg: e.message }); }
  };

  const updateUser = async () => {
    if (!selectedUser) return;
    await doAction(selectedUser.username, 'update', { group: form.group, fleetIds: form.fleetIds });
    setShowEdit(false);
  };

  const deleteUser = async (username: string) => {
    if (!confirm(`Delete user ${username}? This cannot be undone.`)) return;
    try {
      await fetch(`${apiBase}api/v1/users/${encodeURIComponent(username)}`, { method: 'DELETE', headers: hdrs() });
      setAlert({ type: 'success', msg: `User ${username} deleted` });
      fetchUsers();
    } catch (e: any) { setAlert({ type: 'error', msg: e.message }); }
  };

  const fleetOptions = fleets.map(f => ({ label: `${f.name || f.fleetId} (${f.fleetId})`, value: f.fleetId }));

  return (
    <SpaceBetween size="l">
      {alert && <Alert type={alert.type} dismissible onDismiss={() => setAlert(null)}>{alert.msg}</Alert>}

      <Table
        header={
          <Header variant="h2" counter={`(${users.length})`}
            actions={<Button variant="primary" onClick={() => setShowCreate(true)}>Create User</Button>}>
            User Management
          </Header>
        }
        loading={loading}
        items={users}
        columnDefinitions={[
          { id: 'email', header: 'Email', cell: (u) => u.email, sortingField: 'email' },
          { id: 'status', header: 'Status', cell: (u) => {
            if (!u.enabled) return <StatusIndicator type="stopped">Disabled</StatusIndicator>;
            if (u.status === 'CONFIRMED') return <StatusIndicator type="success">Active</StatusIndicator>;
            if (u.status === 'FORCE_CHANGE_PASSWORD') return <StatusIndicator type="warning">Pending</StatusIndicator>;
            return <StatusIndicator type="info">{u.status}</StatusIndicator>;
          }},
          { id: 'role', header: 'Role', cell: (u) => (
            <Badge color={u.groups.includes('platform-admin') ? 'red' : u.groups.includes('fleet-operator') ? 'blue' : 'grey'}>
              {u.groups[0] || 'none'}
            </Badge>
          )},
          { id: 'fleetIds', header: 'Fleet IDs', cell: (u) => u.fleetIds || '—' },
          { id: 'actions', header: 'Actions', cell: (u) => (
            <ButtonDropdown
              variant="icon"
              ariaLabel="User actions"
              expandToViewport={true}
              items={[
                { id: 'edit', text: 'Edit Role & Fleets' },
                { id: 'setPassword', text: 'Set Temporary Password' },
                { id: 'resetPassword', text: 'Send Password Reset' },
                { id: 'resendInvite', text: 'Resend Invite Email', disabled: u.status !== 'FORCE_CHANGE_PASSWORD' },
                { id: u.enabled ? 'disable' : 'enable', text: u.enabled ? 'Disable User' : 'Enable User' },
                { id: 'delete', text: 'Delete User' },
              ]}
              onItemClick={({ detail }) => {
                switch (detail.id) {
                  case 'edit':
                    setSelectedUser(u);
                    setForm({ ...form, group: u.groups[0] || 'fleet-viewer', fleetIds: u.fleetIds });
                    setShowEdit(true);
                    break;
                  case 'setPassword':
                    setSelectedUser(u);
                    setPasswordForm({ tempPassword: '' });
                    setShowPassword(true);
                    break;
                  case 'resetPassword': doAction(u.username, 'resetPassword'); break;
                  case 'resendInvite': doAction(u.username, 'resendInvite'); break;
                  case 'disable': doAction(u.username, 'disable'); break;
                  case 'enable': doAction(u.username, 'enable'); break;
                  case 'delete': deleteUser(u.username); break;
                }
              }}
            />
          )},
        ]}
        empty={<Box textAlign="center">No users found. Create one to get started.</Box>}
        sortingDisabled={false}
      />

      {/* Create User Modal */}
      <Modal visible={showCreate} onDismiss={() => setShowCreate(false)} header="Create User" size="medium"
        footer={<SpaceBetween direction="horizontal" size="xs"><Button onClick={() => setShowCreate(false)}>Cancel</Button><Button variant="primary" onClick={createUser}>Create</Button></SpaceBetween>}>
        <SpaceBetween size="m">
          <FormField label="Email" constraintText="User will receive an invite email">
            <Input value={form.email} onChange={({ detail }) => setForm({ ...form, email: detail.value })} placeholder="user@example.com" />
          </FormField>
          <FormField label="Role">
            <Select selectedOption={ROLE_OPTIONS.find(o => o.value === form.group) || ROLE_OPTIONS[1]} options={ROLE_OPTIONS}
              onChange={({ detail }) => setForm({ ...form, group: detail.selectedOption.value || 'fleet-operator' })} />
          </FormField>
          <FormField label="Fleet Assignment" description="Select fleets this user can access">
            <Multiselect
              selectedOptions={form.fleetIds ? form.fleetIds.split(',').map(id => fleetOptions.find(o => o.value === id.trim()) || { label: id.trim(), value: id.trim() }) : []}
              options={fleetOptions}
              onChange={({ detail }) => setForm({ ...form, fleetIds: detail.selectedOptions.map(o => o.value).join(',') })}
              placeholder="Select fleets"
            />
          </FormField>
          <FormField label="Temporary Password" constraintText="Min 8 chars, uppercase, lowercase, number, symbol">
            <Input type="password" value={form.tempPassword} onChange={({ detail }) => setForm({ ...form, tempPassword: detail.value })} />
          </FormField>
        </SpaceBetween>
      </Modal>

      {/* Edit User Modal */}
      <Modal visible={showEdit} onDismiss={() => setShowEdit(false)} header={`Edit ${selectedUser?.email}`} size="medium"
        footer={<SpaceBetween direction="horizontal" size="xs"><Button onClick={() => setShowEdit(false)}>Cancel</Button><Button variant="primary" onClick={updateUser}>Save</Button></SpaceBetween>}>
        <SpaceBetween size="m">
          <FormField label="Role">
            <Select selectedOption={ROLE_OPTIONS.find(o => o.value === form.group) || ROLE_OPTIONS[1]} options={ROLE_OPTIONS}
              onChange={({ detail }) => setForm({ ...form, group: detail.selectedOption.value || 'fleet-operator' })} />
          </FormField>
          <FormField label="Fleet Assignment">
            <Multiselect
              selectedOptions={form.fleetIds ? form.fleetIds.split(',').map(id => fleetOptions.find(o => o.value === id.trim()) || { label: id.trim(), value: id.trim() }) : []}
              options={fleetOptions}
              onChange={({ detail }) => setForm({ ...form, fleetIds: detail.selectedOptions.map(o => o.value).join(',') })}
              placeholder="Select fleets"
            />
          </FormField>
        </SpaceBetween>
      </Modal>

      {/* Set Password Modal */}
      <Modal visible={showPassword} onDismiss={() => setShowPassword(false)} header={`Set Password for ${selectedUser?.email}`}
        footer={<SpaceBetween direction="horizontal" size="xs"><Button onClick={() => setShowPassword(false)}>Cancel</Button>
          <Button variant="primary" onClick={() => { doAction(selectedUser!.username, 'setTempPassword', { tempPassword: passwordForm.tempPassword }); setShowPassword(false); }}>Set Password</Button></SpaceBetween>}>
        <FormField label="Temporary Password" constraintText="User will be required to change on next login">
          <Input type="password" value={passwordForm.tempPassword} onChange={({ detail }) => setPasswordForm({ tempPassword: detail.value })} />
        </FormField>
      </Modal>
    </SpaceBetween>
  );
}
