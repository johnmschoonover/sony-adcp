# Building a client on pyadcp

This page is for anyone writing a client on top of pyadcp, the Home
Assistant integration first among them. It explains what the library does
for you, what it deliberately leaves to the client, and why the line sits
where it does. The library contains no Home Assistant code and never will;
see "The boundary" below.

## The boundary

The rule of thumb: if a second, non-Home-Assistant consumer (the command
line helper, a script, another automation platform) would want the same
behaviour, it lives in the library. If it only makes sense in terms of
config entries, coordinators and entity platforms, it lives in the client.

| Concern | Lives in | Why |
|---|---|---|
| Wire protocol, handshake, framing, reconnect, retries | library | Protocol knowledge; every client needs it identically. |
| Probing a host: does it speak ADCP, does it need a password (`probe`) | library | Reading the greeting is protocol knowledge. |
| Choosing between ADCP and another protocol after a probe | client | Which fallback exists, and what to tell the user, are client decisions. |
| Typed errors per `err_*` reply | library | The protocol defines them; clients only choose what to do. |
| Command table and rendering of every form | library | Derived from the manual once, tested once. |
| Capability discovery and its cache | library | The probe loop is the only place progress can be reported from. |
| Per-suffix options and ranges | library | A question of how the protocol's replies are modelled. |
| Serialising capabilities (`to_dict` / `from_dict`) | library | The library owns the layout and its version, so every client round-trips it the same way. |
| Comparing two snapshots (`changes_since`) | library | "What changed" is defined on library data and should mean one thing. |
| Progress events during discovery | library | Only the probe loop can emit them. |
| When to run discovery, and when to re-run it | client | Depends on config flow, setup and power-state lifecycle. |
| Where the serialised snapshot is stored and when it is invalidated | client | Config entry storage, firmware-change policy and user refresh are client concepts. |
| Mapping a capability to an entity type | client | Select, number, switch and button are the client's vocabulary, not the protocol's. |
| Adding or removing entities when a later discovery changes things | client | Entity registry and platform lifecycle. |
| Polling cadence, availability, unique IDs, device info | client | Platform policy. The library supplies the data via `poll_status()` and `identify()`. |

## What the library gives you

### `probe()`: is this ADCP?

```python
from pyadcp import ProbeOutcome, probe

result = await probe(host)                       # greeting only
result = await probe(host, password=password)    # greeting, then the password check
result.outcome            # a ProbeOutcome
result.is_adcp            # True / False / None (cannot tell)
result.auth_required      # True / False / None
result.password_accepted  # True / False / None
```

`probe` opens one TCP connection, reads the line the projector sends on
connect, optionally answers the challenge, and closes. It never sends a
command and closes the socket on every path, cancellation included. Expected
outcomes are returned, not raised:

| Outcome | Meaning | `is_adcp` |
|---|---|---|
| `REFUSED` | The host refused the TCP connection: nothing listens on the port. | `False` |
| `UNREACHABLE` | No TCP connection: bad name, no route, or the connect timed out. | `None` |
| `TIMEOUT` | Connected, but no complete greeting line in time. | `None` |
| `CLOSED` | Connected, then closed or reset by the peer without a greeting. | `None` |
| `NOT_ADCP` | A greeting arrived that is not `NOKEY`, a hex nonce or `err_auth`. | `False` |
| `HOST_DENIED` | Greeted with `err_auth`: ADCP, but this client is not allowed. | `True` |
| `NO_AUTH` | ADCP, authentication off (`NOKEY`). A given password is ignored. | `True` |
| `AUTH_REQUIRED` | ADCP, authentication on; no password was given. | `True` |
| `AUTH_OK` | ADCP, and the projector accepted the password. | `True` |
| `AUTH_FAILED` | ADCP, and the projector rejected the password (`err_auth`). | `True` |
| `AUTH_UNCONFIRMED` | ADCP, password sent, but no `OK` / `err_auth` came back. | `True` |

The string values (`"auth_failed"` and so on) are stable and safe to
store. `result.detail` carries a human-readable reason for logs;
`result.elapsed` says how long the probe took.

**Choosing the timeout.** The default, `DEFAULT_PROBE_TIMEOUT` (15 s), covers
the whole probe. The manual documents up to 10 s of silence from a unit in
low-power standby before it answers its first command; it does not say
whether the greeting is held back too, so the default leaves room for that
plus connection set-up. A shorter timeout risks reporting a sleeping
projector as `TIMEOUT`. On a fast LAN with units known to be awake, 5 s is
plenty; never go below a couple of seconds.

### `capabilities()` and the cache

```python
caps = await projector.capabilities()
```

Probes `? --info` for every command in the table and returns a
`Capabilities` object with three sets:

- `commands`: supported, keyed by name, each a `CommandCapability` with
  `kind`, `options`, `range`, `suffixes` and `by_suffix`.
- `unsupported`: commands the projector answered `err_cmd` to.
- `incomplete`: supported commands whose details the projector withheld
  (`err_inactive`, typically in standby). The next `capabilities()` call
  re-probes only those.

A full discovery is about 200 round trips. `names=[...]` limits it to the
commands you care about, and a plain call afterwards completes whatever is
still unclassified. The result is cached on the `Projector` for its lifetime.

### Per-suffix detail

Suffixed commands (an HDMI input, a screen edge, a colour channel) carry
`by_suffix`, a mapping from suffix to `SuffixCapability` with that suffix's
own `options`, `range` and `incomplete` flag. `caps.options(name, suffix)`
and `caps.range(name, suffix)` read them. The top-level `options` and
`range` are those of the first fully described suffix, for callers that do
not care which.

### Persisting across restarts

```python
stored = caps.to_dict()          # JSON-compatible, carries a "format" version
...
projector.restore_capabilities(Capabilities.from_dict(stored))
caps = await projector.capabilities()   # probes only what the snapshot left incomplete
```

`from_dict` raises `ValueError` for a snapshot written by a newer library
format or for malformed data. Treat that as "discover again". Pass
`refresh=True` to discard a restored snapshot deliberately.

### Reacting to change

```python
changes = new_caps.changes_since(old_caps)
changes.added       # supported now, not before
changes.removed     # supported before, not now
changes.changed     # options, range or suffixes differ
changes.completed   # were incomplete, now fully described
changes.affected    # added | removed | changed
```

Use it after a re-probe (for example when the projector first reports
`on`) to decide which entities to create, remove or update, instead of
rebuilding everything.

### Progress

```python
async for event in projector.discover_capabilities():
    event.name, event.index, event.total        # "picture_mode", 17, 139
    event.capability, event.incomplete           # what was learned
    event.snapshot                               # cumulative Capabilities so far
```

or, for a synchronous callback, `capabilities(progress=callback)`. The
cache is updated after every probe, so stopping the iterator early keeps
what was learned. Wrap the iterator in `contextlib.aclosing` if you may
break out of it.

## What the projector must have switched on

Everything the library needs is in the projector's web page, under its
network and service settings. Tell owners about these four things; nothing
else there matters to pyadcp.

- **ADCP** (on by default) is the control protocol itself. Without it
  `probe()` reports `REFUSED` or `TIMEOUT` and nothing works.
- **Advertisement** (on by default) is the SDAP broadcast that `discover()`
  listens for. With it off the unit is invisible to discovery and the
  owner has to enter the address by hand; everything else still works.
  Discovery also needs the client on the same subnet, since the packet is
  a broadcast that routers do not forward.
- **Standby mode "Standard"** or **Network management "On"** in the
  projector menu keeps the network board awake in standby. Without it the
  unit cannot be reached or woken over the network once it is off.
- The **ADCP allow-list**, when the owner has filled it in, must include
  the client's address or `probe()` reports `HOST_DENIED`.

PJ Talk, DDDP and the web server's own settings are not involved; a
VPL-VW295ES with PJ Talk and DDDP switched off was discovered and
controlled normally. When `discover()` returns an empty list, say so in
those words: no projector advertised itself within the window, so either
the Advertisement service is off, the projector is on another subnet, or
it is not on the network at all; offer manual entry of the address rather
than reporting a failure.

ADCP itself has no discovery: it is a TCP command channel that only speaks
when connected to, and the manuals define no other announcement than SDAP.
The one alternative is an active sweep, `probe()` against every address on
the subnet, which finds a unit with Advertisement off but is a port scan
that misses non-default ports and allow-listed hosts and that Home
Assistant discourages. The library leaves that choice to the client.

## Choosing a backend in a config flow

A client that supports both ADCP projectors and older units through a
different protocol needs to decide which library to use for a host. The
library answers the question; the routing is the client's:

1. **Probe first.** `await probe(host)` before asking for anything else.
   It costs one connection and sends no command.
2. **Prefer ADCP.** If `result.is_adcp` is `True`, use pyadcp. If the
   outcome is `AUTH_REQUIRED`, ask for the password (the factory value is
   `DEFAULT_PASSWORD`) and probe again with it; the "Passwords" section
   below has the full loop.
3. **Never fall back on an authentication failure.** `AUTH_FAILED` means
   "this IS an ADCP projector and the password is wrong": show a password
   error. The same goes for `HOST_DENIED` (the projector's allow-list) and
   `AUTH_UNCONFIRMED` (retry). A client that tried the other protocol on
   any of these would silently pick the wrong library.
4. **Fall back only when the answer is "not ADCP".** `NOT_ADCP` and
   `REFUSED` (`is_adcp is False`) are the clear cases. `TIMEOUT`, `CLOSED` and `UNREACHABLE` (`is_adcp is None`) mean
   the probe could not tell: a sleeping or busy unit can look like this, so
   retry or report the host as unreachable before falling back. The ADCP
   port is configurable on the projector; probe the port the user gave.
5. **Store the result.** Persist which backend was chosen (and the port) in
   the client's configuration so setup does not probe again on every
   start. Probe again only when the user reconfigures the host.

## Passwords: asking, retrying and re-authenticating

Authentication is on by default and the factory password is
`DEFAULT_PASSWORD` (`Projector`), but owners change it, and some switch
authentication off altogether. The library tells the client which case it
is in; the client owns the prompts. The flow below avoids asking for a
password when none is needed and never stores one the projector has not
accepted.

Two settings on the projector's web page are involved. The ADCP page
turns authentication on or off (and sets the port and time-out); it has
no password field. Once authentication is on, the password ADCP checks is
the **Administrator** (`root`) password from the Password page. That page
also has a **User** account with its own name and password; ADCP ignores
it, and there is no user name in the handshake. A prompt should say
"projector administrator password" so an owner who has created a User
account does not enter that one (verified on a VPL-VW295ES: the User
password was rejected with `err_auth`, the Administrator password
accepted).

### At setup

1. **Probe without a password.** `result = await probe(host, port)`.
2. **`NO_AUTH`**: skip the password step entirely. Store an empty
   password; `Projector` ignores whatever it is given when the greeting is
   `NOKEY`.
3. **`AUTH_REQUIRED`**: show a password field, prefilled with
   `DEFAULT_PASSWORD`, and probe again with the entry:
   `await probe(host, port, password=entry)`.
4. **`AUTH_OK`**: the projector accepted it. Store the password and go on
   to `identify()`.
5. **`AUTH_FAILED`**: the projector answered `err_auth`. Show an
   "invalid password" error on the same field and let the user try again.
   Do not fall back to another backend and do not store the value.
6. **`AUTH_UNCONFIRMED`**: the password was sent but no verdict arrived in
   time. Retry the same probe once before showing an error; a unit in
   low-power standby can be slow.
7. **`HOST_DENIED`**: the projector greeted with `err_auth` before any
   password was sent, so its allow-list rejects this address. No password
   will help; tell the user to add the client's address on the projector's
   web page.

Every outcome comes back on `ProbeResult`, not as an exception, so this
loop needs no `try`. A probe costs one TCP connection and sends no
command, so repeating it on each attempt is cheap (a VPL-VW295ES answers
in a few milliseconds).

### While running

`Projector` and `AdcpConnection` raise `AuthenticationError` when a
handshake fails after setup: the owner changed the password, or turned
authentication on after a setup that saw `NOKEY`. The library never
retries this error (the session is useless after `err_auth`, and the
projector leaves the socket open, so the library closes it). Map it to a
re-authentication flow:

1. Mark the entry as needing re-authentication and stop polling.
2. Ask for the new password and verify it with
   `probe(host, port, password=entry)` before saving, exactly as at setup.
   `AUTH_OK` saves and reloads; `AUTH_FAILED` re-prompts.
3. If the probe reports `NO_AUTH`, the owner switched authentication off.
   Clear the stored password and reload; no prompt is needed.

The reverse change, authentication switched off while a password is
stored, needs no handling: the next connect sees `NOKEY`, the stored
password is ignored and everything keeps working.

### What the library does not do

It does not cache or persist passwords, does not retry a rejected one, and
does not decide what the prompt looks like or how many attempts to allow.
It also cannot tell a wrong password from a changed one; both are
`AUTH_FAILED` / `AuthenticationError`.

## A suggested client flow

1. **Config flow**: offer the hosts `discover()` found and manual entry
   (with the empty-result wording above), then `probe()` the chosen host
   and settle the password as described above, then connect, call `identify()` for the model name, serial and
   MAC (unique ID), and optionally `capabilities(names=[...])` for a
   handful of commands so the first boot is fast.
2. **Setup**: `restore_capabilities()` from the stored snapshot if there is
   one, then `capabilities()`. Store `to_dict()` afterwards.
3. **Entities from capabilities**, with no model names anywhere:
   - a select for each supported command with `options`,
   - a number for each with a `range`, one per suffix for suffixed commands,
   - a switch for selects whose options are exactly `on` and `off`,
   - a button for each exec command and for the remote keys you expose;
     `caps.options("key")` lists the codes this unit accepts,
   - sensors from `poll_status()`.
4. **Polling**: `poll_status()` on the coordinator interval. It is five
   round trips; identity fields come from the cached `identify()`.
   Picture settings answer `err_inactive` while the unit is on with no
   input signal, not only in standby.
5. **After power on**: if `caps.incomplete` is non-empty, call
   `capabilities()` again, compute `changes_since`, and add the entities
   that now have options. Store the new snapshot.
6. **Errors**: catch `AdcpError` in the coordinator; `AuthenticationError`
   is the one to map to the re-authentication flow above. A
   `CommandInactiveError` on a setter means the projector is in the wrong
   power state or has no input signal, not that the entity is wrong.

## What the library will not do

- It will not choose entity types or names. Those are platform decisions.
- It will not store anything. There is no file or registry access in the
  library.
- It will not poll on its own. Every round trip happens inside a call the
  client makes.
- It will not carry per-model tables. Everything above comes from the
  connected projector at runtime.
