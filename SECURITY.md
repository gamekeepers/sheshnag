# Security Policy

Sheshnag runs other people's inference on other people's hardware. A deployment
holds user accounts, API keys, and the contents of every batch submitted to it.
We would rather hear about a problem than read about one.

## Reporting a vulnerability

**Please do not open a public issue for a security problem.** A public issue
tells everyone running Sheshnag about the hole at the same moment it tells us,
and they cannot all patch at once.

Report it privately instead:

**→ [Open a draft security advisory](https://github.com/gamekeepers/sheshnag/security/advisories/new)**

That is GitHub's private vulnerability reporting. It opens a thread visible only
to you and the maintainers, and it becomes the place where the fix is worked out
and, when we publish, the advisory itself. You need a GitHub account and nothing
else — no key exchange, no mailing list, no waiting to hear whether the address
is still monitored.

If you cannot use it for any reason, open a normal issue saying only *"I have a
security report and need a private channel"* — no detail — and a maintainer will
come back to you with one.

**What to include.** What you found, roughly how bad you think it is, and enough
detail to reproduce it — a request, a config, a sequence of steps. A proof of
concept helps but is not required, and please do not test against a deployment
you do not run.

**What to expect.**

| | |
|---|---|
| First response | within 3 working days |
| Assessment and plan | within 10 working days |
| Fix and disclosure | Coordinated with you; we will credit you unless you prefer otherwise |

If you do not hear back in the time above, please chase us — a missed report is
far more likely than an ignored one.

## Scope

**In scope** — anything that lets someone:

- read or modify batches, files or results belonging to another user or
  organisation;
- authenticate as another user, or escalate to `admin` or `superadmin`;
- use or forge an API key they were not issued (`gk-…` personal or worker keys);
- register a worker into an organisation they do not belong to, or claim a batch
  assigned elsewhere;
- execute code on the control plane, or on a provider's machine through the
  worker daemon;
- extract secrets from a deployment — `SECRET_KEY`, database credentials, OAuth
  configuration.

**Out of scope**

- Misconfiguration of a self-hosted deployment that our documentation warns
  against — for example leaving `CORS_ORIGINS` at `*`, leaving `SECRET_KEY` at
  its development default, or not changing the `admin@platform.com` password.
  If our documentation is what led you astray, that *is* worth reporting.
- Denial of service through sheer volume, and anything requiring physical
  access to a machine or an already-compromised account.
- Findings from automated scanners with no demonstrated impact.

## Supported versions

Sheshnag has not cut a release yet. The `develop` branch is the only supported
version, and fixes land there. If you run a deployment, track it — there is no
back-porting to an older commit, because there is nothing yet to back-port to.

*This section needs revisiting the day a version is tagged: a table of which
releases still receive fixes, and for how long.*

## A note for people running deployments

You are the security boundary for your own users. The
[self-host guide](docs/self-host.md) lists what has to be true before a
deployment faces anyone: a generated `SECRET_KEY`, `CORS_ORIGINS` narrowed to
your own dashboard, TLS in front of both services, and the default admin
password changed. None of those are optional, and none of them are things we can
do for you.
