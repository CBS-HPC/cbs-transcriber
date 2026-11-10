"""Launcher: open the remembered library or ask which one, and switch libraries on request."""

import argparse
import os
import sqlite3
import threading
import webbrowser
from pathlib import Path

import uvicorn

from . import libraries, library_deletion, release_profile
from .app import create_app, create_chooser_app
from .storage import default_project_parent, legacy_library_root
from .store import SCHEMA_VERSION

# A paused media stream must not hold a library switch open; ordinary Ctrl+C is unchanged.
SWITCH_DRAIN_SECONDS = 5


def server_for(app, port):
    return uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", access_log=False)
    )


def run(server, on_started):
    """Serve until stopped; return whether the server started (startup can fail on a lease)."""

    def watch():
        while not server.started and not server.should_exit:
            threading.Event().wait(0.1)
        if server.started:
            on_started()

    threading.Thread(target=watch, daemon=True).start()
    server.run()
    return server.started


def stopper(server, chosen):
    def stop(selection):
        chosen.append(selection)
        server.config.timeout_graceful_shutdown = SWITCH_DRAIN_SECONDS
        server.should_exit = True

    return stop


def choose(port, opened):
    """Serve the start-up chooser until a library is picked; None after Ctrl+C."""
    chosen = []
    app = create_chooser_app()
    server = server_for(app, port)
    app.state.choose = stopper(server, chosen)
    print("Several libraries were found. Choose one in the browser.", flush=True)
    run(server, opened)
    return chosen[0] if chosen else None


def recover_library_deletions():
    """Settle interrupted permanent library deletions before any library opens (ADR 0144)."""
    try:
        outcomes = library_deletion.recover_all()
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Could not check unfinished library deletions: {exc}", flush=True)
        return
    for outcome in outcomes:
        if outcome["state"] == "attention":
            print(
                f"An unfinished library deletion needs attention: {outcome['message']} "
                "Use Retry under Library.",
                flush=True,
            )


def main():
    parser = argparse.ArgumentParser(description="CBS Transcriber local workspace")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data-dir", help="Managed library location (outside your Git repository)")
    parser.add_argument(
        "--choose-library", action="store_true", help="Ask which library to open at start"
    )
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument(
        "--projects-dir", help="Default parent folder for new projects (each gets a subfolder)"
    )
    parser.add_argument(
        "--create-shortcut",
        action="store_true",
        help="Create a per-user shortcut for this installation, then exit",
    )
    parser.add_argument(
        "--shortcut-location",
        choices=("applications", "desktop"),
        help="Shortcut location (default: Windows Start menu or macOS ~/Applications)",
    )
    parser.add_argument(
        "--profile",
        choices=sorted(release_profile.PROFILES),
        help="Release profile: which features are offered (default: "
        f"${release_profile.ENVIRONMENT}, then the packaged choice, then full)",
    )
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("Port must be between 1 and 65535.")
    if args.shortcut_location and not args.create_shortcut:
        parser.error("--shortcut-location requires --create-shortcut.")
    if args.create_shortcut:
        from .shortcuts import create_shortcut

        arguments = ["--port", str(args.port)]
        for flag, value in (("--data-dir", args.data_dir), ("--projects-dir", args.projects_dir)):
            if value:
                arguments.extend((flag, str(Path(value).expanduser().resolve())))
        if args.choose_library:
            arguments.append("--choose-library")
        if args.no_browser:
            arguments.append("--no-browser")
        if args.profile:
            arguments.extend(("--profile", args.profile))
        try:
            shortcut = create_shortcut(arguments, location=args.shortcut_location or "applications")
        except (OSError, ValueError) as exc:
            parser.error(str(exc))
        print(f"Created shortcut: {shortcut}", flush=True)
        print(
            "Keep this installation and its Python environment in place. Recreate the shortcut "
            "after moving or replacing either. Delete only the shortcut to remove it.",
            flush=True,
        )
        return
    try:
        # An unknown environment or packaged profile refuses startup (ADR 0145).
        release = release_profile.resolve(args.profile)
    except ValueError as exc:
        parser.error(str(exc))
    # Project storage must be absolute; resolve a relative --projects-dir against the cwd.
    parent = (
        Path(args.projects_dir).expanduser().resolve()
        if args.projects_dir
        else default_project_parent()
    )
    explicit = args.data_dir or os.environ.get("CBS_TRANSCRIBER_DATA_DIR")
    recover_library_deletions()
    try:
        target = (
            Path(explicit).expanduser().resolve()
            if explicit
            else libraries.startup_library(force=args.choose_library, schema=SCHEMA_VERSION)
        )
    except (ValueError, RuntimeError, OSError, sqlite3.Error) as exc:
        print(f"Could not select a library: {exc}", flush=True)
        raise SystemExit(1) from exc
    url = f"http://127.0.0.1:{args.port}"
    print(f"CBS Transcriber · {url}", flush=True)
    if release.name != "full":
        print(f"Release profile: {release.name}", flush=True)
    print("Press Ctrl+C to stop.", flush=True)
    pending_browser = [not args.no_browser]

    def open_browser():
        if pending_browser[0]:
            pending_browser[0] = False
            webbrowser.open(url)

    previous = None
    try:
        while True:
            if target is None:
                target = choose(args.port, open_browser)
                if target is None:
                    return
            try:
                if not explicit and not isinstance(target, libraries.SelectedLibrary):
                    raise ValueError("The library choice is no longer bound. Choose it again.")
                target_path = (
                    libraries.recheck(target, schema=SCHEMA_VERSION)
                    if isinstance(target, libraries.SelectedLibrary)
                    else target
                )
                app = create_app(target_path, project_parent=parent, release=release)
                root = app.state.store.root
                current = libraries.select(str(root), schema=SCHEMA_VERSION)
            except (ValueError, RuntimeError, OSError, sqlite3.Error) as exc:
                print(f"Could not open the library at {target}: {exc}", flush=True)
                if previous is None:
                    raise SystemExit(1) from exc
                target, previous = previous, None
                continue
            server, switched = server_for(app, args.port), []
            app.state.switch_library = stopper(server, switched)
            print(f"Library: {root}", flush=True)
            print(f"New projects: {parent}", flush=True)
            if root == legacy_library_root().resolve():
                print(
                    "Using your existing library at the earlier app-data location; it is never "
                    "moved automatically. New installations use <home>/CBS Transcriber/Library.",
                    flush=True,
                )

            # An explicit --data-dir is a one-off: it never changes which library opens
            # next time. Discovered, chosen and switched-to libraries are remembered.
            remember = not explicit or previous is not None

            def started(root=root, remember=remember):
                if remember:
                    try:
                        libraries.remember(root)
                    except OSError:
                        print(
                            "The library opened, but its default selection could not be "
                            "remembered. Check access to the library chooser settings.",
                            flush=True,
                        )
                open_browser()

            if not run(server, started):
                if previous is None:
                    raise SystemExit(1)
                print(f"Could not open {root}; reopening {previous}.", flush=True)
                target, previous = previous, None
                continue
            if not switched:
                return
            previous, target = current, switched[0]
            print(f"Opening library {target.path}…", flush=True)
    except KeyboardInterrupt:
        return


if __name__ == "__main__":
    main()
