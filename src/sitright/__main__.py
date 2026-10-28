import argparse

from . import __version__


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="sitright",
        description="Reminds you to sit up straight, blink and take breaks, using your webcam.",
    )
    parser.add_argument("--camera", type=int, default=0, help="camera index (default: 0)")
    parser.add_argument(
        "--preview", action="store_true", help="open a camera window with live readings"
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = parser.parse_args()

    # Imported late so --help and --version work instantly.
    from .app import run_preview, run_tray

    if args.preview:
        run_preview(args.camera)
    else:
        run_tray(args.camera)


if __name__ == "__main__":
    main()
