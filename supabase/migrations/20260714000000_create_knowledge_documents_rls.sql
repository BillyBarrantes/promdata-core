-- =============================================================================
-- Fase 2.3: RLS policies for knowledge_documents & knowledge_document_chunks
-- These tables were created manually (no migration), so we add RLS after the fact.
-- =============================================================================

-- Enable RLS on both tables (idempotent)
ALTER TABLE IF EXISTS knowledge_documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS knowledge_document_chunks ENABLE ROW LEVEL SECURITY;

-- Drop existing policies if any (idempotent)
DROP POLICY IF EXISTS "Users can view their own knowledge documents" ON knowledge_documents;
DROP POLICY IF EXISTS "Users can insert their own knowledge documents" ON knowledge_documents;
DROP POLICY IF EXISTS "Users can update their own knowledge documents" ON knowledge_documents;
DROP POLICY IF EXISTS "Users can delete their own knowledge documents" ON knowledge_documents;
DROP POLICY IF EXISTS "Users can view their own knowledge document chunks" ON knowledge_document_chunks;
DROP POLICY IF EXISTS "Users can insert their own knowledge document chunks" ON knowledge_document_chunks;
DROP POLICY IF EXISTS "Users can delete their own knowledge document chunks" ON knowledge_document_chunks;

-- knowledge_documents: SELECT
CREATE POLICY "Users can view their own knowledge documents"
    ON knowledge_documents
    FOR SELECT
    USING (auth.uid()::text = user_id);

-- knowledge_documents: INSERT
CREATE POLICY "Users can insert their own knowledge documents"
    ON knowledge_documents
    FOR INSERT
    WITH CHECK (auth.uid()::text = user_id);

-- knowledge_documents: UPDATE
CREATE POLICY "Users can update their own knowledge documents"
    ON knowledge_documents
    FOR UPDATE
    USING (auth.uid()::text = user_id)
    WITH CHECK (auth.uid()::text = user_id);

-- knowledge_documents: DELETE
CREATE POLICY "Users can delete their own knowledge documents"
    ON knowledge_documents
    FOR DELETE
    USING (auth.uid()::text = user_id);

-- knowledge_document_chunks: SELECT
CREATE POLICY "Users can view their own knowledge document chunks"
    ON knowledge_document_chunks
    FOR SELECT
    USING (auth.uid()::text = user_id);

-- knowledge_document_chunks: INSERT
CREATE POLICY "Users can insert their own knowledge document chunks"
    ON knowledge_document_chunks
    FOR INSERT
    WITH CHECK (auth.uid()::text = user_id);

-- knowledge_document_chunks: DELETE
CREATE POLICY "Users can delete their own knowledge document chunks"
    ON knowledge_document_chunks
    FOR DELETE
    USING (auth.uid()::text = user_id);
