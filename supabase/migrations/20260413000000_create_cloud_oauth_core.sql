CREATE TABLE public.cloud_oauth_connections (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  provider TEXT NOT NULL CHECK (provider IN ('google_drive', 'onedrive')),
  external_account_id TEXT,
  external_account_email TEXT,
  external_account_name TEXT,
  access_token TEXT NOT NULL,
  refresh_token TEXT,
  token_type TEXT,
  scopes TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
  expires_at TIMESTAMPTZ,
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'expired', 'revoked', 'error')),
  metadata JSONB NOT NULL DEFAULT '{}'::JSONB,
  last_refreshed_at TIMESTAMPTZ,
  last_error_at TIMESTAMPTZ,
  last_error TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (user_id, provider)
);

COMMENT ON TABLE public.cloud_oauth_connections IS 'Persistencia segura de tokens OAuth por usuario y proveedor cloud.';

CREATE TABLE public.cloud_watch_targets (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  connection_id UUID NOT NULL REFERENCES public.cloud_oauth_connections(id) ON DELETE CASCADE,
  user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  provider TEXT NOT NULL CHECK (provider IN ('google_drive', 'onedrive')),
  target_type TEXT NOT NULL CHECK (target_type IN ('file', 'folder', 'drive')),
  target_id TEXT NOT NULL,
  target_name TEXT,
  linked_file_id UUID REFERENCES public.uploaded_files(id) ON DELETE SET NULL,
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  metadata JSONB NOT NULL DEFAULT '{}'::JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (connection_id, target_id)
);

COMMENT ON TABLE public.cloud_watch_targets IS 'Targets declarativos que el usuario decide conectar (archivo/carpeta/drive).';

CREATE TABLE public.cloud_oauth_states (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  provider TEXT NOT NULL CHECK (provider IN ('google_drive', 'onedrive')),
  state TEXT NOT NULL UNIQUE,
  code_verifier TEXT NOT NULL,
  redirect_to TEXT,
  expires_at TIMESTAMPTZ NOT NULL,
  consumed_at TIMESTAMPTZ,
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'connected', 'error', 'cancelled')),
  error_message TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE public.cloud_oauth_states IS 'Estados efímeros del handshake OAuth2/PKCE para validación segura del callback.';

ALTER TABLE public.cloud_oauth_connections ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.cloud_watch_targets ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.cloud_oauth_states ENABLE ROW LEVEL SECURITY;

CREATE OR REPLACE FUNCTION public.touch_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER touch_cloud_oauth_connections_updated_at
BEFORE UPDATE ON public.cloud_oauth_connections
FOR EACH ROW EXECUTE FUNCTION public.touch_updated_at();

CREATE TRIGGER touch_cloud_watch_targets_updated_at
BEFORE UPDATE ON public.cloud_watch_targets
FOR EACH ROW EXECUTE FUNCTION public.touch_updated_at();

CREATE INDEX idx_cloud_oauth_connections_user_provider
  ON public.cloud_oauth_connections (user_id, provider);

CREATE INDEX idx_cloud_watch_targets_user_provider
  ON public.cloud_watch_targets (user_id, provider);

CREATE INDEX idx_cloud_oauth_states_lookup
  ON public.cloud_oauth_states (state, provider, expires_at);
