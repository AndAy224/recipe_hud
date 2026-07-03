import uvicorn

from .config import CONFIG

if __name__ == "__main__":
    uvicorn.run("recipehud.main:app", host=CONFIG.host, port=CONFIG.port)
