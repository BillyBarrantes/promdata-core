-- Create table for Business Memory (Glossary)
create table if not exists business_glossary (
  id uuid default gen_random_uuid() primary key,
  user_id uuid references auth.users(id) on delete cascade not null,
  term text not null,
  definition text not null,
  created_at timestamp with time zone default timezone('utc'::text, now()) not null,
  unique(user_id, term) -- Prevent duplicate terms for the same user
);

-- Enable RLS
alter table business_glossary enable row level security;

-- Policies
create policy "Users can insert their own glossary items"
  on business_glossary for insert
  with check (auth.uid() = user_id);

create policy "Users can view their own glossary items"
  on business_glossary for select
  using (auth.uid() = user_id);

create policy "Users can update their own glossary items"
  on business_glossary for update
  using (auth.uid() = user_id);

create policy "Users can delete their own glossary items"
  on business_glossary for delete
  using (auth.uid() = user_id);
