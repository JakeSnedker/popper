# Team authentication activation

1. Create the three users in Supabase Authentication.
2. Configure `POPPER_SUPABASE_EMAIL` and `POPPER_SUPABASE_PASSWORD` on every worker.
3. Add the browser login and bearer-token handling to the GUI.
4. Add `TeamAuthMiddleware` to FastAPI and set `POPPER_AUTH_REQUIRED=true`.
5. Apply `supabase/migrations/0002_team_auth_rls.sql`.
6. Insert the first admin through the Supabase SQL editor or service role:

```sql
insert into public.team_members (user_id, email, role)
select id, email, 'admin' from auth.users where email = '<admin-email>';
```

7. The admin can then add the two remaining Auth users to `team_members`.

Do not enable RLS before steps 1-4 are working; anonymous dashboard and worker requests are denied after activation.
