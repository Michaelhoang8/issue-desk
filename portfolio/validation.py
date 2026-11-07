class ValidationError(ValueError):
    pass

class ConflictError(ValueError):
    pass

class NotFoundError(LookupError):
    pass

def text(data, key, limit=200):
    value = data.get(key)
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
        raise ValidationError(f'{key} must contain 1–{limit} characters')
    return value.strip()

def integer(data, key, minimum=0, maximum=1000000000):
    value = data.get(key)
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValidationError(f'{key} must be an integer from {minimum} to {maximum}')
    return value

def choice(data, key, choices):
    value = data.get(key)
    if value not in choices:
        raise ValidationError(f"{key} must be one of: {', '.join(choices)}")
    return value
