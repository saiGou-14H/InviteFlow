import uvicorn

from inviteflow.config import get_settings


def run() -> None:
    settings = get_settings()
    uvicorn.run(
        "inviteflow.app:create_app",
        factory=True,
        host=settings.host,
        port=settings.port,
    )


if __name__ == "__main__":
    run()
