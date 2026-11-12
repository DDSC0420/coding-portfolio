"""Start a server the way a README says, wait for a page, check its text, stop it. Any OS.

    python serve_check.py URL TEXT -- command to run

Exits non-zero if the page never answers with TEXT within 90 seconds. The server's output is
printed either way, so a failure shows why.
"""

import os
import signal
import subprocess
import sys
import time
import urllib.request

# Server logs carry characters like Vite's arrow, which Windows' default code page can't print.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
url, text, sep, *command = sys.argv[1:]
assert sep == "--" and command, __doc__
windows = os.name == "nt"
proc = subprocess.Popen(
    " ".join(command),
    shell=True,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    start_new_session=not windows,
    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if windows else 0,
)
found, last = False, ""
deadline = time.monotonic() + 90
while time.monotonic() < deadline and proc.poll() is None:
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            last = resp.read().decode("utf-8", "replace")
        if text in last:
            found = True
            break
    except OSError as exc:
        last = repr(exc)
    time.sleep(1)

if windows:
    subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
else:
    os.killpg(proc.pid, signal.SIGTERM)
out = proc.communicate(timeout=30)[0].decode("utf-8", "replace")
print(out[-3000:])
print(f"{'OK' if found else 'FAILED'}: {url} {'contains' if found else 'never contained'} {text!r}")
if not found:
    print("last response:", last[:500])
sys.exit(0 if found else 1)
