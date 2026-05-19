-- Fase 1.2: Políticas de Seguridad (RLS)
-- Helper function para obtener el team_id de un usuario
CREATE OR REPLACE FUNCTION get_my_team_id()
RETURNS UUID AS $$
BEGIN
RETURN (SELECT team_id FROM team_members WHERE user_id = auth.uid() LIMIT 1);
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- 1. Políticas para la tabla profiles
-- Los usuarios pueden ver todos los perfiles, pero solo pueden actualizar el suyo.
CREATE POLICY "Allow users to view all profiles" ON public.profiles FOR SELECT USING (true);
CREATE POLICY "Allow users to update their own profile" ON public.profiles FOR UPDATE USING (auth.uid() = id);

-- 2. Políticas para la tabla teams
-- Los usuarios solo pueden ver los equipos de los que son miembros.
CREATE POLICY "Allow users to view their own teams" ON public.teams FOR SELECT USING (id IN (SELECT team_id FROM team_members WHERE user_id = auth.uid()));

-- 3. Políticas para la tabla team_members
-- Los usuarios pueden ver la lista de miembros de los equipos a los que pertenecen.
CREATE POLICY "Allow users to view members of their own teams" ON public.team_members FOR SELECT USING (team_id IN (SELECT team_id FROM team_members WHERE user_id = auth.uid()));

-- 4. Políticas para uploaded_files y dashboards
-- Los usuarios pueden realizar todas las acciones (CRUD) sobre archivos y dashboards que pertenecen a su equipo.
CREATE POLICY "Allow full access on team files" ON public.uploaded_files
FOR ALL USING (team_id = get_my_team_id())
WITH CHECK (team_id = get_my_team_id());

CREATE POLICY "Allow full access on team dashboards" ON public.dashboards
FOR ALL USING (team_id = get_my_team_id())
WITH CHECK (team_id = get_my_team_id());