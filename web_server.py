import os
import uvicorn

from web.web_server import app


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("web.web_server:app", host="0.0.0.0", port=port, reload=False)
