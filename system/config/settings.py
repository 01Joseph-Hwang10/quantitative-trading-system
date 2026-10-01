"""
Declare environment variables here.

Example:

```python
from typing import Annotated

from dotenv import load_dotenv
from pydantic import AfterValidator
from pydantic_settings import BaseSettings

load_dotenv(".env")


def sanitize_url(url: str | None) -> str | None:
    return url.rstrip("/") if url else url


class Settings(BaseSettings):
    # Server settings
    env: str = "live"
    log_level: str = "INFO"
    api_key: str | None = None

    # Airflow settings
    airflow_url: Annotated[str, AfterValidator(sanitize_url)]
    airflow_username: str
    airflow_password: str
    airflow_fernet_key: str

    # Database settings
    db_user: str
    db_password: str
    db_host: str
    db_port: int | None = None
    db_name: str | None = None

    # Zitadel config
    zitadel_domain: Annotated[str, AfterValidator(sanitize_url)]
    zitadel_api_access_key_id: str
    zitadel_api_access_secret_key: str
```

"""
