"""Public application mode, separate from Plaid credentials and access gates."""
import os


def application_config(directory):
    value = None
    try:
        for line in (directory / '.env').read_text(encoding='utf-8-sig').splitlines():
            key, separator, setting = line.partition('=')
            if separator and key.strip() == 'APP_ENV':
                value = setting.strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    value = os.environ.get('APP_ENV', value or 'development').lower()
    if value not in {'development', 'production'}:
        raise ValueError('APP_ENV must be development or production.')
    return {'environment': value, 'unified': value == 'production', 'sandbox_enabled': value == 'development'}
