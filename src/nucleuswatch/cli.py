"""Command line entry point.

    nucleuswatch object ZTF21abfmbix [--plot lc.png]
    nucleuswatch nuclear --days 7 --mag-max 19 [--csv out.csv] [--zooniverse DIR]
    nucleuswatch digest --days 7 --data digest --site _site
    nucleuswatch notify --data digest [--webhook URL]
    nucleuswatch zooniverse-upload DIR --project 1234 --subject-set "Week 43"
"""

from __future__ import annotations

import argparse
import sys
import textwrap

import pandas as pd

from .brokers import ADAPTERS


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="nucleuswatch",
                                description="Compare what alert brokers say about ZTF transients.")
    sub = p.add_subparsers(dest="command", required=True)

    o = sub.add_parser("object", help="merged view of one object")
    o.add_argument("object_id", help="ZTF id (ZTF21abfmbix) or LSST diaObjectId (170587117485817955)")
    o.add_argument("--brokers", nargs="+", choices=list(ADAPTERS), help="brokers to query (default: all)")
    o.add_argument("--no-cache", action="store_true", help="always refetch from the brokers")
    o.add_argument("--plot", metavar="FILE", help="save a light-curve plot to FILE (png/pdf)")

    n = sub.add_parser("nuclear", help="recent nuclear transients + cross-broker consensus")
    n.add_argument("--survey", choices=["ztf", "lsst"], default="ztf", help="alert stream to search (default ztf)")
    n.add_argument("--days", type=float, default=7, help="look back this many days (default 7)")
    n.add_argument("--mag-max", type=float, default=19.0, help="faintest magnitude (default 19)")
    n.add_argument("--distnr-max", type=float, default=0.5, help="arcsec to ZTF ref source (default 0.5)")
    n.add_argument("--no-merge", action="store_true", help="skip per-object broker lookups (faster)")
    n.add_argument("--csv", metavar="FILE", help="write the candidate table to FILE")
    n.add_argument("--zooniverse", metavar="DIR", help="export Zooniverse subjects to DIR")
    n.add_argument("--no-cache", action="store_true")

    d = sub.add_parser("digest", help="weekly digest: record in --data, static site in --site")
    d.add_argument("--days", type=float, default=7)
    d.add_argument("--mag-max", type=float, default=20.0, help="faintest magnitude (default 20)")
    d.add_argument("--until", metavar="YYYY-MM-DD", help="end of the window (default: now), for backfilling")
    d.add_argument("--brokers", nargs="+", choices=list(ADAPTERS), help="brokers for consensus (default: all)")
    d.add_argument("--data", default="digest", help="folder for the committed JSON/CSV records")
    d.add_argument("--site", default="_site", help="folder for the generated web page")
    d.add_argument("--volunteers", default="zooniverse/classifications.csv",
                   help="Zooniverse classification export to add as a vote (used if the file exists)")
    d.add_argument("--no-cache", action="store_true")

    t = sub.add_parser("notify", help="post the latest digest's TDE-like candidates to a Slack/Discord webhook")
    t.add_argument("--data", default="digest", help="folder with latest.json")
    t.add_argument("--webhook", help="incoming-webhook URL (default: $NUCLEUSWATCH_WEBHOOK; none = print only)")
    t.add_argument("--always", action="store_true", help="post even when nothing is TDE-like")

    z = sub.add_parser("zooniverse-ingest", help="tally a Zooniverse classification export by object")
    z.add_argument("export", help="classification export CSV from the project's Data Exports page")
    z.add_argument("--task", default="T0", help="workflow task holding the classification (default T0)")
    z.add_argument("--workflow", type=int, help="only this workflow id")
    z.add_argument("--csv", metavar="FILE", help="write the tally to FILE")

    u = sub.add_parser("zooniverse-upload", help="upload an exported folder to a Zooniverse project")
    u.add_argument("folder", help="folder written by --zooniverse (contains manifest.csv)")
    u.add_argument("--project", type=int, required=True, help="Zooniverse project id")
    u.add_argument("--subject-set", required=True, help="name for the new subject set")

    args = p.parse_args(argv)
    pd.set_option("display.width", 160)
    pd.set_option("display.max_colwidth", 60)
    commands = {"object": _object, "nuclear": _nuclear, "digest": _digest, "notify": _notify,
                "zooniverse-ingest": _ingest, "zooniverse-upload": _upload}
    return commands[args.command](args, p)


def _object(args, p) -> int:
    from .core import get_object

    try:
        obj = get_object(args.object_id, args.brokers, use_cache=not args.no_cache)
    except ValueError as exc:
        p.error(str(exc))

    print(f"\n{obj.object_id}\n")
    if obj.results:
        summary = obj.summary().drop(columns=["url"])
        # Long free-text fields (Sherlock's description) are shown below the table.
        notes = summary.pop("sherlock_description") if "sherlock_description" in summary else None
        digits = {"ra": 5, "dec": 5, "offset_arcsec": 2, "first_mjd": 2, "last_mjd": 2}
        print(summary.round(digits).to_string(index=False, na_rep="-"))
        if notes is not None and notes.notna().any():
            text = textwrap.shorten(notes.dropna().iloc[0], 240)
            print("\n" + textwrap.fill(text, 96, initial_indent="Lasair Sherlock: ", subsequent_indent="  "))
        print("\nClassifications:")
        cls = obj.classifications
        cls["votes"] = cls["votes"].map({True: "*", False: ""})
        cls["probability"] = pd.to_numeric(cls["probability"], errors="coerce")
        print(cls[["broker", "classifier", "label", "probability", "category", "votes"]].to_string(
            index=False, float_format=lambda x: f"{x:.3f}", na_rep="-"))
        print("(* = counts toward consensus)")
        c = obj.consensus()
        print(f"\nConsensus: {c['status'].upper()}  {c['categories']}")
    _print_errors(obj.errors.items())

    if args.plot and obj.results:
        import matplotlib
        matplotlib.use("Agg")
        ax = obj.plot()
        ax.figure.savefig(args.plot, dpi=150, bbox_inches="tight")
        print(f"\nSaved plot to {args.plot}")
    return 0 if obj.results else 1


def _nuclear(args, p) -> int:
    from .recipes import NuclearCuts, lsst_nuclear_transients, nuclear_transients

    if args.survey == "lsst":
        return _nuclear_lsst(args, lsst_nuclear_transients)
    cuts = NuclearCuts(mag_max=args.mag_max, distnr_max=args.distnr_max)
    search = nuclear_transients(args.days, cuts, merge=not args.no_merge, use_cache=not args.no_cache)
    _print_errors(("search", e) for e in search.errors)

    print(f"\n{len(search)} nuclear transient candidate(s), last {args.days:g} days, mag <= {args.mag_max:g}\n")
    if len(search):
        cols = ["object_id", "mag", "band", "distnr", "sgscore1", "fink_class"]
        if "consensus" in search.candidates:
            cols += ["consensus", "categories"]
        print(search.candidates[cols].to_string(index=False, float_format=lambda x: f"{x:.2f}"))
    for obj in search:
        _print_errors((f"{obj.object_id} {b}", e) for b, e in obj.errors.items()
                      if "no Lasair token" not in e)

    if args.csv:
        search.candidates.to_csv(args.csv, index=False)
        print(f"\nWrote {args.csv}")
    if args.zooniverse:
        if args.no_merge:
            p.error("--zooniverse needs the per-object lookups; drop --no-merge")
        from .zooniverse import export_subjects
        path = export_subjects(search, args.zooniverse, use_cache=not args.no_cache)
        print(f"\nWrote Zooniverse subjects to {path}")
        print(f"Upload: nucleuswatch zooniverse-upload {args.zooniverse} --project ID --subject-set NAME")
    return 0


def _nuclear_lsst(args, recipe) -> int:
    from datetime import datetime, timedelta, timezone

    search = recipe(args.days, args.mag_max, max_offset=args.distnr_max, merge=not args.no_merge,
                    use_cache=not args.no_cache)
    _print_errors(("search", e) for e in search.errors)
    last = search.query.get("latest_alert_mjd")
    if last and not search.funnel.get("alerts_scanned"):
        when = datetime(1858, 11, 17, tzinfo=timezone.utc) + timedelta(days=last)
        print(f"\nNo Rubin alerts in the last {args.days:g} days; the latest is from {when:%Y-%m-%d}.")
    print(f"\n{len(search)} Rubin nuclear candidate(s), last {args.days:g} days, mag <= {args.mag_max:g}\n")
    if len(search):
        cols = ["object_id", "mag", "band", "host_sep", "host_type"]
        if "consensus" in search.candidates:
            cols += ["consensus", "categories"]
        print(search.candidates[cols].to_string(index=False, float_format=lambda x: f"{x:.2f}"))
    if args.csv:
        search.candidates.to_csv(args.csv, index=False)
        print(f"\nWrote {args.csv}")
    return 0


def _digest(args, p) -> int:
    from datetime import datetime, timezone

    from . import digest

    now = (datetime.fromisoformat(args.until).replace(tzinfo=timezone.utc, hour=23, minute=59)
           if args.until else None)
    from pathlib import Path

    volunteers = args.volunteers if args.volunteers and Path(args.volunteers).exists() else None
    record, search = digest.build(args.days, args.mag_max, brokers=args.brokers, now=now,
                                  use_cache=not args.no_cache, volunteers=volunteers)
    digest.mark_returning(record, args.data)
    path = digest.write_data(record, args.data)
    page = digest.render_site(record, args.data, args.site, search, use_cache=not args.no_cache)
    print(f"{record['n_candidates']} candidates ({record['n_disagree']} disagree)")
    print(f"Wrote {path} and {page}")
    return 0


def _notify(args, p) -> int:
    import json
    import os
    from pathlib import Path

    from . import notify

    record = json.loads((Path(args.data) / "latest.json").read_text())
    text = notify.message(record)
    url = args.webhook or os.environ.get("NUCLEUSWATCH_WEBHOOK")
    if not url:
        print(text)
        return 0
    if not notify.has_news(record) and not args.always:
        print("No new TDE-like candidate this week; not posting (use --always to post anyway).")
        return 0
    notify.post(url, text)
    print("Posted to the webhook.")
    return 0


def _ingest(args, p) -> int:
    from .taxonomy import categorize
    from .zooniverse import MIN_MAJORITY, MIN_VOLUNTEERS, read_classifications

    tally = read_classifications(args.export, task=args.task, workflow_id=args.workflow)
    rows = [{"object_id": oid, "volunteers": t["n"], "top_answer": t["label"], "share": t["fraction"],
             "category": categorize(t["label"]),
             "votes": t["n"] >= MIN_VOLUNTEERS and t["fraction"] > MIN_MAJORITY,
             "answers": "; ".join(f"{k}: {v}" for k, v in sorted(t["votes"].items(), key=lambda kv: -kv[1]))}
            for oid, t in sorted(tally.items())]
    table = pd.DataFrame(rows)
    print(f"{len(table)} object(s) classified; {int(table['votes'].sum()) if len(table) else 0} with a decided "
          f"volunteer vote (>= {MIN_VOLUNTEERS} volunteers, majority > {MIN_MAJORITY:.0%})\n")
    if len(table):
        print(table.to_string(index=False, float_format=lambda x: f"{x:.2f}"))
    if args.csv:
        table.to_csv(args.csv, index=False)
        print(f"\nWrote {args.csv}")
    return 0


def _upload(args, p) -> int:
    from pathlib import Path

    from .zooniverse import MANIFEST, upload_subjects

    manifest = Path(args.folder) / MANIFEST
    if not manifest.exists():
        p.error(f"{manifest} not found")
    try:
        set_id = upload_subjects(manifest, args.project, args.subject_set)
    except (ImportError, ValueError) as exc:
        p.error(str(exc))
    print(f"Created subject set {set_id} in project {args.project}")
    return 0


def _print_errors(items) -> None:
    for name, err in items:
        print(f"[skipped {name}] {err}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
