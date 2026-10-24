"""Turn merged broker objects into Zooniverse subjects.

``export_subjects`` writes one folder that the Zooniverse uploaders accept
directly: PNG images plus a ``manifest.csv`` with one row per subject. Upload
it with the Project Builder, with panoptes-cli::

    panoptes subject-set upload-subjects <subject_set_id> manifest.csv

or with :func:`upload_subjects` (needs ``pip install nucleuswatch[zooniverse]``).

Metadata visibility follows Zooniverse's column-prefix convention:

- ``#`` never shown to volunteers (kept in the classification export).
  Broker classifications go here so they don't bias volunteers.
- ``!`` hidden while classifying, shown on Talk afterwards. The object id,
  position and broker links go here so volunteers can discuss and look up
  objects once they have classified them.
"""

from __future__ import annotations

import csv
import os
from collections.abc import Iterable
from pathlib import Path

from . import __version__
from ._http import BrokerError
from .brokers import fink
from .core import MergedObject

MANIFEST = "manifest.csv"


def subject_metadata(obj: MergedObject) -> dict:
    """Manifest metadata for one object (without image columns)."""
    summary = obj.summary()
    lc = obj.lightcurve
    c = obj.consensus()
    first = summary.iloc[0] if len(summary) else {}

    meta = {
        "!object_id": obj.object_id,
        "!ra": _fmt(first.get("ra"), 6),
        "!dec": _fmt(first.get("dec"), 6),
    }
    for name, r in obj.results.items():
        meta[f"!{name}_url"] = r.url or ""

    meta.update({
        "#consensus": c["status"],
        "#categories": ";".join(c["categories"]),
        "#n_detections": int(lc["candid"].nunique()) if len(lc) else 0,
        "#peak_mag": _fmt(lc["mag"].min() if len(lc) else None, 3),
        "#first_mjd": _fmt(lc["mjd"].min() if len(lc) else None, 5),
        "#last_mjd": _fmt(lc["mjd"].max() if len(lc) else None, 5),
        "#brokers": ";".join(obj.brokers),
        "#nucleuswatch_version": __version__,
    })
    for v in c["votes"]:
        meta[f"#{v['broker']}_{v['classifier']}"] = v["label"]
    return meta


def export_subjects(
    objects: Iterable[MergedObject],
    outdir: str | os.PathLike,
    *,
    cutouts: bool = True,
    use_cache: bool = True,
) -> Path:
    """Write images + ``manifest.csv`` for a Zooniverse subject set.

    Each subject gets ``image1`` = a blind light curve (no ids or broker
    labels) and, if ``cutouts``, ``image2`` = science/template/difference
    stamps of the latest alert from Fink. Returns the manifest path.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for obj in objects:
        if obj.lightcurve.empty:
            continue
        row = {}

        ax = obj.plot(annotate=False)
        lc_name = f"{obj.object_id}_lightcurve.png"
        ax.figure.savefig(out / lc_name, dpi=110, bbox_inches="tight")
        plt.close(ax.figure)
        row["image1"] = lc_name

        if cutouts and obj.survey == "ztf":  # LSST stamps: not wired up yet
            try:
                stamps = {k: fink.cutout(obj.object_id, k, use_cache=use_cache) for k in fink.CUTOUT_KINDS}
            except BrokerError:
                stamps = None
            if stamps:
                st_name = f"{obj.object_id}_stamps.png"
                _save_stamps(stamps, out / st_name)
                row["image2"] = st_name

        row.update(subject_metadata(obj))
        rows.append(row)

    path = out / MANIFEST
    _write_manifest(rows, path)
    return path


def _save_stamps(stamps: dict[str, list], path: Path) -> None:
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, len(stamps), figsize=(3 * len(stamps), 3.3))
    draw_stamps(axes, stamps)
    fig.tight_layout()
    fig.savefig(path, dpi=100)
    plt.close(fig)


STAMP_TITLES = {"Science": "New", "Template": "Before", "Difference": "Difference"}


def draw_stamps(axes, stamps: dict[str, list]) -> None:
    """Draw cutouts (from :func:`fink.cutout`) onto ``axes``, one per kind."""
    import numpy as np

    for ax, (kind, pixels) in zip(axes, stamps.items()):
        img = np.asarray(pixels, dtype=float)
        # Percentile stretch per stamp: shows faint host structure without
        # letting the nucleus saturate. Difference images are centred on 0.
        lo, hi = np.nanpercentile(img, [1, 99.5])
        ax.imshow(img, cmap="gray", vmin=lo, vmax=hi, origin="lower", interpolation="nearest")
        ax.set_title(STAMP_TITLES[kind])
        ax.set_axis_off()


def _write_manifest(rows: list[dict], path: Path) -> None:
    # Image columns first, then the union of metadata keys in first-seen order.
    columns: list[str] = []
    for key in ("image1", "image2"):
        if any(key in r for r in rows):
            columns.append(key)
    for r in rows:
        for k in r:
            if k not in columns:
                columns.append(k)
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, restval="")
        writer.writeheader()
        writer.writerows(rows)


def read_manifest(path: str | os.PathLike) -> list[dict]:
    with Path(path).open(newline="") as fh:
        return list(csv.DictReader(fh))


def upload_subjects(
    manifest: str | os.PathLike,
    project_id: int,
    subject_set_name: str,
    *,
    username: str | None = None,
    password: str | None = None,
) -> int:
    """Create a subject set in a Zooniverse project and upload the manifest.

    Credentials default to the ``ZOONIVERSE_USERNAME`` and
    ``ZOONIVERSE_PASSWORD`` environment variables. Returns the new subject
    set id. You must be an owner or collaborator on the project.
    """
    try:
        from panoptes_client import Panoptes, Project, Subject, SubjectSet
    except ImportError as exc:
        raise ImportError("upload needs panoptes-client: pip install 'nucleuswatch[zooniverse]'") from exc

    username = username or os.environ.get("ZOONIVERSE_USERNAME")
    password = password or os.environ.get("ZOONIVERSE_PASSWORD")
    if not (username and password):
        raise ValueError("set ZOONIVERSE_USERNAME and ZOONIVERSE_PASSWORD (or pass them)")

    manifest = Path(manifest)
    rows = read_manifest(manifest)
    Panoptes.connect(username=username, password=password)
    project = Project.find(project_id)

    subject_set = SubjectSet()
    subject_set.links.project = project
    subject_set.display_name = subject_set_name
    subject_set.save()

    subjects = []
    with Subject.async_saves():
        for row in rows:
            subject = Subject()
            subject.links.project = project
            for key in ("image1", "image2"):
                if row.get(key):
                    subject.add_location(str(manifest.parent / row[key]))
            subject.metadata.update({k: v for k, v in row.items() if not k.startswith("image")})
            subject.save()
            subjects.append(subject)
    subject_set.add(subjects)
    return int(subject_set.id)


def _fmt(value, digits: int) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return ""


# ------------------------------------------------------------------ the way back

MIN_VOLUNTEERS = 5   # fewer answers than this: no volunteer vote
MIN_MAJORITY = 0.5   # the top answer needs more than this share


def read_classifications(export: str | os.PathLike, *, task: str = "T0",
                         workflow_id: int | None = None) -> dict[str, dict]:
    """Tally a Zooniverse classification export by object.

    ``export`` is the CSV from the project's *Data Exports → Request new
    classification export*. Answers to ``task`` are matched to objects
    through the ``!object_id`` subject metadata that :func:`export_subjects`
    writes. Each volunteer counts once per subject (their latest answer);
    anonymous classifications count once each.

    Returns ``{object_id: {"votes": {answer: n}, "n": volunteers,
    "label": top answer, "fraction": its share}}``.
    """
    import json
    from collections import Counter

    latest: dict[tuple[str, str], tuple[str, str]] = {}
    with open(export, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if workflow_id is not None and str(row.get("workflow_id")) != str(workflow_id):
                continue
            try:
                subjects = json.loads(row.get("subject_data") or "{}")
                annotations = json.loads(row.get("annotations") or "[]")
            except ValueError:
                continue
            answer = next((a.get("value") for a in annotations if a.get("task") == task), None)
            if isinstance(answer, list):  # multiple-choice questions: take the first choice
                answer = answer[0] if answer else None
            if not isinstance(answer, str) or not answer.strip():
                continue
            who = row.get("user_id") or f"anon:{row.get('classification_id')}"
            for meta in subjects.values():
                oid = (meta or {}).get("!object_id")
                if oid:
                    key = (str(oid), who)
                    stamp = row.get("created_at", "")
                    if key not in latest or stamp >= latest[key][0]:
                        latest[key] = (stamp, answer.strip())

    tally: dict[str, Counter] = {}
    for (oid, _), (_, answer) in latest.items():
        tally.setdefault(oid, Counter())[answer] += 1
    out = {}
    for oid, votes in tally.items():
        label, top = votes.most_common(1)[0]
        n = sum(votes.values())
        out[oid] = {"votes": dict(votes), "n": n, "label": label, "fraction": top / n}
    return out


def volunteer_result(object_id: str, tally: dict, *, min_volunteers: int = MIN_VOLUNTEERS,
                     min_majority: float = MIN_MAJORITY):
    """A BrokerResult for the volunteers, so they vote in the consensus like a broker.

    The volunteers vote only with at least ``min_volunteers`` answers and a
    top answer above ``min_majority``; otherwise their label is "Ambiguous",
    which abstains.
    """
    from .brokers.base import BrokerResult, classification_frame, lightcurve_frame

    decided = tally["n"] >= min_volunteers and tally["fraction"] > min_majority
    label = tally["label"] if decided else "Ambiguous"
    return BrokerResult(
        broker="zooniverse",
        object_id=object_id,
        lightcurve=lightcurve_frame([], "zooniverse"),
        classifications=classification_frame(
            [{"classifier": "volunteers", "label": label, "probability": tally["fraction"],
              "version": f"{tally['n']} volunteers"}], "zooniverse"),
        extra={"volunteer_votes": tally["votes"], "volunteers": tally["n"]},
    )


def add_volunteer_votes(objects: dict[str, MergedObject], export: str | os.PathLike, **kwargs) -> int:
    """Add the volunteers' vote to every object that has classifications in ``export``.

    ``kwargs`` go to :func:`read_classifications` (``task``, ``workflow_id``).
    Returns how many objects got a volunteer result.
    """
    tallies = read_classifications(export, **kwargs)
    n = 0
    for oid, obj in objects.items():
        if oid in tallies:
            obj.results["zooniverse"] = volunteer_result(oid, tallies[oid])
            n += 1
    return n
