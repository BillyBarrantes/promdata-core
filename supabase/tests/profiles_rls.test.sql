BEGIN;
SELECT plan(3);

INSERT INTO auth.users (id, email)
VALUES
  ('11111111-1111-4111-8111-111111111111', 'profiles-owner@example.test'),
  ('22222222-2222-4222-8222-222222222222', 'profiles-other@example.test');

SET LOCAL ROLE authenticated;
SET LOCAL request.jwt.claim.sub = '11111111-1111-4111-8111-111111111111';

SELECT results_eq(
  $$SELECT id FROM public.profiles WHERE id = '11111111-1111-4111-8111-111111111111'::uuid$$,
  $$VALUES ('11111111-1111-4111-8111-111111111111'::uuid)$$,
  'a user can read their own profile'
);

SELECT results_eq(
  $$UPDATE public.profiles
    SET preferences = '{"currency":"USD"}'::jsonb
    WHERE id = '11111111-1111-4111-8111-111111111111'::uuid
    RETURNING preferences$$,
  $$VALUES ('{"currency":"USD"}'::jsonb)$$,
  'a user can still update their own preferences'
);

SET LOCAL request.jwt.claim.sub = '22222222-2222-4222-8222-222222222222';

SELECT is_empty(
  $$SELECT id FROM public.profiles WHERE id = '11111111-1111-4111-8111-111111111111'::uuid$$,
  'another user cannot read the owner profile'
);

SELECT * FROM finish();
ROLLBACK;
