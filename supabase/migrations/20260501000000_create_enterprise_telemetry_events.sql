CREATE TABLE public.enterprise_telemetry_events (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  team_id UUID REFERENCES public.teams(id) ON DELETE SET NULL,
  user_id UUID REFERENCES auth.users(id) ON DELETE CASCADE,
  telemetry_version TEXT NOT NULL DEFAULT 'phase6.v1',
  event_source TEXT NOT NULL DEFAULT 'backend',
  metric_domain TEXT NOT NULL CHECK (metric_domain IN ('usage', 'confidence', 'latency', 'product')),
  metric_name TEXT NOT NULL,
  metric_value DOUBLE PRECISION NOT NULL,
  metric_unit TEXT NOT NULL DEFAULT 'count',
  dimensions JSONB NOT NULL DEFAULT '{}'::JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE public.enterprise_telemetry_events IS 'Bitácora persistente de métricas enterprise para uso, confianza, producto y latencia.';

ALTER TABLE public.enterprise_telemetry_events ENABLE ROW LEVEL SECURITY;

CREATE INDEX idx_enterprise_telemetry_events_user_created_at
  ON public.enterprise_telemetry_events (user_id, created_at DESC);

CREATE INDEX idx_enterprise_telemetry_events_team_created_at
  ON public.enterprise_telemetry_events (team_id, created_at DESC);

CREATE INDEX idx_enterprise_telemetry_events_metric_window
  ON public.enterprise_telemetry_events (metric_domain, metric_name, created_at DESC);

CREATE POLICY "Allow scoped access on enterprise telemetry events" ON public.enterprise_telemetry_events
FOR ALL USING (
  (team_id IS NOT NULL AND team_id = get_my_team_id())
  OR (team_id IS NULL AND user_id = auth.uid())
)
WITH CHECK (
  (team_id IS NOT NULL AND team_id = get_my_team_id())
  OR (team_id IS NULL AND user_id = auth.uid())
);
