-- Migration: Add onboarding trigger for new users
-- Purpose: Auto-creates profile, team, and team membership when a user signs up
-- Context: This trigger already exists in production (created manually in Supabase)
--          but was never versioned in migrations. This is schema drift F-ENV-1.
--          This migration reproduces the exact logic found in production.
-- Safety: CREATE OR REPLACE FUNCTION + DROP TRIGGER IF EXISTS = fully idempotent

CREATE OR REPLACE FUNCTION public.handle_new_user()
 RETURNS trigger
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'public'
AS $function$
DECLARE
  new_team_id UUID;
  user_name TEXT;
BEGIN
  -- Intenta obtener el nombre completo desde diferentes posibles ubicaciones en los metadatos
  -- Si no lo encuentra, usa el email como último recurso.
  user_name := COALESCE(
    NEW.raw_user_meta_data->>'full_name',
    NEW.raw_user_meta_data->>'name',
    NEW.raw_user_meta_data->>'user_name',
    NEW.email
  );

  -- Crear un perfil para el nuevo usuario
  INSERT INTO public.profiles (id, full_name)
  VALUES (NEW.id, user_name);

  -- Crear un nuevo equipo para el usuario
  INSERT INTO public.teams (team_name)
  VALUES (user_name || '''s Team')
  RETURNING id INTO new_team_id;

  -- Vincular al nuevo usuario con su nuevo equipo
  INSERT INTO public.team_members (user_id, team_id, role)
  VALUES (NEW.id, new_team_id, 'admin');

  RETURN NEW;
END;
$function$;

DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
CREATE TRIGGER on_auth_user_created AFTER INSERT ON auth.users FOR EACH ROW EXECUTE FUNCTION handle_new_user();
