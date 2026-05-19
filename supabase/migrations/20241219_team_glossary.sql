-- Migration: Add team_id to business_glossary for Team Knowledge Base
ALTER TABLE business_glossary 
ADD COLUMN IF NOT EXISTS team_id UUID REFERENCES teams(id);

-- Update RLS Policies to allow Team Access
-- Note: You might need to drop existing policies if they conflict. 
-- This assumes standard Supabase RLS setup.

-- Policy: Select glossary items for my team
CREATE POLICY "Enable read access for team members" ON business_glossary
FOR SELECT
USING (
  auth.uid() IN (
    SELECT user_id FROM team_members WHERE team_id = business_glossary.team_id
  )
  OR user_id = auth.uid() -- Keep backward compatibility for personal items
);
