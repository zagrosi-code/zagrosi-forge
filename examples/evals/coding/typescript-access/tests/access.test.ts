import assert from 'node:assert/strict';
import test from 'node:test';
import { canManage, changeRole } from '../src/access.ts';
import { setDisplayName } from '../src/profiles.ts';

const admin = { id: 'owner', tenant: 'one', role: 'admin' };
const member = { id: 'member', tenant: 'one', name: 'Ada', role: 'member' };
const store = () => ({ read: () => ({ ...member }), transaction: work => work(), write() {}, audit() {} });

test('admin changes member name and role', () => {
  assert.equal(setDisplayName(store(), admin, 'member', ' Grace ').name, 'Grace');
  assert.equal(changeRole(store(), admin, 'member', 'admin').role, 'admin');
});
test('missing identity cannot manage a member', () => {
  assert.equal(canManage(null, member), false);
  assert.throws(() => changeRole(store(), null, 'member', 'admin'), /unauthenticated/);
});
