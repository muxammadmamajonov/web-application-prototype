/** Self-service profile writes. */

import { useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../api';
import { queryKeys } from '../queryKeys';
import { useSession } from '@/store/session';
import type { MeResponse } from '@/types';

// ── profile (self) ───────────────────────────────────────────────────────────

interface ProfileUpdate {
  /** The person's own name. Telegram's `first_name` / `last_name` are absent on
   *  purpose: they are refreshed from validated initData on every sign-in, so a
   *  value written there would be reverted without anyone being told. */
  display_name?: string | null;
  phone?: string | null;
  email?: string | null;
  birthdate?: string | null;
  gender?: string | null;
  locale?: string;
}

/** Self-service profile edit — PATCH /auth/me, then sync the session store. */
export function useUpdateProfile() {
  const qc = useQueryClient();
  const setMe = useSession((s) => s.setMe);
  return useMutation({
    mutationFn: (body: ProfileUpdate) =>
      api.patch<MeResponse>('/auth/me', body).then((r) => r.data),
    onSuccess: (data) => {
      setMe(data);
      qc.setQueryData(queryKeys.me.root(), data);
    },
  });
}
