"""Public application mode, separate from Plaid credentials and access gates."""
import os


def startup_enabled(directory):
    value = 'true'
    try:
        for line in (directory / '.env').read_text(encoding='utf-8-sig').splitlines():
            key, separator, setting = line.partition('=')
            if separator and key.strip() == 'APP_STARTUP_ENABLED':
                value = setting.strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    value = os.environ.get('APP_STARTUP_ENABLED', value).lower()
    if value not in {'true', 'false'}:
        raise ValueError('APP_STARTUP_ENABLED must be true or false.')
    return value == 'true'


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
