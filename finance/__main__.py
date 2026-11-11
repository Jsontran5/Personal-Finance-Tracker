import socket
import threading
import time
import urllib.request
import webbrowser

import uvicorn

from .api import app

HOST = "127.0.0.1"


def open_browser(port):
    url = f"http://{HOST}:{port}"
    for _ in range(100):
        try:
            with urllib.request.urlopen(url, timeout=0.5):
                webbrowser.open(url)
                return
        except OSError:
            time.sleep(0.1)


def main():
    with socket.socket() as listener:
        listener.bind((HOST, 0))
        port = listener.getsockname()[1]
        print(f"Finance is starting at http://{HOST}:{port}")
        threading.Thread(target=open_browser, args=(port,), daemon=True).start()
        config = uvicorn.Config(app, host=HOST, port=port, access_log=False, proxy_headers=False)
        uvicorn.Server(config).run(sockets=[listener])


if __name__ == "__main__":
    main()


