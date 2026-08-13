# Security and data-handling policy

## Supported versions

Before the first stable release, security fixes are applied only to the latest alpha release.

## Report privately

Do not open a public issue for a suspected vulnerability, leaked credential, non-public research
record, identifying metadata, or path disclosure. Use GitHub's private vulnerability-reporting form
for this repository. Include the affected version, a minimal synthetic reproduction, likely impact,
and any safe mitigation you have identified.

Maintainers aim to acknowledge reports within seven days and provide a remediation plan or status
update within fourteen days. Timing can change with severity and reproducibility.

## Local execution and network boundaries

PhytoLocus's compiler runs locally against caller-supplied records. Core compilation must not upload
inputs or capsules. Optional adapters may contact a service only when the caller explicitly invokes
that adapter; the adapter must disclose the destination, request fields, pagination behavior, and
returned provenance. ToolUniverse integration does not grant permission to transmit local evidence.

Treat inputs, caches, logs, and capsules as potentially sensitive. A capsule can expose sample
relationships and research intent even when it contains no primary sequence payload. Keep working data outside
the public repository, restrict permissions, and review every artifact before sharing.

## Public-release checks

Before a public release, run:

```bash
python scripts/privacy_scan.py .
python scripts/privacy_scan.py dist
```

The scanner rejects likely credentials, local absolute paths, disclosure labels,
biological file extensions, long sequence-like content, symbolic links, and binary files. Tiny
synthetic fragments are allowed only under the documented synthetic field convention. The scanner
is conservative release hygiene; it cannot establish consent, ownership, de-identification,
redistribution rights, or absence of all sensitive information.

## Integrity model

Capsule hashes detect byte changes relative to their manifest. `--expected-digest` can bind a
capsule to a digest retained through an independent channel. Neither mechanism authenticates the
origin of evidence unless an external authority independently binds that evidence. A valid capsule
may still contain mistaken source claims and may correctly remain on hold.

Do not rely on PhytoLocus for sandboxing untrusted executables. Inputs should be treated as untrusted
data: parsing must be bounded, schemas closed, symbolic links refused, and output paths controlled by
the caller.
