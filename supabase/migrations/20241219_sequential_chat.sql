-- Migration: Add parent_id to chat_messages for sequential chat context
ALTER TABLE chat_messages 
ADD COLUMN IF NOT EXISTS parent_id UUID REFERENCES chat_messages(id);

-- Optional: Index for performance on thread lookups
CREATE INDEX IF NOT EXISTS idx_chat_messages_parent_id ON chat_messages(parent_id);
