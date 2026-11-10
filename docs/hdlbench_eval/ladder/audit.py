"""Scan a subagent transcript for access outside the allowed files (prints only counts and truncated matching commands)."""
import json, re, sys
path = sys.argv[1]
bad = re.compile(r"sparky-ee-llm-tools|reference\.v|/problems/|hdlbench/|score\.py|formal\.py|/runs/|work/(pilot)|\.\./|eval/specs/\.\.|find /|grep -r|ls /home|cat /home/user/(?!eval/(specs|work/ladder))")
allowed_hint = re.compile(r"/home/user/eval/(specs|work/ladder|score\.sh)")
n = hits = 0
for line in open(path):
    try: o = json.loads(line)
    except Exception: continue
    s = json.dumps(o)
    for m in re.finditer(r'"(?:command|file_path|path|pattern)"\s*:\s*"((?:[^"\\]|\\.)*)"', s):
        cmd = m.group(1); n += 1
        stripped = re.sub(r"/home/user/eval/score\.sh ladder \w+ /home/user/eval/work/ladder/\S+", "", cmd)
        if bad.search(stripped) and not re.fullmatch(r"\s*", stripped):
            hits += 1; print("  SUSPICIOUS:", cmd[:200].replace("\\n", " "))
print(f"tool inputs scanned: {n}, suspicious: {hits}")
