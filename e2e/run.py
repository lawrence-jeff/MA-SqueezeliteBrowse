#!/usr/bin/env python3
"""
Run the end-to-end tests against a real player.

    python3 e2e/run.py --dry-run          # discover media and show the plan, change nothing
    python3 e2e/run.py                    # run every case (asks for confirmation first)
    python3 e2e/run.py --only E2E-04,E2E-07
    python3 e2e/run.py --list

The tests clear the queue on the configured player and play real media. The player's volume is
set to 0 for the run (and restored afterwards) unless --audible is given.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import cases  # noqa: E402
from harness import (  # noqa: E402
    FAIL,
    Check,
    PASS,
    SKIP,
    WARN,
    CaseResult,
    ClientLog,
    Ctx,
    Rpc,
    ServerLog,
)

SYMBOL = {PASS: "PASS", FAIL: "FAIL", WARN: "WARN", SKIP: "SKIP"}


def load_config(path: Path) -> dict:
    if not path.exists():
        sys.exit(f"No config at {path}. Copy config.example.json to config.json and fill it in.")
    return json.loads(path.read_text())


def build_ctx(config: dict, args: argparse.Namespace) -> Ctx:
    rpc = Rpc(config["server_url"], config["player_id"])
    client_log = None
    if config.get("client_log_url") and not args.no_client_log:
        client_log = ClientLog(config["client_log_url"], config["player_id"])
    server_log = None
    if config.get("server_ssh") and not args.no_server_log:
        key = str(Path(config.get("server_ssh_key", "~/.ssh/id_ed25519")).expanduser())
        server_log = ServerLog(config["server_ssh"], key, config["server_container"])
    media = cases.discover(rpc, config.get("prefer", {}))
    return Ctx(rpc, client_log, server_log, media)


def check_player(rpc: Rpc, config: dict, force: bool) -> None:
    players = {p.get("playerid"): p for p in rpc.players()}
    player = players.get(config["player_id"])
    if player is None:
        sys.exit(f"Player {config['player_id']} is not connected to the server.")
    if config.get("player_name") and player.get("name") != config["player_name"]:
        sys.exit(f"Player name is {player.get('name')!r}, expected {config['player_name']!r}. Refusing to run.")
    status = rpc.status()
    busy = status.get("mode") == "play" or int(status.get("playlist_tracks", 0)) > 0
    if busy and not force:
        sys.exit(
            f"{player.get('name')} is playing or has a queue "
            f"({status.get('playlist_tracks')} item(s), mode {status.get('mode')}). "
            "Stop it and clear the queue first, or pass --force to let the tests clear it."
        )


def select(only: str | None) -> list[cases.CaseDef]:
    if not only:
        return list(cases.CASES)
    wanted = {part.strip().upper() for part in only.split(",") if part.strip()}
    return [c for c in cases.CASES if c.case_id.upper() in wanted]


def print_plan(ctx: Ctx, chosen: list[cases.CaseDef]) -> None:
    print("Media found in the library:")
    for key, item in ctx.media.items():
        print(f"  {key:16} {item['name'][:60]:60} {item['params']}")
    print("\nCases:")
    for c in chosen:
        missing = [n for n in c.needs if n not in ctx.media]
        note = f"  (skipped, no {', '.join(missing)})" if missing else ""
        print(f"  {c.case_id}  {c.title}{note}")
    print(
        "\nChecks: "
        f"client log {'on' if ctx.client_log else 'off'}, "
        f"server log {'on' if ctx.server_log else 'off'}"
    )


def run_case(ctx: Ctx, definition: cases.CaseDef) -> CaseResult:
    result = CaseResult(definition.case_id, definition.title, definition.covers)
    ctx.result = result
    started = time.time()
    missing = [n for n in definition.needs if n not in ctx.media]
    if missing:
        ctx.skip("media", f"library has no {', '.join(missing)}")
    else:
        try:
            ctx.reset()
            definition.fn(ctx)
        except Exception as err:  # noqa: BLE001 - a crashing case must not stop the run
            result.error = f"{type(err).__name__}: {err}"
            result.checks.append(Check("case crashed", FAIL, traceback.format_exc(limit=3)[-400:]))
        finally:
            ctx.reset()
    result.seconds = time.time() - started
    return result


def print_result(result: CaseResult, verbose: bool) -> None:
    print(f"[{SYMBOL[result.status]}] {result.case_id} {result.title} ({result.seconds:.0f}s)")
    for check in result.checks:
        if verbose or check.status in (FAIL, WARN):
            detail = f" - {check.detail}" if check.detail else ""
            print(f"      {SYMBOL[check.status]} {check.name}{detail}")


def write_reports(results: list[CaseResult], config: dict) -> Path:
    out_dir = HERE / "reports"
    out_dir.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    data = [
        {
            "id": r.case_id,
            "title": r.title,
            "covers": r.covers,
            "status": r.status,
            "seconds": round(r.seconds, 1),
            "error": r.error,
            "checks": [{"name": c.name, "status": c.status, "detail": c.detail} for c in r.checks],
        }
        for r in results
    ]
    (out_dir / f"{stamp}.json").write_text(json.dumps(data, indent=2))
    counts = {s: sum(1 for r in results if r.status == s) for s in (PASS, WARN, FAIL, SKIP)}
    lines = [
        f"# End-to-end run {stamp}",
        "",
        f"Player: {config.get('player_name')} ({config['player_id']})  ",
        f"Totals: {counts[PASS]} pass, {counts[WARN]} warn, {counts[FAIL]} fail, {counts[SKIP]} skipped",
        "",
        "| Case | Result | Title | Covers | Secs |",
        "|---|---|---|---|---|",
    ]
    for r in results:
        lines.append(f"| {r.case_id} | {r.status} | {r.title} | {r.covers} | {r.seconds:.0f} |")
    for r in results:
        problems = [c for c in r.checks if c.status in (FAIL, WARN)]
        if problems or r.error:
            lines += ["", f"## {r.case_id} {r.title}"]
            if r.error:
                lines.append(f"- crashed: `{r.error}`")
            lines += [f"- {c.status}: {c.name} - {c.detail}" for c in problems]
    path = out_dir / f"{stamp}.md"
    path.write_text("\n".join(lines) + "\n")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default=str(HERE / "config.json"))
    parser.add_argument("--only", help="comma-separated case ids, e.g. E2E-01,E2E-04")
    parser.add_argument("--list", action="store_true", help="list the cases and exit")
    parser.add_argument("--dry-run", action="store_true", help="discover media and show the plan only")
    parser.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    parser.add_argument("--audible", action="store_true", help="do not set the volume to 0 during the run")
    parser.add_argument("--force", action="store_true", help="run even if the player is playing or has a queue")
    parser.add_argument("--no-client-log", action="store_true")
    parser.add_argument("--no-server-log", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true", help="show passing checks too")
    args = parser.parse_args()

    if args.list:
        for c in cases.CASES:
            print(f"{c.case_id}  {c.title}   [{c.covers}]")
        return 0

    config = load_config(Path(args.config))
    ctx = build_ctx(config, args)
    check_player(ctx.rpc, config, args.force)
    chosen = select(args.only)
    print_plan(ctx, chosen)
    if args.dry_run:
        return 0

    if not args.yes:
        answer = input(
            f"\nThis clears the queue on {config.get('player_name')} and plays real media"
            f"{'' if args.audible else ' (volume set to 0)'}. Continue? [y/N] "
        )
        if answer.strip().lower() != "y":
            print("Aborted.")
            return 2

    original_volume = ctx.rpc.mixer("volume")
    if not args.audible:
        # Volume 0 is the reliable way to silence a Squeezelite player. The protocol's mute
        # ("aude") only switches the output off, and the next stream switches it back on.
        ctx.rpc.mixer("volume", 0)
        if ctx.rpc.mixer("volume") != 0:
            ctx.rpc.mixer("volume", original_volume)
            sys.exit("Could not set the volume to 0, so the run would be audible. Aborting.")
    results: list[CaseResult] = []
    try:
        print()
        for definition in chosen:
            result = run_case(ctx, definition)
            print_result(result, args.verbose)
            results.append(result)
    finally:
        ctx.rpc.mixer("volume", int(original_volume or 0))

    report = write_reports(results, config)
    counts = {s: sum(1 for r in results if r.status == s) for s in (PASS, WARN, FAIL, SKIP)}
    print(
        f"\n{counts[PASS]} passed, {counts[WARN]} warnings, {counts[FAIL]} failed, "
        f"{counts[SKIP]} skipped. Report: {report}"
    )
    return 1 if counts[FAIL] else 0


if __name__ == "__main__":
    sys.exit(main())
