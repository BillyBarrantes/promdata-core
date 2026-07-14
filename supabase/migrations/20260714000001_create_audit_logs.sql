-- =============================================================================
-- Fase 3.1: audit_logs — Enterprise audit trail
-- Captura cada operación mutante (POST/PUT/PATCH/DELETE) + acceso a datos
-- =============================================================================

CREATE TABLE IF NOT EXISTS audit_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id TEXT NOT NULL,
    event TEXT NOT NULL,
    method TEXT NOT NULL,
    path TEXT NOT NULL,
    status_code INTEGER,
    ip_address TEXT,
    user_agent TEXT,
    request_body_preview TEXT,
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Index for queries by user
CREATE INDEX IF NOT EXISTS idx_audit_logs_user_id ON audit_logs (user_id);
-- Index for queries by time
CREATE INDEX IF NOT EXISTS idx_audit_logs_created_at ON audit_logs (created_at DESC);
-- Index for queries by event type
CREATE INDEX IF NOT EXISTS idx_audit_logs_event ON audit_logs (event);

-- Enable RLS
ALTER TABLE audit_logs ENABLE ROW LEVEL SECURITY;

-- Users can only see their own audit logs
CREATE POLICY "Users can view their own audit logs"
    ON audit_logs
    FOR SELECT
    USING (auth.uid()::text = user_id);

-- Only the service role can insert audit logs (app backend)
CREATE POLICY "Service role can insert audit logs"
    ON audit_logs
    FOR INSERT
    WITH CHECK (true);
