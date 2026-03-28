"""Entry point: python -m distill_app"""
import argparse
from distill_app.server import launch

def main():
    parser = argparse.ArgumentParser(description="Distill — document to Markdown converter")
    parser.add_argument("--host",       default="127.0.0.1", help="Host to bind (default: 127.0.0.1)")
    parser.add_argument("--port",       default=7860, type=int, help="Port to listen on (default: 7860)")
    parser.add_argument("--no-browser", action="store_true", help="Don't open browser automatically")
    args = parser.parse_args()

    launch(host=args.host, port=args.port, inbrowser=not args.no_browser)

if __name__ == "__main__":
    main()
