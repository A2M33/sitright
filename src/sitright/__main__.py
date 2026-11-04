import argparse

from . import __version__, single_instance


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="sitright",
        description="Reminds you to sit up straight, blink and take breaks, using your webcam.",
    )
    parser.add_argument(
        "--camera",
        type=int,
        default=None,
        help="camera index for this run (default: the one picked in the tray menu, else 0)",
    )
    parser.add_argument(
        "--preview", action="store_true", help="open a camera window with live readings"
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = parser.parse_args()

    # Imported late so --help and --version work instantly.
    if args.preview:
        from .app import run_preview

        run_preview(args.camera)
        return
    release = single_instance.acquire()
    if release is None:
        single_instance.tell_user(
            "SitRight is already running. Look for its icon in the system tray."
        )
        return
    from .app import run_tray

    try:
        run_tray(args.camera)
    finally:
        release()


if __name__ == "__main__":
    main()
