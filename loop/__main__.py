"""
loop — you are what you consume, and your body keeps score.

  python3 -m loop install                      # start everything now + at every login (launchd)
  python3 -m loop uninstall                    # stop it and remove from login
  python3 -m loop run [--host H] [--port P]    # everything, in the foreground: tracker + syncs + UI
  python3 -m loop connect garmin|spotify|lastfm|usda|claude   # one-time logins (claude = Ask loop)
  python3 -m loop sync garmin|lastfm [--days N]  # pull a feed right now (e.g. backfill)
  python3 -m loop serve | track                # just the UI, or just the Mac tracker
  python3 -m loop import-mynetdiary FILE.xls…  # your MyNetDiary food/exercise/water history
  python3 -m loop usda-build                   # build the offline USDA food database from data/usda/raw
  python3 -m loop reset-password               # forgot your loop password (run on the Mac)
  python3 -m loop publish-brain [--dry-run|--no-push|--on|--off]   # public topic snapshot → your website (monthly if on)
  python3 -m loop import-claude EXPORT.zip     # your claude.ai conversations, for the brain graph (titles + keywords)
  python3 -m loop import-health FILE.csv       # any daily health CSV (see sources/healthcsv.py)
  python3 -m loop demo                         # fill ~/.loop/demo.db with fake data and serve it
  python3 -m loop today                        # print today's predictions
  python3 -m loop test                         # a throwaway test login (test / test), reset every start, at localhost:8767

Global: --db PATH (default ~/.loop/loop.db, or $LOOP_DB)
"""
import argparse
import json
import sys
from datetime import date
from pathlib import Path

from . import db


def main(argv=None):
    p = argparse.ArgumentParser(prog="loop", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8765)
    y = sub.add_parser("sync")
    y.add_argument("source", choices=["garmin", "spotify", "lastfm", "all"])
    y.add_argument("--days", type=int)
    t = sub.add_parser("track")
    t.add_argument("--interval", type=int, default=5)
    r = sub.add_parser("run")
    r.add_argument("--host", default="127.0.0.1")
    r.add_argument("--port", type=int, default=8765)
    r.add_argument("--no-track", action="store_true")
    c = sub.add_parser("connect")
    c.add_argument("source", choices=["garmin", "spotify", "lastfm", "usda", "claude", "zotero"])
    sub.add_parser("install")
    sub.add_parser("uninstall")
    sub.add_parser("usda-build")
    sub.add_parser("reset-password")
    mnd = sub.add_parser("import-mynetdiary")
    mnd.add_argument("paths", nargs="+")
    pb = sub.add_parser("publish-brain", help="write the public brain snapshot into your site repo (and push, if publishing is on)")
    pb.add_argument("--dry-run", action="store_true", help="print the snapshot only")
    pb.add_argument("--on", action="store_true", help="turn monthly auto-publishing on")
    pb.add_argument("--off", action="store_true", help="turn monthly auto-publishing off")
    pb.add_argument("--no-push", action="store_true", help="write the file but don't commit or push")
    ic = sub.add_parser("import-claude", help="import your claude.ai data export (the zip, or conversations.json)")
    ic.add_argument("file")
    i = sub.add_parser("import-health")
    i.add_argument("file")
    d = sub.add_parser("demo")
    d.add_argument("--port", type=int, default=8765)
    d.add_argument("--no-serve", action="store_true")
    sub.add_parser("today")
    tt = sub.add_parser("test", help="a throwaway test account (test / test), wiped and re-seeded every start")
    tt.add_argument("--port", type=int, default=8767)
    h = sub.add_parser("hub", help="run a loop hub (the shared server for Together)")
    h.add_argument("--host", default="127.0.0.1")
    h.add_argument("--port", type=int, default=8787)
    h.add_argument("--hub-db", default=str(Path.home() / ".loop-hub" / "hub.db"))
    a = p.parse_args(argv)

    if a.cmd == "hub":
        from .hub import serve as hub_serve
        try:
            hub_serve(a.hub_db, a.host, a.port)
        except KeyboardInterrupt:
            print("\nstopped")
        return

    if a.cmd == "test":
        import os
        real = Path(db.default_path()).resolve()
        path = Path.home() / ".loop" / "test.db"
        if path.resolve() == real:
            sys.exit("refusing: the test database can't be your real one")
        os.environ["LOOP_NO_KEYCHAIN"] = "1"                                         # never read or write your real Keychain
        os.environ["LOOP_EGRESS_LOG"] = str(Path.home() / ".loop" / "test-egress.log")  # keep test traffic out of your log
        from .sources import garmin as _g
        _g.TOKENS = Path.home() / ".loop" / "test-garmin_tokens"                       # never your Garmin session
        Path(str(Path.home() / ".loop" / "test-avatar.img")).unlink(missing_ok=True)
        for suffix in ("", "-wal", "-shm"):
            Path(str(path) + suffix).unlink(missing_ok=True)
        con = db.connect(str(path))
        from .demo import seed, make_test_account, TEST_USERNAME
        seed(con)
        make_test_account(con)
        print(f"test account ready (fresh demo data): http://localhost:{a.port}  ·  username {TEST_USERNAME}, password: see loop/demo.py")
        from .server import serve
        serve(con, "127.0.0.1", a.port)
        return

    if a.cmd == "demo":
        path = a.db or str(Path.home() / ".loop" / "demo.db")
        for suffix in ("", "-wal", "-shm"):  # stale WAL files would corrupt the fresh demo db
            Path(path + suffix).unlink(missing_ok=True)
        con = db.connect(path)
        from .demo import seed
        seed(con)
        print(f"demo data written to {path}")
        if not a.no_serve:
            from .server import serve
            serve(con, port=a.port)
        return

    con = db.connect(a.db)
    if a.cmd == "serve":
        from .server import serve
        serve(con, a.host, a.port)
    elif a.cmd == "sync":
        from .sources import garmin, lastfm, spotify
        sources = ["garmin", "spotify", "lastfm"] if a.source == "all" else [a.source]
        failed = False
        for src in sources:
            try:
                if src == "garmin":
                    print(garmin.sync(con, a.days or 14))
                elif src == "spotify":
                    print(spotify.sync(con))
                else:
                    print(lastfm.sync(con, a.days or 7))
            except (garmin.GarminError, lastfm.LastfmError, spotify.SpotifyError) as e:
                print(f"{src}: {e}", file=sys.stderr)
                failed = True
        sys.exit(1 if failed else 0)
    elif a.cmd == "track":
        from .sources import mac
        try:
            mac.Tracker(con, interval=a.interval).run()
        except KeyboardInterrupt:
            print("\nstopped")
    elif a.cmd == "run":
        from .runner import run
        try:
            run(a.db or db.default_path(), a.host, a.port, track=not a.no_track)
        except KeyboardInterrupt:
            print("\nstopped")
    elif a.cmd == "connect":
        from .sources import garmin, lastfm
        if a.source == "garmin":
            print(garmin.connect_interactive())
        elif a.source == "lastfm":
            print(lastfm.connect_interactive(con))
        elif a.source == "claude":
            import getpass
            print("Ask loop sends a summary of your stats (not your logs or journal) to Claude.")
            print("Get a key at https://platform.claude.com/settings/keys (leave blank to use ANTHROPIC_API_KEY / `ant auth login`).")
            key = getpass.getpass("Anthropic API key (hidden): ").strip()
            from . import keychain
            keychain.save_secret(con, "anthropic_api_key", key)
            con.commit()
            print("Saved. Also run once:  pip3 install anthropic")
        elif a.source == "zotero":
            import getpass
            from .sources import zotero
            print("Create a read-only key at https://www.zotero.org/settings/keys (tick 'Allow library access').")
            r = zotero.connect(con, getpass.getpass("Zotero API key (hidden): "))
            print(f"Connected as {r['username']} · {r['items']} items synced.")
        elif a.source == "spotify":
            from .sources import spotify
            print(spotify.connect_interactive(con))
        else:
            key = input("USDA FoodData Central API key (free: https://api.data.gov/signup): ").strip()
            con.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('usda_api_key',?)", (key,))
            con.commit()
            print("USDA food search connected.")
    elif a.cmd == "install":
        from .sources import mac
        print(mac.install(str(Path(a.db or db.default_path()).resolve())))
    elif a.cmd == "uninstall":
        from .sources import mac
        print(mac.uninstall())
    elif a.cmd == "reset-password":
        import getpass
        from . import auth
        pw = getpass.getpass("New password (8+ characters): ")
        if pw != getpass.getpass("Again: "):
            sys.exit("Passwords didn't match.")
        print(f"Password reset for '{auth.reset_password(con, pw)}'. All devices are signed out.")
    elif a.cmd == "usda-build":
        from .sources import usda_local
        print(usda_local.build())
    elif a.cmd == "publish-brain":
        from . import publish
        if a.on or a.off:
            con.execute("INSERT OR REPLACE INTO settings(key, value) VALUES('publish_brain', ?)", ("on" if a.on else "off",))
            con.commit()
            print("monthly brain publishing:", "on" if a.on else "off")
        elif a.dry_run:
            print(json.dumps(publish.snapshot(con), indent=1, ensure_ascii=False))
        else:
            print(publish.publish(con, db.settings(con), push=False if a.no_push else None))
    elif a.cmd == "import-claude":
        from . import brain
        print(brain.import_claude(con, Path(a.file).expanduser().read_bytes()))
    elif a.cmd == "import-mynetdiary":
        from .sources.mynetdiary import import_paths
        for r in import_paths(con, a.paths):
            print(r)
    elif a.cmd == "import-health":
        from .sources.healthcsv import import_csv
        print(import_csv(con, a.file))
    elif a.cmd == "today":
        from .server import today_bundle
        b = today_bundle(con, date.today())
        b.pop("recent_days"), b.pop("events"), b["work"].pop("hours")
        print(json.dumps(b, indent=2))


if __name__ == "__main__":
    main()
