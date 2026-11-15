#!/usr/bin/env python3
"""Every scenario a story implements must have a test that names it.

A scenario counts as tested when a test file contains its exact name, or when it cites
`Covered by check-invariants: "<label>"` and check-invariants.py has that case. A citation to a
case that does not exist always fails.

Which scenarios must be tested: those of the requirements a change lists in its proposal.md under
`## Requirements`, one line each, `- <capability>: <Requirement name>`. Archived changes are always
checked; pass `--change <dir>` to check one in-flight change before archiving it.
"""
import argparse, pathlib, re, sys

root = pathlib.Path(__file__).resolve().parents[1]
specs_dir, changes_dir = root / "openspec" / "specs", root / "openspec" / "changes"
TEST_GLOBS = ("*_test.go", "*.test.ts", "*.test.tsx", "*.spec.ts", "test_*.py", "*_test.py")
SKIP = {"openspec", "reference", "node_modules", ".agents", ".claude", ".git", ".mise", "parity"}


def parse_spec(path):
    """{requirement: [(scenario, cited label or None)]}"""
    reqs, req, scen = {}, None, None
    for line in path.read_text().splitlines():
        if m := re.match(r"### Requirement:\s*(.+?)\s*$", line):
            req, scen = m.group(1), None
            reqs.setdefault(req, [])
        elif req and (m := re.match(r"#### Scenario:\s*(.+?)\s*$", line)):
            scen = [m.group(1), None]
            reqs[req].append(scen)
        elif req and (m := re.search(r'Covered by check-invariants: "([^"]+)"', line)):
            if scen:
                scen[1] = m.group(1)
            else:
                reqs[req].append(["(requirement)", m.group(1)])
    return {r: [tuple(s) for s in ss] for r, ss in reqs.items()}


def load_specs(extra=None):
    caps = {p.parent.name: parse_spec(p) for p in specs_dir.glob("*/spec.md")}
    for p in (extra or []):
        for r, ss in parse_spec(p).items():
            caps.setdefault(p.parent.name, {})[r] = ss
    return caps


def test_corpus():
    files = [p for g in TEST_GLOBS for p in root.rglob(g) if not SKIP & set(p.relative_to(root).parts)]
    return "\n".join(p.read_text(errors="replace") for p in files)


labels = {m[1] for m in re.findall(r'expect\(\s*(["\'])(.+?)\1\s*,', (root / "docs" / "check-invariants.py").read_text())}
corpus = test_corpus()


def tested(name, label):
    return (label is not None and label in labels) or (name != "(requirement)" and name in corpus)


def requirements_of(change):
    text = (change / "proposal.md").read_text() if (change / "proposal.md").exists() else ""
    m = re.search(r"^## Requirements\s*$(.*?)(?=^## |\Z)", text, re.M | re.S)
    return re.findall(r"^\s*-\s*`?([a-z0-9-]+)`?\s*:\s*(.+?)\s*$", m.group(1), re.M) if m else None


ap = argparse.ArgumentParser()
ap.add_argument("--change", type=pathlib.Path, help="an in-flight change folder to check as well")
args = ap.parse_args()

errors = []
caps = load_specs()
for cap, reqs in caps.items():
    for req, scens in reqs.items():
        for name, label in scens:
            if label is not None and label not in labels:
                errors.append(f"{cap} / {req} / {name}: cites missing check-invariants case \"{label}\"")

changes = sorted((changes_dir / "archive").glob("*/")) if (changes_dir / "archive").exists() else []
if args.change:
    changes.append(args.change.resolve())
for change in changes:
    reqs = requirements_of(change)
    if reqs is None:
        errors.append(f"{change.name}: proposal.md has no '## Requirements' list")
        continue
    scoped = load_specs(sorted((change / "specs").glob("*/spec.md")))
    for cap, req in reqs:
        scens = scoped.get(cap, {}).get(req)
        if scens is None:
            errors.append(f"{change.name}: {cap} / {req}: no such requirement")
            continue
        if not scens:
            errors.append(f"{change.name}: {cap} / {req}: requirement has no scenario")
        for name, label in scens:
            if not tested(name, label):
                errors.append(f"{change.name}: {cap} / {req} / {name}: no test names this scenario")

all_scens = [(n, l) for reqs in caps.values() for ss in reqs.values() for n, l in ss if n != "(requirement)"]
print(f"scenarios: {len(all_scens)}, tested: {sum(tested(n, l) for n, l in all_scens)}, "
      f"changes checked: {len(changes)}")
for e in errors:
    print("FAIL", e)
print("ok" if not errors else f"{len(errors)} coverage error(s)")
sys.exit(1 if errors else 0)
