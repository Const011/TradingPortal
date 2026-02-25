from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Trading Portal Backend"
    bybit_rest_base_url: str = "https://api.bybit.com"
    bybit_ws_public_spot_url: str = "wss://stream.bybit.com/v5/public/spot"
    cors_origins: list[str] = ["http://localhost:3000"]

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()

