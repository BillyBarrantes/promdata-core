CREATE TABLE public.cloud_sync_jobs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  team_id UUID NOT NULL REFERENCES public.teams(id) ON DELETE CASCADE,
  user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  watch_target_id UUID NOT NULL REFERENCES public.cloud_watch_targets(id) ON DELETE CASCADE,
  linked_file_id UUID REFERENCES public.uploaded_files(id) ON DELETE SET NULL,
  provider TEXT NOT NULL CHECK (provider IN ('google_drive', 'onedrive')),
  target_id TEXT NOT NULL,
  revision_signature TEXT NOT NULL,
  trigger_source TEXT NOT NULL DEFAULT 'poll' CHECK (trigger_source IN ('poll', 'webhook', 'manual')),
  status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'skipped', 'superseded')),
  attempt_count INTEGER NOT NULL DEFAULT 0,
  error_summary TEXT,
  metadata JSONB NOT NULL DEFAULT '{}'::JSONB,
  started_at TIMESTAMPTZ,
  finished_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (watch_target_id, revision_signature)
);

COMMENT ON TABLE public.cloud_sync_jobs IS 'Bitácora operativa e idempotente de sincronizaciones cloud por revisión remota.';

ALTER TABLE public.cloud_sync_jobs ENABLE ROW LEVEL SECURITY;

CREATE TRIGGER touch_cloud_sync_jobs_updated_at
BEFORE UPDATE ON public.cloud_sync_jobs
FOR EACH ROW EXECUTE FUNCTION public.touch_updated_at();

CREATE INDEX idx_cloud_sync_jobs_user_status
  ON public.cloud_sync_jobs (user_id, status);

CREATE INDEX idx_cloud_sync_jobs_watch_target_status
  ON public.cloud_sync_jobs (watch_target_id, status);

CREATE POLICY "Allow full access on team cloud sync jobs" ON public.cloud_sync_jobs
FOR ALL USING (team_id = get_my_team_id())
WITH CHECK (team_id = get_my_team_id());
