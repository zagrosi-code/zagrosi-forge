export type Actor = { id: string; tenant: string; role: string } | null;
export type Member = { id: string; tenant: string; name: string; role: string };
export type Store = {
  read(id: string): Member | undefined;
  transaction<T>(work: () => T): T;
  write(member: Member): void;
  audit(event: { actor: string; member: string; action: string }): void;
};

// Legacy orchestration mixes permission policy, validation, and transaction work.
export function changeRole(store: Store, actor: Actor, id: string, role: string): Member {
  if (actor === null) { throw new Error('unauthenticated'); }
  if (actor.role !== 'admin') { throw new Error('forbidden'); }
  if (typeof id !== 'string' || id.length === 0) { throw new Error('invalid member'); }
  const member = store.read(id);
  if (!member || member.tenant !== actor.tenant) { throw new Error('not found'); }
  if (role !== 'admin' && role !== 'member') { throw new Error('invalid role'); }
  if (member.id === actor.id && role !== 'admin') { throw new Error('cannot demote self'); }
  const result = { ...member, role: role };
  const work = () => {
    store.write(result);
    store.audit({ actor: actor.id, member: member.id, action: 'role' });
    return { ...result };
  };
  return store.transaction(work);
}

export function canManage(actor: Actor, member: Member): boolean {
  if (actor === null) { return false; }
  if (actor.tenant !== member.tenant) { return false; }
  if (actor.role === 'admin') { return true; }
  return false;
}
