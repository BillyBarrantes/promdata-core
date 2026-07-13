-- Fase 0.2: Agregar RLS policies para tablas OAuth y Watch Targets
-- Garantizar que get_my_team_id() existe (definida en 0002_setup_rls_policies.sql)
CREATE OR REPLACE FUNCTION get_my_team_id()
RETURNS UUID AS $$
BEGIN
RETURN (SELECT team_id FROM team_members WHERE user_id = auth.uid() LIMIT 1);
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- 1. cloud_oauth_connections: solo el propietario puede ver sus conexiones OAuth
ALTER TABLE public.cloud_oauth_connections ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can view their own oauth connections"
    ON public.cloud_oauth_connections FOR SELECT
    USING (auth.uid() = user_id);

CREATE POLICY "Users can insert their own oauth connections"
    ON public.cloud_oauth_connections FOR INSERT
    WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Users can update their own oauth connections"
    ON public.cloud_oauth_connections FOR UPDATE
    USING (auth.uid() = user_id);

CREATE POLICY "Users can delete their own oauth connections"
    ON public.cloud_oauth_connections FOR DELETE
    USING (auth.uid() = user_id);

-- 2. cloud_watch_targets: por user_id
ALTER TABLE public.cloud_watch_targets ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can view their own watch targets"
    ON public.cloud_watch_targets FOR SELECT
    USING (auth.uid() = user_id);

CREATE POLICY "Users can insert their own watch targets"
    ON public.cloud_watch_targets FOR INSERT
    WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Users can update their own watch targets"
    ON public.cloud_watch_targets FOR UPDATE
    USING (auth.uid() = user_id);

CREATE POLICY "Users can delete their own watch targets"
    ON public.cloud_watch_targets FOR DELETE
    USING (auth.uid() = user_id);

-- 3. cloud_oauth_states: solo el usuario que inició el flujo OAuth puede ver/modificar
ALTER TABLE public.cloud_oauth_states ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can view their own oauth states"
    ON public.cloud_oauth_states FOR SELECT
    USING (auth.uid() = user_id);

CREATE POLICY "Users can insert their own oauth states"
    ON public.cloud_oauth_states FOR INSERT
    WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Users can update their own oauth states"
    ON public.cloud_oauth_states FOR UPDATE
    USING (auth.uid() = user_id);
