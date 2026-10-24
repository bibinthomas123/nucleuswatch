import json
import xml.dom.minidom

from nucleuswatch import cli, notify

RECORD = {
    "generated": "2026-10-24T06:00:00+00:00",
    "n_candidates": 3,
    "candidates": [
        {"object_id": "ZTF26aaaaaaa", "mag": 18.2, "band": "g", "ra": 10.0, "dec": -5.0, "tde_score": 0.71,
         "consensus": "disagree", "votes": []},
        {"object_id": "ZTF26aaaaaab", "mag": 19.1, "band": "r", "ra": None, "dec": None, "tde_score": 0.02,
         "consensus": "agree", "votes": [{"broker": "alerce", "category": "TDE"}]},
        {"object_id": "ZTF26aaaaaac", "mag": 19.5, "band": "r", "ra": 1.0, "dec": 1.0, "tde_score": 0.03,
         "consensus": "agree", "votes": [{"broker": "fink", "category": "SN"}]},
    ],
}


def test_notable_uses_score_and_tde_votes():
    assert [c["object_id"] for c in notify.notable(RECORD)] == ["ZTF26aaaaaaa", "ZTF26aaaaaab"]


def test_feed_is_valid_atom_with_one_entry_per_notable_candidate():
    doc = xml.dom.minidom.parseString(notify.atom_feed([RECORD]))
    assert len(doc.getElementsByTagName("entry")) == 2


def test_post_picks_payload_by_host(monkeypatch):
    sent = []

    class Ok:
        def raise_for_status(self):
            pass

    monkeypatch.setattr(notify.requests, "post", lambda url, json, timeout: sent.append(json) or Ok())
    notify.post("https://hooks.slack.com/services/x", "hi")
    notify.post("https://discord.com/api/webhooks/x", "hi")
    assert sent == [{"text": "hi"}, {"content": "hi"}]


def test_cli_prints_without_webhook(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("NUCLEUSWATCH_WEBHOOK", raising=False)
    (tmp_path / "latest.json").write_text(json.dumps(RECORD))
    assert cli.main(["notify", "--data", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "2 TDE-like" in out and "ZTF26aaaaaab" in out and "ZTF26aaaaaac" not in out
