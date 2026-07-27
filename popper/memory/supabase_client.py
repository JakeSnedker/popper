"""Build a Supabase client with the current operator's Auth session."""

from popper.config import settings


def create_configured_client():
    try:
        from supabase import create_client
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("Supabase support requires `pip install -e .[supabase]`") from exc

    client = create_client(settings.supabase_url, settings.supabase_key)
    email_set = bool(settings.supabase_email)
    password_set = bool(settings.supabase_password)
    if email_set != password_set:
        raise RuntimeError("Set both POPPER_SUPABASE_EMAIL and POPPER_SUPABASE_PASSWORD")
    if email_set:
        client.auth.sign_in_with_password({
            "email": settings.supabase_email,
            "password": settings.supabase_password,
        })
    elif settings.auth_required:
        raise RuntimeError("POPPER_AUTH_REQUIRED needs operator Supabase email/password")
    return client
