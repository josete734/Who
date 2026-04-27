-- Migration 0014: secondary email (logical only, payload is JSONB)
-- No DDL needed; SearchInput.email_secondary is parsed by Pydantic and
-- persisted inside cases.input_payload (jsonb).
SELECT 1;
