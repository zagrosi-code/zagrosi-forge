// External behavioral oracle: ordering, public API, tenant isolation, rollback.
import assert from 'node:assert/strict';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import * as baselineAccess from '../examples/evals/coding/typescript-access/src/access.ts';
import * as baselineProfiles from '../examples/evals/coding/typescript-access/src/profiles.ts';

const candidateAccess = await import(pathToFileURL(path.resolve(process.argv[2], 'src/access.ts')).href);
const candidateProfiles = await import(pathToFileURL(path.resolve(process.argv[2], 'src/profiles.ts')).href);
let assertions = 0;
for (const [actual, expected] of [[candidateAccess, baselineAccess], [candidateProfiles, baselineProfiles]]) {
  assert.deepEqual(Object.keys(actual), Object.keys(expected)); assertions++;
}
function exercise(fn, actor, id, value, failure) {
  actor = structuredClone(actor);
  let state = [{ id: 'member', tenant: 'one', name: 'Ada', role: 'member' },
               { id: 'owner', tenant: 'one', name: 'Owner', role: 'admin' }];
  let audit = [], calls = [];
  const store = {
    read(id) { calls.push(['read', id]); return structuredClone(state.find(row => row.id === id)); },
    write(member) {
      calls.push(['write', member]);
      if (failure === 'write') throw new Error('write unavailable');
      state = state.map(row => row.id === member.id ? structuredClone(member) : row);
    },
    audit(event) { calls.push(['audit', event]); if (failure === 'audit') throw new Error('audit unavailable'); audit.push(event); },
    transaction(work) {
      calls.push(['begin']); const saved = structuredClone([state, audit]);
      try { const result = work(); calls.push(['commit']); return result; }
      catch (error) { [state, audit] = saved; calls.push(['rollback']); throw error; }
    },
  };
  let result, error;
  try { result = fn(store, actor, id, value); }
  catch (caught) { error = { name: caught.name, message: caught.message }; }
  return { state, audit, calls, result, error, actor };
}
const actors = [null, { id: 'owner', tenant: 'one', role: 'member' },
                { id: 'owner', tenant: 'one', role: 'admin' }, { id: 'owner', tenant: 'two', role: 'admin' }];
for (const [name, candidate, baseline, values] of [
  ['role', candidateAccess.changeRole, baselineAccess.changeRole, ['admin', 'member', 'invalid', null]],
  ['name', candidateProfiles.setDisplayName, baselineProfiles.setDisplayName, [' Grace ', '', 'x'.repeat(81), null]],
]) {
  for (const actor of actors) for (const id of ['member', 'owner', 'missing', ''])
    for (const value of values) for (const failure of [null, 'write', 'audit']) {
      assert.deepEqual(exercise(candidate, actor, id, value, failure), exercise(baseline, actor, id, value, failure),
                       `${name}: ${JSON.stringify({ actor, id, value, failure })}`); assertions++;
    }
}
for (const actor of actors) for (const tenant of ['one', 'two']) {
  const member = { id: 'member', tenant, name: 'Ada', role: 'member' };
  const actualActor = structuredClone(actor), actualMember = structuredClone(member);
  assert.deepEqual([candidateAccess.canManage(actualActor, actualMember), actualActor, actualMember],
                   [baselineAccess.canManage(actor, member), actor, member]); assertions++;
}
console.log(JSON.stringify({ case: process.argv[3], assertions }));
