"""Alerts after every run (task 1.7).

    python -m nowcast.ingest.alerts --store <folder> [--run-id ID] [--no-issues] [--crashed]

* Telegram: one message per run (counts, new points, every warning and failure, link to the run).
  Needs the repository secrets TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID; without them the message
  is printed to the log and nothing is sent.
* GitHub issues: one open issue per failing source (label ``ingest-failure``). A later failure
  of the same source adds a comment instead of a new issue; when the source works again the
  issue gets a comment and is closed. Needs GITHUB_TOKEN and GITHUB_REPOSITORY (set in Actions).

``--crashed`` is used when the run itself did not finish (a bug, not a source failure): it sends
a short message and opens a "run crashed" issue.
"""

from __future__ import annotations

import argparse
import html
import os
import sys

import requests

from nowcast.ingest import http
from nowcast.store.vintages import Store

LABEL = "ingest-failure"
CRASH_TITLE = "Monthly data run crashed"
TG_LIMIT = 4000


def run_url() -> str:
    server, repo, rid = (
        os.environ.get(k, "") for k in ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID")
    )
    return f"{server}/{repo}/actions/runs/{rid}" if server and repo and rid else ""


def telegram_text(report: dict) -> str:
    c = report["counts"]
    icon = "❌" if c["fail"] else ("⚠️" if c["warn"] else "✅")
    run = html.escape(report["run_id"])
    lines = [
        f"{icon} <b>Nowcast data run {run}</b> ({report['mode']}, {report['trigger']})",
        f"ok {c['ok']} · warn {c['warn']} · fail {c['fail']}",
        f"new points {report['new_points']} · revised {report['revised_points']} "
        f"· removed {report['removed_points']}",
    ]
    changed = [
        f"{sid}: {r['changes']}"
        for sid, r in report["series"].items()
        if r["status"] != "fail" and r.get("changes") not in (None, "no change")
    ]
    if changed:
        lines += ["", "<b>Updated</b>"] + [html.escape(x) for x in changed]
    for status in ("fail", "warn"):
        rows = []
        for sid, r in report["series"].items():
            if r["status"] == status:
                msgs = [m.split(": ", 1)[1] for m in r["messages"] if m.startswith(status)]
                rows.append(f"{sid}: " + "; ".join(msgs)[:300])
        if rows:
            lines += ["", f"<b>{'Failed' if status == 'fail' else 'Warnings'}</b>"] + [
                html.escape(x) for x in rows
            ]
    if run_url():
        lines += ["", run_url()]
    text = "\n".join(lines)
    return text if len(text) <= TG_LIMIT else text[: TG_LIMIT - 20] + "\n… (cut, see the run)"


def send_telegram(text: str, session: requests.Session | None = None) -> bool:
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        print("Telegram secrets not set; message not sent:\n" + text)
        return False
    s = session or http.session()
    r = s.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        timeout=30,
        json={"chat_id": chat, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True},
    )
    if not r.ok:
        print(f"Telegram error {r.status_code}: {r.text[:300]}")
    return r.ok


class Issues:
    def __init__(self, session: requests.Session | None = None):
        self.repo = os.environ["GITHUB_REPOSITORY"]
        self.s = session or http.session()
        self.s.headers.update(
            {"Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}", "Accept": "application/vnd.github+json"}
        )
        self.api = f"https://api.github.com/repos/{self.repo}"

    def open_issues(self) -> dict[str, int]:
        r = self.s.get(
            f"{self.api}/issues", params={"labels": LABEL, "state": "open", "per_page": 100}, timeout=30
        )
        r.raise_for_status()
        return {i["title"]: i["number"] for i in r.json()}

    def create(self, title: str, body: str) -> None:
        self.s.post(
            f"{self.api}/issues", json={"title": title, "body": body, "labels": [LABEL]}, timeout=30
        ).raise_for_status()

    def comment(self, number: int, body: str) -> None:
        self.s.post(
            f"{self.api}/issues/{number}/comments", json={"body": body}, timeout=30
        ).raise_for_status()

    def close(self, number: int) -> None:
        self.s.patch(f"{self.api}/issues/{number}", json={"state": "closed"}, timeout=30).raise_for_status()


def issue_title(file_key: str) -> str:
    return f"Data source failing: {file_key}"


def issue_body(report: dict, file_key: str, src: dict) -> str:
    lines = [
        f"Run **{report['run_id']}** ({report['mode']}, {report['trigger']}) could not use `{file_key}`.",
        "",
        f"- source adapter: `{src['source']}`",
        f"- url: {src['url']}",
        f"- series affected: {', '.join(f'`{x}`' for x in src['failed'])}",
    ]
    if src["errors"]:
        lines += ["", "Errors:", "```", *src["errors"], "```"]
    for sid in src["failed"]:
        other = [m for m in report["series"][sid]["messages"] if not m.startswith("fail: left out")]
        if other and not report["series"][sid].get("error"):
            lines += [f"`{sid}`: " + "; ".join(other)]
    lines += [
        "",
        "The store keeps the previous values for these series. See `docs/runbook_ingest.md` "
        "(manual inbox) if the source stays broken.",
    ]
    if run_url():
        lines += ["", f"Run log: {run_url()}"]
    return "\n".join(lines)


def sync_issues(report: dict, issues: Issues) -> None:
    open_ = issues.open_issues()
    for file_key, src in report["sources"].items():
        title = issue_title(file_key)
        if src["failed"]:
            body = issue_body(report, file_key, src)
            if title in open_:
                issues.comment(open_[title], body)
            else:
                issues.create(title, body)
        elif title in open_ and not src["failed"]:
            issues.comment(open_[title], f"Worked again in run **{report['run_id']}**. Closing.")
            issues.close(open_[title])
    if CRASH_TITLE in open_:
        issues.comment(open_[CRASH_TITLE], f"Run **{report['run_id']}** finished. Closing.")
        issues.close(open_[CRASH_TITLE])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", required=True)
    ap.add_argument("--run-id", default="")
    ap.add_argument("--no-issues", action="store_true")
    ap.add_argument("--crashed", action="store_true")
    args = ap.parse_args(argv)
    want_issues = (
        not args.no_issues and os.environ.get("GITHUB_TOKEN") and os.environ.get("GITHUB_REPOSITORY")
    )

    if args.crashed:
        send_telegram(f"❌ <b>Nowcast data run crashed</b> before finishing.\n{run_url()}")
        if want_issues:
            iss = Issues()
            title = CRASH_TITLE
            body = f"The ingestion run did not finish (a code error, not a source failure).\n\n{run_url()}"
            open_ = iss.open_issues()
            if title in open_:
                iss.comment(open_[title], body)
            else:
                iss.create(title, body)
        return 0

    store = Store(args.store)
    if not args.run_id:
        print("no run id: nothing to report (catch-up had nothing to retry)")
        return 0
    report = store.report(args.run_id)
    send_telegram(telegram_text(report))
    if want_issues:
        sync_issues(report, Issues())
    return 0


if __name__ == "__main__":
    sys.exit(main())
