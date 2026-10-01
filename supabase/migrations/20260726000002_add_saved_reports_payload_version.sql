ALTER TABLE public.saved_reports
  ADD COLUMN IF NOT EXISTS payload_version text;
