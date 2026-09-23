import re

SENSITIVE_PATTERNS: dict[str, tuple[str, ...]] = {
    "pii": (
        "email",
        "e_mail",
        "phone",
        "mobile",
        "ssn",
        "social_security",
        "passport",
        "national_id",
        "dob",
        "date_of_birth",
        "birthdate",
        "birth_date",
        "birth_year",
        "address",
        "street",
        "zip",
        "postal",
        "first_name",
        "last_name",
        "full_name",
        "surname",
        "^name$",
    ),
    "credential": (
        "password",
        "passwd",
        "secret",
        "access_token",
        "auth_token",
        "refresh_token",
        "api_key",
        "apikey",
    ),
    "financial": (
        "salary",
        "wage",
        "compensation",
        "iban",
        "account_number",
        "card_number",
        "credit_card",
        "cvv",
    ),
    "health": ("diagnosis", "medical", "medical_record", "health_condition"),
}


def _normalize(name: str) -> str:
    value = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name)
    value = re.sub(r"[^a-z0-9]+", "_", value.lower())
    return re.sub(r"_+", "_", value).strip("_")


def classify_column(name: str) -> str | None:
    normalized = _normalize(name)
    for category, patterns in SENSITIVE_PATTERNS.items():
        for pattern in patterns:
            if pattern == "^name$":
                if normalized == "name":
                    return category
            elif re.search(rf"(^|_){re.escape(pattern)}(_|$)", normalized):
                return category
    return None
