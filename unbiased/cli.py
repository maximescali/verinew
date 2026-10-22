"""python -m unbiased.cli run "<title>" [--date YYYY-MM-DD] [--place ...] [--desc ...]   run an event in the foreground
   python -m unbiased.cli show <event_id>                                               print the tree as JSON"""
import argparse
import json
import sys

from . import db, pipeline


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("title")
    r.add_argument("--date", default="")
    r.add_argument("--place", default="")
    r.add_argument("--desc", default="")
    s = sub.add_parser("show")
    s.add_argument("event_id")
    a = ap.parse_args()
    db.init()
    if a.cmd == "run":
        eid = db.add_event(a.title, a.desc, a.date, a.place)
        print("event", eid, file=sys.stderr)
        pipeline.run_event(eid)
        print(json.dumps(pipeline.tree(eid), indent=1, ensure_ascii=False, default=str))
    else:
        print(json.dumps(pipeline.tree(a.event_id), indent=1, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
