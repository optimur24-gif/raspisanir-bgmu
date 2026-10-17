"""Run a short Telegram test using an ephemeral encrypted token handoff.

The runner's private RSA key stays in RUNNER_TEMP. Only its public key, an
encrypted token, and non-sensitive status reports are exchanged through Git.
"""

import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import subprocess
import time

from telegram.error import Conflict, InvalidToken
from telegram.ext import TypeHandler
from telegram import Update

from bgmu_bot.__main__ import RedactingFormatter
from bgmu_bot.bgmu import BGMUSource
from bgmu_bot.ui import create_application
from bgmu_bot.website import WebsiteClient
from bgmu_bot.shared_schedule import SharedSchedule
from bgmu_bot.runner import refresh_feed


BRANCH = "codex/bgmu-github-test"
SESSION = Path("test-session")


def command(args, *, data=None, allow_failure=False):
    result = subprocess.run(args, input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode and not allow_failure:
        raise RuntimeError("command_failed_" + Path(args[0]).name)
    return result


def git(*args, allow_failure=False):
    return command(["git", *args], allow_failure=allow_failure)


def sync_branch():
    git("fetch", "origin", "refs/heads/" + BRANCH)
    git("merge", "--ff-only", "FETCH_HEAD")


def publish(files, message):
    git("add", "--", *(str(path) for path in files))
    if git("diff", "--cached", "--quiet", allow_failure=True).returncode == 0:
        return
    git("-c", "user.name=github-actions[bot]", "-c",
        "user.email=41898282+github-actions[bot]@users.noreply.github.com",
        "commit", "-m", message)
    git("push", "origin", "HEAD:refs/heads/" + BRANCH)


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def generate_key(private_path):
    private_path.parent.mkdir(parents=True, exist_ok=True)
    command(["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:3072", "-out", str(private_path)])
    private_path.chmod(0o600)
    return command(["openssl", "pkey", "-in", str(private_path), "-pubout"]).stdout


def decrypt_token(ciphertext, private_path):
    return command([
        "openssl", "pkeyutl", "-decrypt", "-inkey", str(private_path),
        "-pkeyopt", "rsa_padding_mode:oaep", "-pkeyopt", "rsa_oaep_md:sha256",
        "-pkeyopt", "rsa_mgf1_md:sha256",
    ], data=ciphertext).stdout.decode().strip()


def matches_request(metadata, run_id, public_sha):
    return isinstance(metadata, dict) and metadata.get("run_id") == run_id and metadata.get("public_key_sha256") == public_sha


def await_token(run_id, public_sha, private_path, timeout=300):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        sync_branch()
        metadata_path = SESSION / "token.json"
        encrypted_path = SESSION / "token.enc"
        if metadata_path.exists() and encrypted_path.exists():
            metadata = json.loads(metadata_path.read_text())
            if matches_request(metadata, run_id, public_sha):
                return decrypt_token(encrypted_path.read_bytes(), private_path)
        time.sleep(5)
    raise TimeoutError("encrypted_token_not_received")


def publish_status(report):
    sync_branch()
    path = SESSION / "status.json"
    write_json(path, report)
    publish([path], "test: update temporary Telegram session status")


async def serve(credentials, run_id, seconds):
    token, restricted_token = credentials['public_token'], credentials['restricted_token']
    os.environ['RESTRICTED_BOT_TOKEN'] = restricted_token
    from restricted_bot import bot as restricted
    from aiogram.exceptions import TelegramConflictError, TelegramUnauthorizedError
    from aiogram.methods import GetUpdates
    handler = logging.StreamHandler()
    handler.setFormatter(RedactingFormatter([token, restricted_token, os.environ.get('BGMU_PROXY_URL', '')]))
    logging.basicConfig(level=logging.WARNING, handlers=[handler], force=True)
    report = {'run_id': run_id, 'state': 'initializing', 'received_updates': 0,
              'public_handler_errors': 0, 'restricted_updates': 0, 'interface': 'shared-p114',
              'requested_duration_seconds': seconds}
    stop, restricted_ready = asyncio.Event(), asyncio.Event()
    initialized = False
    restricted_task = feed_task = None
    async with WebsiteClient.from_environment() as website:
        source = BGMUSource(website, cache_ttl=300)
        feed = SharedSchedule(source)
        source.schedule_consumers = [feed]
        app = create_application(token, source)

        async def count_error(update, context):
            report['public_handler_errors'] += 1

        async def count_update(update, context):
            report['received_updates'] += 1

        async def restricted_requests(make_request, bot, method):
            try:
                result = await make_request(bot, method)
                if isinstance(method, GetUpdates):
                    report['restricted_updates'] += len(result)
                    restricted_ready.set()
                return result
            except (TelegramConflictError, TelegramUnauthorizedError) as error:
                report['state'] = 'restricted_polling_conflict' if isinstance(error, TelegramConflictError) else 'restricted_invalid_token'
                stop.set()
                raise

        restricted.bot.session.middleware(restricted_requests)
        app.add_error_handler(count_error)
        app.add_handler(TypeHandler(Update, count_update), group=-1)

        def polling_error(error):
            if isinstance(error, (Conflict, InvalidToken)):
                report['state'] = 'public_polling_conflict' if isinstance(error, Conflict) else 'public_invalid_token'
                stop.set()

        try:
            publish_status(report)
            await feed.refresh()
            report['linked_group'] = feed.group
            report['linked_semester_events'] = len(feed.rows)
            await app.initialize()
            initialized = True
            public_me = await app.bot.get_me()
            restricted_me = await restricted.bot.get_me()
            report.update(bot_url='https://t.me/' + public_me.username,
                          restricted_bot_url='https://t.me/' + restricted_me.username,
                          privacy_disabled=bool(public_me.can_read_all_group_messages))
            if restricted_me.id != restricted.EXPECTED_BOT_ID:
                raise ValueError('restricted_identity_mismatch')
            public_hook = await app.bot.get_webhook_info()
            restricted_hook = await restricted.bot.get_webhook_info()
            if public_hook.url or restricted_hook.url:
                report['state'] = 'webhook_present'
                return report
            await app.start()
            await app.updater.start_polling(bootstrap_retries=0, drop_pending_updates=False, error_callback=polling_error)
            restricted_task = asyncio.create_task(restricted.run_restricted(feed, handle_signals=False))
            feed_task = asyncio.create_task(refresh_feed(feed))
            ready_task = asyncio.create_task(restricted_ready.wait())
            stop_task = asyncio.create_task(stop.wait())
            try:
                await asyncio.wait([restricted_task, ready_task, stop_task], timeout=55, return_when=asyncio.FIRST_COMPLETED)
                if restricted_task.done():
                    restricted_task.result()
                    raise RuntimeError('restricted_polling_stopped')
                if stop.is_set():
                    return report
                if not restricted_ready.is_set():
                    raise TimeoutError('restricted_polling_not_ready')
            finally:
                ready_task.cancel()
                stop_task.cancel()
                await asyncio.gather(ready_task, stop_task, return_exceptions=True)
            if restricted.SCHEDULE_FEED is not feed:
                raise RuntimeError('shared_schedule_not_connected')
            started = datetime.now(timezone.utc)
            report.update(state='running', both_bots_polling=True, shared_source=True,
                          started_at=started.isoformat(), planned_end_at=(started + timedelta(seconds=seconds)).isoformat())
            publish_status(report)
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline and not stop.is_set():
                try:
                    await asyncio.wait_for(stop.wait(), timeout=min(30, max(0.1, deadline-time.monotonic())))
                except asyncio.TimeoutError:
                    pass
                if restricted_task.done():
                    restricted_task.result()
                    raise RuntimeError('restricted_polling_stopped')
                report['heartbeat_at'] = datetime.now(timezone.utc).isoformat()
                publish_status(report)
            if report['state'] == 'running':
                report['state'] = 'finished'
        except Exception as error:
            report['state'] = 'failed'
            report['error_type'] = type(error).__name__
        finally:
            for task in (restricted_task, feed_task):
                if task is not None:
                    task.cancel()
            await asyncio.gather(*(task for task in (restricted_task, feed_task) if task is not None), return_exceptions=True)
            await restricted.bot.session.close()
            if app.updater.running:
                await app.updater.stop()
            if app.running:
                await app.stop()
            if initialized:
                await app.shutdown()
            report['finished_at'] = datetime.now(timezone.utc).isoformat()
            publish_status(report)
    return report


def main():
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    if not run_id.isdigit() or os.environ.get("GITHUB_REF") != "refs/heads/" + BRANCH:
        print("This tool only runs in its dedicated GitHub Actions branch.")
        return 1
    SESSION.mkdir(exist_ok=True)
    private_path = Path(os.environ["RUNNER_TEMP"]) / ("bgmu-test-" + run_id + ".pem")
    try:
        # A previous session may commit its final status after this checkout's SHA.
        # Start the key exchange from that latest branch tip before writing files.
        sync_branch()
        public = generate_key(private_path)
        public_sha = hashlib.sha256(public).hexdigest()
        (SESSION / "public.pem").write_bytes(public)
        write_json(SESSION / "request.json", {
            "run_id": run_id, "state": "awaiting_encrypted_token", "public_key_sha256": public_sha,
            "source_commit": os.environ.get("GITHUB_SHA"),
        })
        publish([SESSION / "public.pem", SESSION / "request.json"], "test: publish ephemeral token encryption key")
        credentials = json.loads(await_token(run_id, public_sha, private_path))
        private_path.unlink(missing_ok=True)
        report = asyncio.run(serve(credentials, run_id, seconds=2400))
        return 0 if report["state"] == "finished" else 1
    except Exception as error:
        report = {"run_id": run_id, "state": "setup_failed", "error_type": type(error).__name__}
        try:
            publish_status(report)
        except Exception:
            pass
        print("Session setup failed:", type(error).__name__)
        return 1
    finally:
        private_path.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
