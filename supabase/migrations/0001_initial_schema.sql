-- Fase 1: Creación del Esquema Inicial para la App de BI Conversacional
-- 1. Tabla para los Equipos/Espacios de Trabajo
CREATE TABLE public.teams (
id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
team_name TEXT NOT NULL,
created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE public.teams IS 'Almacena los equipos o espacios de trabajo de los usuarios.';

-- Habilitar Row Level Security (RLS) para la tabla de equipos
ALTER TABLE public.teams ENABLE ROW LEVEL SECURITY;

-- 2. Tabla de Perfiles de Usuario
-- Esta tabla extenderá la tabla auth.users de Supabase para añadir metadatos.
CREATE TABLE public.profiles (
id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
full_name TEXT,
-- Columna JSONB para almacenar preferencias de usuario (colores, etc.)
preferences JSONB
);
COMMENT ON TABLE public.profiles IS 'Almacena datos públicos del perfil de cada usuario.';

-- Habilitar RLS para la tabla de perfiles
ALTER TABLE public.profiles ENABLE ROW LEVEL SECURITY;

-- 3. Tabla Intermedia para vincular Usuarios y Equipos (Miembros)
CREATE TABLE public.team_members (
team_id UUID NOT NULL REFERENCES public.teams(id) ON DELETE CASCADE,
user_id UUID NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
role TEXT NOT NULL DEFAULT 'member', -- Ej: 'admin', 'editor', 'member'
PRIMARY KEY (team_id, user_id)
);
COMMENT ON TABLE public.team_members IS 'Tabla de unión para gestionar la pertenencia de usuarios a equipos.';

-- Habilitar RLS para la tabla de miembros
ALTER TABLE public.team_members ENABLE ROW LEVEL SECURITY;

-- 4. Tabla para Metadatos de Archivos Subidos
CREATE TABLE public.uploaded_files (
id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
team_id UUID NOT NULL REFERENCES public.teams(id) ON DELETE CASCADE,
user_id UUID NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
file_name TEXT NOT NULL,
storage_path TEXT NOT NULL UNIQUE,
created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE public.uploaded_files IS 'Registra los metadatos de los archivos subidos a Supabase Storage.';

-- Habilitar RLS para la tabla de archivos
ALTER TABLE public.uploaded_files ENABLE ROW LEVEL SECURITY;

-- 5. Tabla para Dashboards Guardados
CREATE TABLE public.dashboards (
id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
team_id UUID NOT NULL REFERENCES public.teams(id) ON DELETE CASCADE,
file_id UUID REFERENCES public.uploaded_files(id) ON DELETE SET NULL,
title TEXT NOT NULL,
report_data JSONB NOT NULL,
created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE public.dashboards IS 'Almacena los dashboards y reportes generados vinculados a un equipo.';

-- Habilitar RLS para la tabla de dashboards
ALTER TABLE public.dashboards ENABLE ROW LEVEL SECURITY;