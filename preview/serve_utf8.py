from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
import argparse


class Utf8Handler(SimpleHTTPRequestHandler):
    def end_headers(self) -> None:
        if self.path.endswith(".html"):
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
        super().end_headers()


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve preview static pages with UTF-8 headers.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=4173)
    parser.add_argument("--directory", default=".")
    args = parser.parse_args()

    root = Path(args.directory).resolve()
    handler = lambda *handler_args, **kwargs: Utf8Handler(*handler_args, directory=str(root), **kwargs)
    server = HTTPServer((args.host, args.port), handler)
    print(f"Serving {root} at http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
