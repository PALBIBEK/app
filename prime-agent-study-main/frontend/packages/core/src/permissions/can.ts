export type User = {
  id: string;
  roles?: readonly string[];
};

export type Action = string;

export type Resource = {
  type: string;
  id?: string;
};

export type Permission = { allowed: true } | { allowed: false; reason: string };

/**
 * Phase 1 stub — always allows. Real predicates land in Phase 2.
 * Signature is fixed now so call sites can carry a reason.
 */
export function can(user: User, action: Action, resource?: Resource): Permission {
  void user;
  void action;
  void resource;
  return { allowed: true };
}
