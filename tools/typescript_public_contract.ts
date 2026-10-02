// External consumer: candidate code and tests cannot relax these API contracts.
import type * as Actual from "__CANDIDATE__/src/access.ts";
import type * as Expected from "__BASELINE__/src/access.ts";
import type { setDisplayName as actualName } from "__CANDIDATE__/src/profiles.ts";
import type { setDisplayName as expectedName } from "__BASELINE__/src/profiles.ts";

// Bidirectional generic comparison also rejects contracts erased to `any`.
type Same<A, B> =
  (<T>() => T extends A ? 1 : 2) extends (<T>() => T extends B ? 1 : 2)
    ? (<T>() => T extends B ? 1 : 2) extends (<T>() => T extends A ? 1 : 2) ? true : false
    : false;
type Check<T extends true> = T;

type PublicContract = [
  Check<Same<Actual.Actor, Expected.Actor>>,
  Check<Same<Actual.Member, Expected.Member>>,
  Check<Same<Actual.Store, Expected.Store>>,
  Check<Same<typeof Actual.changeRole, typeof Expected.changeRole>>,
  Check<Same<typeof Actual.canManage, typeof Expected.canManage>>,
  Check<Same<typeof actualName, typeof expectedName>>,
];
