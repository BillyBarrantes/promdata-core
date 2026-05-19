-- Tabla para guardar los reportes generados
create table if not exists saved_reports (
  id uuid default gen_random_uuid() primary key,
  user_id uuid references auth.users not null,
  title text not null,
  content jsonb not null, -- Guardamos el array de componentes
  file_id uuid, -- Opcional: referencia al archivo original
  created_at timestamp with time zone default timezone('utc'::text, now()) not null
);

-- Políticas RLS (Seguridad)
alter table saved_reports enable row level security;

create policy "Usuarios pueden ver sus propios reportes"
  on saved_reports for select
  using (auth.uid() = user_id);

create policy "Usuarios pueden insertar sus propios reportes"
  on saved_reports for insert
  with check (auth.uid() = user_id);

create policy "Usuarios pueden borrar sus propios reportes"
  on saved_reports for delete
  using (auth.uid() = user_id);
