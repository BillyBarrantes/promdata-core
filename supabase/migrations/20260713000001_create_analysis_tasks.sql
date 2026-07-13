-- Fase 0.2: Crear tabla analysis_tasks + RLS policies
-- Esta tabla se creó originalmente en Supabase Dashboard (no via migración).
-- Esta migración oficializa el DDL y habilita RLS con políticas por user_id.

CREATE TABLE IF NOT EXISTS public.analysis_tasks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    file_id UUID REFERENCES public.uploaded_files(id) ON DELETE SET NULL,
    prompt TEXT NOT NULL,
    task_type TEXT NOT NULL DEFAULT 'analysis',
    status TEXT NOT NULL DEFAULT 'pending',
    results_json JSONB,
    error_message TEXT,
    parent_task_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ
);

ALTER TABLE public.analysis_tasks ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can view their own tasks"
    ON public.analysis_tasks FOR SELECT
    USING (auth.uid() = user_id);

CREATE POLICY "Users can insert their own tasks"
    ON public.analysis_tasks FOR INSERT
    WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Users can update their own tasks"
    ON public.analysis_tasks FOR UPDATE
    USING (auth.uid() = user_id);

CREATE POLICY "Users can delete their own tasks"
    ON public.analysis_tasks FOR DELETE
    USING (auth.uid() = user_id);
