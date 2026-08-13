#!/usr/bin/env python3
"""Conservatively scan a public-release tree for likely disclosure hazards.

This is release hygiene, not a complete data-loss-prevention system. The
scanner never follows symbolic links, redacts every match, and treats binary
or unreadable content as a finding instead of silently skipping it.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

EXCLUDED_DIRECTORIES = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "htmlcov",
}
EXCLUDED_FILES = {".coverage", ".DS_Store"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo"}
RISKY_BIOLOGICAL_SUFFIXES = {
    ".ab1",
    ".bam",
    ".bcf",
    ".bed",
    ".bedgraph",
    ".bedpe",
    ".bigbed",
    ".bigwig",
    ".bim",
    ".bw",
    ".cram",
    ".fa",
    ".faa",
    ".fasta",
    ".fast5",
    ".fastq",
    ".ffn",
    ".fna",
    ".fq",
    ".frn",
    ".gb",
    ".gbff",
    ".gbk",
    ".genbank",
    ".gfa",
    ".gff",
    ".gff3",
    ".gtf",
    ".h5ad",
    ".hap",
    ".haps",
    ".loom",
    ".mtx",
    ".paf",
    ".pdb",
    ".ped",
    ".pgen",
    ".pod5",
    ".psam",
    ".pvar",
    ".sam",
    ".sra",
    ".vcf",
}
MAX_TEXT_BYTES = 5_000_000
ALLOWED_CONTROLS = {9, 10, 13}
MAX_SYNTHETIC_SEQUENCE_CHARS = 32
SYNTHETIC_SEQUENCE_FIELDS = {"synthetic_sequence", "synthetic_sequence_fragment"}
SEQUENCE_FIELD = re.compile(
    r"(?:^|_)(?:raw_)?(?:sequence|seq|bases?|nucleotides?|amino_acids?)(?:_|$)",
    re.IGNORECASE,
)
BIOLOGICAL_ALPHABET = frozenset("ACDEFGHIKLMNPQRSTVWYBXZJUO*")
NUCLEOTIDE_ALPHABET = frozenset("ACGTUNRYKMSWBDHV")
REDACTED_PATH = "<redacted-path>"


@dataclass(frozen=True, slots=True)
class Finding:
    rule: str
    path: str
    line: int
    excerpt: str


def _patterns() -> tuple[tuple[str, re.Pattern[str]], ...]:
    """Build sensitive patterns without embedding scan-triggering examples."""

    slash_roots = "|".join(
        [
            "Us" + "ers",
            "ho" + "me",
            "ro" + "ot",
            "work" + "space",
            "t" + "mp",
            "pri" + "vate",
            "v" + "ar",
            "da" + "ta",
            "scra" + "tch",
            "s" + "rv",
            "o" + "pt",
            "me" + "dia",
            "r" + "un/user",
            "Volumes",
            "mnt",
        ]
    )
    posix_path = rf"(?<![A-Za-z0-9])/(?:{slash_roots})(?:/[^\s\"'<>]+)?(?=$|[\s\"'<>])"
    backslash = re.escape(chr(92))
    windows_path = rf"\b[A-Za-z]:(?:/|{backslash}+)[^\s\"'<>]+"
    unc_path = (
        rf"(?:{backslash}+){{2}}[^{backslash}\s\"'<>]+"
        rf"(?:{backslash}+)[^\s\"'<>]+"
    )
    file_uri = "fi" + r"le://[^\s\"'<>]+"
    environment_path = (
        r"(?<![A-Za-z0-9])(?:~|\$\{?(?:HOME|USERPROFILE|HOMEPATH|TMPDIR|TEMP|TMP)\}?|"
        r"%(?:USERPROFILE|HOMEPATH|TEMP|TMP)%)(?:/|" + backslash + r"+)[^\s\"'<>]+"
    )
    disclosure_labels = [
        "confi" + "dential",
        "not for" + " distribution",
        "not for public" + " release",
        "internal" + " project",
        "internal" + " only",
        "private" + " project",
        "unpublished" + " data",
        "embargoed" + " dataset",
        "do not" + " share",
    ]
    unpublished_sequence_markers = [
        "raw" + " sequence",
        "unpublished" + " sequence",
        "private" + " sequence",
        "real biological" + " sequence",
    ]
    github_token = r"\b(?:gh" + r"[psoru]_|github_pat_)[A-Za-z0-9_]{16,}\b"
    openai_token = r"\bsk" + r"-[A-Za-z0-9_-]{16,}\b"
    slack_token = r"\bxox" + r"[baprs]-[A-Za-z0-9-]{16,}\b"
    aws_access_key = r"\b(?:AK" + r"IA|ASIA)[0-9A-Z]{16}\b"
    private_key = r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE" + r" KEY-----"
    assigned_secret = (
        r"[\"']?\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|password|passwd|"
        r"client[_-]?secret|private[_-]?key|session[_-]?token)\b[\"']?\s*[:=]\s*"
        r"[\"']?[^\s,;}{\"']{4,}"
    )
    aws_assignment = (
        r"\bAWS_(?:ACCESS_KEY_ID|SECRET_ACCESS_KEY|SESSION_TOKEN)\s*[:=]\s*"
        r"[\"']?[A-Za-z0-9/+=_-]{4,}"
    )
    bearer = r"\bBearer[ \t]+[A-Za-z0-9._~+/=-]{12,}"
    jwt = r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{8,}\b"
    flexible = lambda term: re.escape(term).replace(r"\ ", r"[\s_-]+")  # noqa: E731
    label_pattern = "|".join(map(flexible, disclosure_labels))
    sequence_marker_pattern = "|".join(map(flexible, unpublished_sequence_markers))
    return (
        ("absolute-local-path", re.compile(posix_path)),
        ("windows-local-path", re.compile(windows_path, re.IGNORECASE)),
        ("unc-local-path", re.compile(unc_path, re.IGNORECASE)),
        ("file-uri", re.compile(file_uri, re.IGNORECASE)),
        ("environment-local-path", re.compile(environment_path, re.IGNORECASE)),
        (
            "private-label",
            re.compile(r"\b(?:" + label_pattern + r")\b", re.IGNORECASE),
        ),
        (
            "nonpublic-biological-marker",
            re.compile(r"\b(?:" + sequence_marker_pattern + r")\b", re.IGNORECASE),
        ),
        ("github-token", re.compile(github_token)),
        ("openai-token", re.compile(openai_token)),
        ("slack-token", re.compile(slack_token)),
        ("aws-access-key", re.compile(aws_access_key)),
        ("private-key", re.compile(private_key)),
        ("assigned-secret", re.compile(assigned_secret, re.IGNORECASE)),
        ("aws-secret-assignment", re.compile(aws_assignment, re.IGNORECASE)),
        ("bearer-token", re.compile(bearer, re.IGNORECASE)),
        ("jwt-token", re.compile(jwt)),
    )


URL_PATTERN = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
SECRET_QUERY_NAMES = {
    "access_key",
    "access_key_id",
    "access_token",
    "api_key",
    "apikey",
    "auth_token",
    "client_secret",
    "code",
    "credential",
    "credentials",
    "jwt",
    "key",
    "oauth_code",
    "oauth_token",
    "password",
    "passwd",
    "private_key",
    "session",
    "session_id",
    "session_token",
    "sig",
    "signature",
    "token",
}
KNOWN_SHA256_VALUE = re.compile(
    r"(?P<prefix>(?i:(?:[A-Za-z0-9_-]*sha(?:-?256)?))[\"']?\s*[:=]\s*[\"'`]?)"
    r"(?P<digest>[0-9a-f]{64})(?=$|[\"'`\s,}\]])"
)


def _safe_excerpt(_line: str, _start: int, _end: int) -> str:
    return "<redacted>"


def _line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _credential_url(text: str) -> Iterator[tuple[int, int]]:
    for match in URL_PATTERN.finditer(text):
        candidate = match.group(0).rstrip(".,);]}")
        try:
            parsed = urlsplit(candidate)
        except ValueError:
            yield match.span()
            continue
        unsafe = parsed.username is not None or parsed.password is not None
        if not unsafe:
            for raw_name, value in parse_qsl(parsed.query, keep_blank_values=True):
                name = raw_name.casefold().replace("-", "_").replace(".", "_")
                if value and (name.startswith(("x_amz_", "x_goog_")) or name in SECRET_QUERY_NAMES):
                    unsafe = True
                    break
        if unsafe:
            yield match.span()


def _compact_sequence(value: str) -> str:
    return re.sub(r"[\s\d-]", "", value).upper()


def _sequence_like(value: str, *, minimum: int = 40) -> bool:
    """Recognize common contiguous, wrapped, and position-grouped sequence text."""

    masked = KNOWN_SHA256_VALUE.sub(lambda match: match.group("prefix") + ("0" * 64), value)
    if re.search(r"[^A-Za-z*\s\d-]", masked):
        return False
    compact = _compact_sequence(masked)
    if len(compact) < minimum:
        return False
    alphabet = set(compact)
    if alphabet <= NUCLEOTIDE_ALPHABET:
        return True
    if not alphabet <= BIOLOGICAL_ALPHABET:
        return False

    letters = re.sub(r"[^A-Za-z]", "", masked)
    lines = [line.strip() for line in masked.splitlines() if line.strip()]
    groups = re.findall(r"[A-Za-z*]+", masked)
    if not letters:
        return False
    if letters.isupper() or letters.islower():
        if lines and all(not re.search(r"[ \t]", line) for line in lines):
            return True
    else:
        wrapped_lines = (
            len(lines) >= 2
            and all(not re.search(r"[ \t]", line) for line in lines)
            and min(map(len, lines)) >= 12
        )
        grouped_wrap = (
            len(groups) >= 2
            and min(map(len, groups)) >= 12
            and max(map(len, groups)) - min(map(len, groups)) <= 1
        )
        if wrapped_lines or grouped_wrap:
            return True
    if len(groups) < 4:
        return False
    width = len(groups[0])
    return (
        width >= 4
        and all(len(group) == width for group in groups[:-1])
        and 1 <= len(groups[-1]) <= width
    )


def _sequence_spans(text: str) -> Iterator[tuple[int, int]]:
    masked = KNOWN_SHA256_VALUE.sub(lambda match: match.group("prefix") + ("0" * 64), text)
    if _sequence_like(masked):
        yield (0, len(text))
        return
    for match in re.finditer(r"(?<![A-Za-z*])[A-Za-z*]{40,}(?![A-Za-z*])", masked):
        if _sequence_like(match.group(0)):
            yield match.span()
    for match in re.finditer(
        r"(?<![A-Za-z*])(?:[A-Za-z*]{4,}(?:[ \t\d-]+|\r?\n)){1,}"
        r"[A-Za-z*]{1,}(?![A-Za-z*])",
        masked,
    ):
        if _sequence_like(match.group(0)):
            yield match.span()


def _normalized_field_name(key: str) -> str:
    separated = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", key)
    separated = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", separated)
    return re.sub(r"[^A-Za-z0-9]+", "_", separated).strip("_").casefold()


def _sequence_strings(value: object) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for child in value:
            yield from _sequence_strings(child)


def _json_sequence_findings(value: object, *, path: str) -> Iterator[Finding]:
    if isinstance(value, dict):
        for key, child in value.items():
            key_text = _normalized_field_name(key) if isinstance(key, str) else ""
            identifier_or_commitment_suffixes = (
                "_sha256",
                "_checksum",
                "_digest",
                "_hash",
                "_id",
                "_ids",
                "_accession",
                "_accessions",
                "_length",
                "_path",
                "_uri",
                "_url",
            )
            commitment_field = key_text.endswith(identifier_or_commitment_suffixes)
            if isinstance(key, str) and SEQUENCE_FIELD.search(key_text) and not commitment_field:
                compact = _compact_sequence("".join(_sequence_strings(child)))
                sequence_like = len(compact) >= 8 and (
                    set(compact) <= NUCLEOTIDE_ALPHABET or set(compact) <= BIOLOGICAL_ALPHABET
                )
                allowed = key_text in SYNTHETIC_SEQUENCE_FIELDS and (
                    len(compact) <= MAX_SYNTHETIC_SEQUENCE_CHARS
                )
                if sequence_like and not allowed:
                    yield Finding("sequence-field", path, 1, "<redacted>")
            yield from _json_sequence_findings(child, path=path)
    elif isinstance(value, list):
        for child in value:
            yield from _json_sequence_findings(child, path=path)


def _absolute_without_resolving(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _display_path(path: Path, root: Path, *, root_is_file: bool) -> str:
    return path.name if root_is_file else path.relative_to(root).as_posix()


def _candidates(
    root: Path,
    *,
    enumeration_failures: list[None] | None = None,
) -> Iterator[Path]:
    try:
        root_status = os.stat(root, follow_symlinks=False)
    except OSError:
        yield root
        return
    if stat.S_ISLNK(root_status.st_mode) or not stat.S_ISDIR(root_status.st_mode):
        yield root
        return

    def record_enumeration_failure(_error: OSError) -> None:
        if enumeration_failures is not None:
            enumeration_failures.append(None)

    for current, directories, names in os.walk(
        root,
        followlinks=False,
        onerror=record_enumeration_failure,
    ):
        base = Path(current)
        kept: list[str] = []
        for name in sorted(directories):
            candidate = base / name
            try:
                candidate_status = os.stat(candidate, follow_symlinks=False)
            except OSError:
                yield candidate
                continue
            if stat.S_ISLNK(candidate_status.st_mode):
                yield candidate
            elif stat.S_ISDIR(candidate_status.st_mode):
                if name not in EXCLUDED_DIRECTORIES:
                    kept.append(name)
            else:
                yield candidate
        directories[:] = kept
        for name in sorted(names):
            path = base / name
            try:
                candidate_status = os.stat(path, follow_symlinks=False)
            except OSError:
                yield path
                continue
            if stat.S_ISLNK(candidate_status.st_mode) or not stat.S_ISREG(candidate_status.st_mode):
                yield path
                continue
            if name in EXCLUDED_FILES or path.suffix.casefold() in EXCLUDED_SUFFIXES:
                continue
            yield path


def _file_identity(item: os.stat_result) -> tuple[int, int]:
    return item.st_dev, item.st_ino


def _stable_file_metadata(item: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        stat.S_IFMT(item.st_mode),
        item.st_size,
        item.st_mtime_ns,
        item.st_ctime_ns,
        item.st_nlink,
    )


def _owned_bytes(path: Path) -> tuple[bytes | None, str | None]:
    try:
        named_before = os.stat(path, follow_symlinks=False)
    except OSError:
        return None, "file-open-failed"
    if not stat.S_ISREG(named_before.st_mode):
        return None, "non-regular-file"
    if named_before.st_nlink != 1:
        return None, "multiple-hard-links"
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    try:
        descriptor = os.open(path, flags)
    except OSError:
        return None, "file-open-failed"
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            return None, "non-regular-file"
        if _file_identity(named_before) != _file_identity(before) or _stable_file_metadata(
            named_before
        ) != _stable_file_metadata(before):
            return None, "file-changed-during-scan"
        if before.st_nlink != 1:
            return None, "multiple-hard-links"
        if before.st_size > MAX_TEXT_BYTES:
            return None, "file-too-large"
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(1 << 20, MAX_TEXT_BYTES + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > MAX_TEXT_BYTES:
                return None, "file-too-large"
        after = os.fstat(descriptor)
        try:
            named_after = os.stat(path, follow_symlinks=False)
        except OSError:
            return None, "file-changed-during-scan"
        snapshots = (named_before, before, after, named_after)
        if len({_file_identity(item) for item in snapshots}) != 1:
            return None, "file-changed-during-scan"
        if len({_stable_file_metadata(item) for item in snapshots}) != 1:
            return None, "file-changed-during-scan"
        data = b"".join(chunks)
        if len(data) != before.st_size:
            return None, "file-changed-during-scan"
        return data, None
    finally:
        os.close(descriptor)


def _scan_text(
    text: str,
    *,
    path: str,
    rules: tuple[tuple[str, re.Pattern[str]], ...],
) -> list[Finding]:
    normalized = re.sub(r"\\u0*02[fF]", "/", text)
    normalized = re.sub(r"\\u0*05[cC]", lambda _match: chr(92), normalized)
    normalized = re.sub(r"%2[fF]", "/", normalized)
    normalized = re.sub(r"%5[cC]", lambda _match: chr(92), normalized)
    normalized = re.sub(r"%3[aA]", ":", normalized)
    normalized = normalized.replace("\\/", "/")
    for escaped_whitespace in ("\\n", "\\r", "\\t"):
        normalized = normalized.replace(escaped_whitespace, "  ")
    findings: list[Finding] = []
    for rule_name, pattern in rules:
        for match in pattern.finditer(normalized):
            findings.append(
                Finding(
                    rule=rule_name,
                    path=path,
                    line=_line_number(normalized, match.start()),
                    excerpt=_safe_excerpt(normalized, match.start(), match.end()),
                )
            )
    for start, end in _credential_url(normalized):
        findings.append(
            Finding(
                rule="credential-url",
                path=path,
                line=_line_number(normalized, start),
                excerpt=_safe_excerpt(normalized, start, end),
            )
        )
    for start, end in _sequence_spans(normalized):
        findings.append(
            Finding(
                rule="raw-biological-sequence",
                path=path,
                line=_line_number(normalized, start),
                excerpt=_safe_excerpt(normalized, start, end),
            )
        )
    return findings


def _parse_json(text: str) -> tuple[object, bool]:
    duplicate_key = False

    def object_from_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
        nonlocal duplicate_key
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                duplicate_key = True
            result[key] = value
        return result

    return json.loads(text, object_pairs_hook=object_from_pairs), duplicate_key


def scan(root: Path, forbidden_terms: Iterable[str] = ()) -> list[Finding]:
    root = _absolute_without_resolving(root)
    base_rules = list(_patterns())
    for index, term in enumerate(forbidden_terms, start=1):
        if term:
            base_rules.append(
                (f"forbidden-term-{index}", re.compile(re.escape(term), re.IGNORECASE))
            )
    rules = tuple(base_rules)
    findings: list[Finding] = []
    enumeration_failures: list[None] = []
    root_is_file = not root.is_dir() or root.is_symlink()
    for path in _candidates(root, enumeration_failures=enumeration_failures):
        display = _display_path(path, root, root_is_file=root_is_file)
        path_findings = _scan_text(display, path=REDACTED_PATH, rules=rules)
        findings.extend(
            Finding(f"path-{item.rule}", REDACTED_PATH, 0, "<redacted>") for item in path_findings
        )
        if path.is_symlink():
            findings.append(Finding("symbolic-link", REDACTED_PATH, 0, "<redacted>"))
            try:
                target = os.readlink(path)
            except OSError:
                findings.append(
                    Finding("symlink-target-unavailable", REDACTED_PATH, 0, "<redacted>")
                )
                continue
            target_findings = _scan_text(target, path=REDACTED_PATH, rules=rules)
            findings.extend(
                Finding(f"symlink-target-{item.rule}", REDACTED_PATH, 0, item.excerpt)
                for item in target_findings
            )
            continue
        if path.suffix.casefold() in RISKY_BIOLOGICAL_SUFFIXES:
            findings.append(Finding("biological-file-extension", REDACTED_PATH, 0, "<redacted>"))
            continue
        data, error = _owned_bytes(path)
        if error is not None:
            findings.append(Finding(error, REDACTED_PATH, 0, "<redacted>"))
            continue
        assert data is not None
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            findings.append(Finding("content-not-utf8", REDACTED_PATH, 0, "<redacted>"))
            continue
        if any(
            (ord(character) < 32 and ord(character) not in ALLOWED_CONTROLS)
            or 127 <= ord(character) <= 159
            for character in text
        ):
            findings.append(Finding("binary-control-content", REDACTED_PATH, 0, "<redacted>"))
            continue
        findings.extend(_scan_text(text, path=REDACTED_PATH, rules=rules))
        if path.suffix.casefold() == ".json":
            try:
                parsed, duplicate_key = _parse_json(text)
            except (json.JSONDecodeError, UnicodeDecodeError):
                pass
            else:
                if duplicate_key:
                    findings.append(Finding("duplicate-json-key", REDACTED_PATH, 1, "<redacted>"))
                findings.extend(_json_sequence_findings(parsed, path=REDACTED_PATH))
    findings.extend(
        Finding("directory-enumeration-failed", REDACTED_PATH, 0, "<redacted>")
        for _failure in enumeration_failures
    )
    return findings


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Scan a public-release tree for local paths, disclosure labels, "
            "biological payloads, credentials, symbolic links, and binary content."
        )
    )
    parser.add_argument("path", nargs="?", default=".", help="File or directory to scan.")
    parser.add_argument(
        "--forbidden-term",
        action="append",
        default=[],
        help="Additional case-insensitive term to reject; repeat as needed.",
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    path = Path(arguments.path)
    if not path.exists() and not path.is_symlink():
        print("privacy scan failed: path does not exist", file=sys.stderr)
        return 2
    findings = scan(path, arguments.forbidden_term)
    if arguments.json:
        print(json.dumps([asdict(item) for item in findings], indent=2, sort_keys=True))
    elif findings:
        for item in findings:
            print(f"{item.path}:{item.line}: {item.rule}: {item.excerpt}")
    else:
        print("privacy scan passed: no blocked patterns found")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
