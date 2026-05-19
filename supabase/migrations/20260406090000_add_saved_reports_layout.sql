ALTER TABLE public.saved_reports
  ADD COLUMN IF NOT EXISTS layout_x integer,
  ADD COLUMN IF NOT EXISTS layout_y integer,
  ADD COLUMN IF NOT EXISTS layout_w integer,
  ADD COLUMN IF NOT EXISTS layout_h integer;

UPDATE public.saved_reports
SET
  layout_x = NULLIF(content->'layout'->>'x', '')::integer,
  layout_y = NULLIF(content->'layout'->>'y', '')::integer,
  layout_w = NULLIF(content->'layout'->>'w', '')::integer,
  layout_h = NULLIF(content->'layout'->>'h', '')::integer
WHERE content ? 'layout';

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_policies
    WHERE schemaname = 'public'
      AND tablename = 'saved_reports'
      AND policyname = 'Usuarios pueden actualizar sus propios reportes'
  ) THEN
    CREATE POLICY "Usuarios pueden actualizar sus propios reportes"
      ON public.saved_reports
      FOR UPDATE
      USING (auth.uid() = user_id)
      WITH CHECK (auth.uid() = user_id);
  END IF;
END $$;
