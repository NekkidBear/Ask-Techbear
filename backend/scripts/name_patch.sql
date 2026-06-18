-- name_patch.sql
-- Gymnarctos Studios LLC — Ask TechBear
-- Updates the three alliterative/generated demo names to natural ones.
-- Safe to run multiple times (idempotent).

UPDATE questions SET attendee_name = 'Mai'   WHERE id = 13;   -- was Fixer_Fiona
UPDATE questions SET attendee_name = 'Ivan'  WHERE id = 22;   -- was Download_Debbie
UPDATE questions SET attendee_name = 'Ahmed' WHERE id = 20;   -- was Muted_Milo