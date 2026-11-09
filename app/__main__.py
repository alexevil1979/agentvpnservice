import uvicorn

from app.config import get_settings


def main() -> None:
    s = get_settings()
    uvicorn.run("app.main:app", host=s.bind_host, port=s.bind_port, reload=False)


if __name__ == "__main__":
    main()
