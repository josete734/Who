-- Migration 0017: drop zombie settings (RapidAPI, Hunter, Numverify, IntelX)
-- After Wave 1 sanitization, these keys are no longer consumed by any collector.
-- The rapidapi_generic.py module was removed entirely; its functionality is
-- duplicated by native collectors (wa_me, holehe+sherlock+whatsmyname,
-- smtp_rcpt+dns_mx, phoneinfoga, reverse_image).
BEGIN;

DELETE FROM app_settings
 WHERE key IN ('RAPIDAPI_KEY', 'HUNTER_API_KEY', 'NUMVERIFY_API_KEY', 'INTELX_FREE_KEY');

COMMIT;
