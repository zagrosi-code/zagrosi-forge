import type { Actor, Member, Store } from './access.ts';

export function setDisplayName(store: Store, actor: Actor, id: string, name: string): Member {
  if (actor === null) { throw new Error('unauthenticated'); }
  if (actor.role !== 'admin') { throw new Error('forbidden'); }
  if (typeof id !== 'string' || id.length === 0) { throw new Error('invalid member'); }
  const member = store.read(id);
  if (!member || member.tenant !== actor.tenant) { throw new Error('not found'); }
  if (typeof name !== 'string' || name.trim().length === 0 || name.length > 80) {
    throw new Error('invalid name');
  }
  const result = { ...member, name: name.trim() };
  const work = () => {
    store.write(result);
    store.audit({ actor: actor.id, member: member.id, action: 'name' });
    return { ...result };
  };
  return store.transaction(work);
}
