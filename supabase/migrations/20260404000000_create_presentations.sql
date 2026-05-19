-- 1. Crear tabla presentations
CREATE TABLE public.presentations (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  file_id UUID REFERENCES public.uploaded_files(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Habilitar RLS para presentations
ALTER TABLE public.presentations ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Usuarios pueden ver sus propias presentaciones"
  ON public.presentations FOR SELECT
  USING (auth.uid() = user_id);

CREATE POLICY "Usuarios pueden insertar sus propias presentaciones"
  ON public.presentations FOR INSERT
  WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Usuarios pueden borrar sus propias presentaciones"
  ON public.presentations FOR DELETE
  USING (auth.uid() = user_id);

-- 2. Modificar tabla saved_reports
ALTER TABLE public.saved_reports
  ADD COLUMN presentation_id UUID REFERENCES public.presentations(id) ON DELETE CASCADE;

-- 3. Migración de datos heredados (Legacy Fallback)
-- Para todos los usuarios que tengan reportes huérfanos, crear la presentación "Dashboard Legacy"
DO $$ 
DECLARE 
    current_uid UUID;
    new_pres_id UUID;
BEGIN
    FOR current_uid IN 
        SELECT DISTINCT user_id FROM public.saved_reports WHERE presentation_id IS NULL 
    LOOP
        -- Insertar la presentación Legacy de forma segura para cada usuario con data vieja
        INSERT INTO public.presentations (user_id, file_id, name)
        VALUES (current_uid, NULL, 'Dashboard Legacy (Migración)')
        RETURNING id INTO new_pres_id;

        -- Migrar todos los reportes sueltos de este usuario a su nueva presentación
        UPDATE public.saved_reports 
        SET presentation_id = new_pres_id 
        WHERE user_id = current_uid AND presentation_id IS NULL;
    END LOOP;
END $$;
