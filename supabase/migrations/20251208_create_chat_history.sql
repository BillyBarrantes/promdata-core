-- Tabla para el historial de chat
create table if not exists chat_messages (
  id uuid default gen_random_uuid() primary key,
  user_id uuid references auth.users not null,
  file_id uuid, -- Para asociar el chat a un archivo de datos específico
  role text check (role in ('user', 'assistant')) not null,
  content jsonb not null, -- Guardamos el contenido (puede ser texto simple o componentes ricos)
  created_at timestamp with time zone default timezone('utc'::text, now()) not null
);

-- Índices para búsqueda rápida
create index idx_chat_messages_file_id on chat_messages(file_id);
create index idx_chat_messages_user_id on chat_messages(user_id);

-- Políticas RLS (Seguridad)
alter table chat_messages enable row level security;

create policy "Usuarios pueden ver sus propios mensajes"
  on chat_messages for select
  using (auth.uid() = user_id);

create policy "Usuarios pueden insertar sus propios mensajes"
  on chat_messages for insert
  with check (auth.uid() = user_id);

create policy "Usuarios pueden borrar sus propios mensajes"
  on chat_messages for delete
  using (auth.uid() = user_id);
