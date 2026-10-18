import type { User } from '@/lib/api/types/auth.types';
import type { AuthUser } from '@/lib/store/authStore';

export function toStoreUser(user: User): AuthUser {
  return {
    id: user.id,
    email: user.email,
    name: user.name,
    role: user.role,
    avatar: user.avatar ?? undefined,
  };
}
