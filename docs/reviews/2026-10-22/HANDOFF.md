# edge-tts engineering handoff

Review date: **22 October 2026**. All findings remain **OPEN**. This handoff contains proposed repairs and release checks; no source fixes or new runtime acceptance are claimed.

**Reviewed source:** [`master` at `4bdb8e4c6ea62f151a45a3fceb4cf6ff696bb89f`](https://github.com/wwongw21/edge-tts/tree/4bdb8e4c6ea62f151a45a3fceb4cf6ff696bb89f).

The full structured handoff is in [report.json](report.json). Historical commands retain their original review-workspace paths; read the prerequisites before running them. Only repository-specific diagnostics listed below are attached.

## Audience and required outcome

### Audience

1. Python library maintainer

2. Application engineer embedding streaming TTS

3. Release reviewer

### Outcome

#### Summary

Three confirmed Medium defects affect text chunk limits, synchronous error propagation, and custom connector lifetime. All remain OPEN; this is an implementation handoff, with no code repair applied.

#### Review mode

Read-only repository review; local focused reproductions

#### Source change status

No project source edits, commits, pushes, releases, or publication

#### Release decision

Repair and validate these behaviors before relying on long-input or custom-connector synthesis. The review is not a release certification.

#### Counts

##### Open medium

3

##### Closed

0

## Scope and reviewed branches

1. text sanitation and chunk splitting

2. SSML configuration and protocol parsing

3. async and synchronous streaming

4. custom connector ownership

5. voice lookup

6. packaging and test inventory

## Architecture

### Product

Python library and CLI for Microsoft's online Edge text-to-speech service, with optional audio playback and subtitle output.

### Runtime flow

1. Communicate.__init__ validates voice/prosody, sanitizes text, XML-escapes it and creates a lazy byte-chunk generator.

2. Communicate.stream iterates text chunks, invokes a per-chunk WebSocket exchange and retries once after supported HTTP 403 clock-skew handling.

3. Communicate.__stream sends configuration plus generated SSML, then yields MP3 audio and boundary metadata; chunk audio byte counts adjust subsequent timing offsets.

4. save writes streamed audio and metadata; stream_sync bridges the async generator through a worker thread and Queue; save_sync runs async save through a worker Future.

5. CLI/util modules compose library synthesis with subtitles; edge_playback modules use OS/player integration.

### Components

#### Item 1

##### Paths

1. src/edge_tts/communicate.py

##### Responsibility

Text sanitation/chunking, SSML, WebSocket synthesis, metadata parsing, async/sync APIs

##### Review relevance

All three confirmed defects are here.

#### Item 2

##### Paths

1. src/edge_tts/data_classes.py

2. src/edge_tts/constants.py

3. src/edge_tts/drm.py

##### Responsibility

TTS configuration, protocol constants and service clock/authentication compatibility

#### Item 3

##### Paths

1. src/edge_tts/voices.py

##### Responsibility

Voice list retrieval and attribute matching

#### Item 4

##### Paths

1. src/edge_tts/submaker.py

2. src/edge_tts/srt_composer.py

3. src/edge_tts/util.py

##### Responsibility

Subtitle construction and CLI orchestration

#### Item 5

##### Paths

1. src/edge_playback

##### Responsibility

Playback integration, including Windows-specific behavior

#### Item 6

##### Paths

1. setup.cfg

2. setup.py

3. tests/001-long-text.sh

4. build_and_publish.sh

##### Responsibility

Package metadata, dependency ranges, existing live smoke test and combined build/publish entry point

### Contracts to preserve

1. Communicate.stream is single-use and yields the existing audio and boundary-metadata shapes.

2. Text chunks must remain valid UTF-8 and must not split XML entities; current whitespace stripping is deliberate and must be accounted for in reconstruction assertions.

3. Audio order and inter-chunk offset compensation must stay consistent across multiple text requests.

4. TLS verification, supplied proxy behavior and the existing HTTP 403 handling must remain enabled.

5. setup.cfg declares Python >=3.7; use syntax and standard-library APIs compatible with the supported interpreter range, or separately approve a support-range change.

6. The user-supplied connector belongs to the caller unless an explicit ownership contract states otherwise.

### Test inventory

The repository includes tests/001-long-text.sh, which launches 26 live CLI syntheses and compares subtitles. No automated unit-test suite was found. The handoff introduces no claim of existing pytest targets.

## Immediate mitigations

### Item 1

#### Finding

EDGE-1

#### Action

Reject or divide very long user input at safe natural boundaries before synthesis, keeping each original input short enough that XML escaping cannot exceed the intended chunk cap.

#### Limit

This caller-side workaround requires UTF-8/escaping-aware sizing and does not repair the library's no-whitespace fallback. Actual Microsoft request-size rejection was not exercised.

### Item 2

#### Finding

EDGE-2

#### Action

Use async for over Communicate.stream() with normal exception handling for applications that can use asyncio. If the sync API must be isolated before repair, enforce a timeout from a process boundary that can terminate the blocked worker.

#### Limit

A timeout on the same consumer thread does not unblock Queue.get(). This workaround does not add cancellation or exception propagation to stream_sync.

### Item 3

#### Finding

EDGE-3

#### Action

Omit the optional connector argument where a custom connector is unnecessary, allowing a new internally owned connector per chunk.

#### Limit

Applications requiring custom DNS, routing, TLS or connection policy still need the ownership fix. Do not weaken TLS or routing controls to adopt this workaround.

## Implementation sequence

### Item 1

#### Step

1

#### Action

Capture the three current defects using the preserved local probe and pin the reviewed commit.

#### Dependencies

None recorded.

#### Completion

Current defect evidence remains inspectable; replacement regression expectations are specified before source edits.

### Item 2

#### Step

2

#### Action

Repair bounded UTF-8 chunking and cover XML-entity/zero-progress edges.

#### Dependencies

1. 1

#### Findings

1. EDGE-1

### Item 3

#### Step

3

#### Action

Repair stream_sync terminal/error delivery and event-loop cleanup.

#### Dependencies

1. 1

#### Findings

1. EDGE-2

### Item 4

#### Step

4

#### Action

Repair custom-connector ownership across chunk and retry sessions.

#### Dependencies

1. 1

#### Findings

1. EDGE-3

### Item 5

#### Step

5

#### Action

Run combined offline regression scenarios, then the approved live-service and OS playback checks.

#### Dependencies

1. 2

2. 3

3. 4

#### Completion

All acceptance criteria pass and remaining service/OS limitations are explicitly recorded.

### Item 6

#### Step

6

#### Action

Build artifacts through a build-only invocation and inspect package metadata/content before any separately authorized publication.

#### Dependencies

1. 5

#### Completion

No call to the repository's combined build_and_publish.sh for validation, because it executes twine upload.

## Findings and implementation handoff

### EDGE-1 Medium: Unspaced text bypasses the 4096-byte chunk limit

**Status:** OPEN. [Pinned source](https://github.com/wwongw21/edge-tts/blob/4bdb8e4c6ea62f151a45a3fceb4cf6ff696bb89f/src/edge_tts/communicate.py#L233).

#### Evidence classification

Confirmed

#### Observed mechanism

When no whitespace appears within the first 4096 bytes, _find_safe_utf8_split_point receives all remaining text rather than text[:byte_length]. It returns the whole valid UTF-8 string. 10,000 unspaced ASCII bytes produce [10000]; 3,000 Han characters produce [9000].

#### Impact and scope

Long text without spaces is not bounded per request. CJK passages, long tokens and URLs can create arbitrarily large SSML/WebSocket requests instead of being segmented, exposing synthesis to service request-size limits.

#### Repair direction

Pass text[:byte_length] into the UTF-8 boundary finder, reject a zero split point when one complete character/entity cannot fit, and preserve all content across chunks.

#### Verification already obtained

Confirmed using actual split_text_by_byte_length through /workspace/review/edge_repros.py. Remote service rejection not exercised.

#### Confidence

Confirmed

#### Detailed handoff evidence

_find_safe_utf8_split_point(text) scans the entire remaining text rather than the first byte_length bytes. A valid 10,000-byte unspaced ASCII string yields [10000]; 3,000 Han characters yield [9000], although byte_length=4096.

#### User impact

Long CJK passages or unbroken tokens can become arbitrarily large SSML/WebSocket requests. The local evidence proves the cap violation; it does not prove how the remote service handles every oversized request.

#### Change surface

1. split_text_by_byte_length

2. _find_safe_utf8_split_point

3. _adjust_split_point_for_xml_entity

4. Focused regression tests to be added; no current unit-test target exists

#### Concrete repair steps

1. Give the UTF-8 fallback only the bounded prefix text[:byte_length], leaving the remainder for subsequent iterations.

2. After entity adjustment, detect the no-progress case explicitly. Raise the documented ValueError when a complete leading character/entity cannot fit rather than dropping one content byte.

3. Preserve whitespace preference and existing chunk stripping; avoid introducing Unicode decode/encode cycles that change original content.

4. Add direct chunk-property tests, then exercise Communicate.texts so the sanitation/escaping path is covered.

#### Invariants

1. Every nonempty emitted chunk has len(chunk) <= byte_length.

2. Each chunk independently decodes as UTF-8.

3. An XML entity is never split between chunks.

4. Non-whitespace content is neither lost nor duplicated; no-whitespace test inputs reconstruct byte-for-byte.

5. Every iteration consumes input or raises a documented error.

#### Acceptance criteria

1. 10,000 unspaced ASCII bytes are split into bounded chunks whose concatenation equals the input.

2. 3,000 Han characters split at valid UTF-8 boundaries, with each chunk <=4096 bytes and complete reconstruction.

3. Escaped ampersands and angle brackets around the limit produce valid entity boundaries.

4. A positive limit too small for the next complete character/entity raises ValueError promptly without data loss or an infinite loop.

5. Whitespace-rich baseline inputs continue using the established natural-boundary behavior.

#### Regression scenarios

1. Exactly 4096 ASCII bytes; 4097 bytes; a 4-byte Unicode character crossing a byte boundary.

2. CJK text with no spaces; a very long URL/token.

3. XML entities whose final semicolon lies just beyond the proposed split.

4. Leading/trailing/repeated whitespace and newline preference.

5. Empty text, bytes input, nonpositive byte_length and invalid input type.

#### Risks

1. Fixing the fallback increases the number of requests for previously unbounded text and can expose the separate connector-lifetime defect unless EDGE-3 is repaired.

2. Incorrect zero-progress handling can silently drop a character or the opening ampersand of an entity.

3. Chunk-boundary timing/subtitles need combined validation because more service turns are generated.

#### Rollback

Revert the isolated splitter repair if an unexpected regression occurs, restore caller-side long-input limits, and leave the defect recorded as OPEN. Reverting reintroduces oversized requests.

#### Detailed handoff validation

##### Recorded status

CONFIRMED

##### Probe

/workspace/review/edge_repros.py:check_split_limit

##### Recorded output

1. ASCII chunk byte lengths: [10000]

2. CJK chunk byte lengths: [9000]

##### Post repair status

NOT RUN

### EDGE-2 Medium: Synchronous streaming hangs after worker errors

**Status:** OPEN. [Pinned source](https://github.com/wwongw21/edge-tts/blob/4bdb8e4c6ea62f151a45a3fceb4cf6ff696bb89f/src/edge_tts/communicate.py#L626).

#### Evidence classification

Confirmed

#### Observed mechanism

The worker enqueues its sentinel only after a clean async iteration; executor.submit's Future is discarded. If stream raises, no sentinel or error enters the queue and the consumer blocks indefinitely on queue.get().

#### Impact and scope

A network/transport/synthesis exception makes the public stream_sync caller hang permanently instead of receiving the original failure.

#### Repair direction

Always communicate completion from a finally block, transfer the worker exception to the consumer, close the event loop in finally, and retain/check the Future.

#### Verification already obtained

Subclassed actual Communicate with an async stream that raises RuntimeError; next(stream_sync()) hung until a subprocess timeout killed it after 2 seconds. Reproduction saved in /workspace/review/edge_repros.py.

#### Confidence

Confirmed

#### Detailed handoff evidence

The worker puts its None sentinel only after successful async iteration. The submitted Future is discarded. A worker exception leaves the consumer blocked forever in queue.get(). A synthetic RuntimeError hung until a 2-second subprocess timeout killed it.

#### User impact

A transport or synthesis failure can permanently block the application's synchronous caller instead of surfacing the cause.

#### Change surface

1. Communicate.stream_sync

2. Worker event-loop lifetime

3. Queue terminal/error signaling

4. New focused offline sync-bridge regression tests

#### Concrete repair steps

1. Retain the Future returned by executor.submit.

2. Ensure the worker signals terminal completion from an outer finally block covering event-loop creation/execution as well as async iteration.

3. After receiving terminal completion, consume future.result() or an explicit queued error so the original failure is raised in the caller; do not translate failure into normal end-of-stream.

4. Close the worker event loop in finally on both success and failure.

5. If early consumer close/cancellation is changed, implement and verify that ownership separately; do not introduce a bounded-queue shutdown deadlock.

#### Invariants

1. Each producer exit wakes the consumer exactly once.

2. The caller receives the original worker exception after any already-yielded items.

3. Success preserves item order and clean iteration termination.

4. The event loop and executor thread are cleaned up after failure.

5. No queued audio or metadata is silently discarded by an ambiguous sentinel.

#### Acceptance criteria

1. A producer that raises before its first item causes next(stream_sync()) to raise the same failure promptly instead of timing out.

2. A producer that yields one item and then raises delivers that item followed by the failure.

3. A successful empty or multi-item producer terminates normally with order preserved.

4. Repeated failures do not accumulate worker threads or event-loop resource warnings.

5. Any intended behavior for an early-closed synchronous iterator is documented and locally checked.

#### Regression scenarios

1. Failure before first yield, between audio chunks, and after metadata.

2. RuntimeError and a representative aiohttp transport exception.

3. Successful empty and normal multi-item streams.

4. Repeated independent synchronous calls after previous failures.

5. Consumer closes the generator early; verify no new deadlock is introduced.

#### Risks

1. A sentinel-only repair can incorrectly turn an error into successful EOF unless the Future or error payload is inspected.

2. Handling only Exception in the wrong scope can still miss producer exit before iteration starts.

3. Introducing queue backpressure without coordinated cancellation can block executor shutdown.

#### Rollback

Revert this isolated sync bridge change if it alters success behavior unexpectedly. Prefer the async API or an external process timeout while the defect remains open; the previous sync behavior still hangs on errors.

#### Detailed handoff validation

##### Recorded status

CONFIRMED

##### Probe

/workspace/review/edge_repros.py:check_sync_failure

##### Recorded output

stream_sync transport failure: hung; child killed after 2 seconds

##### Post repair status

NOT RUN

### EDGE-3 Medium: Multi-chunk synthesis closes the caller-supplied connector

**Status:** OPEN. [Pinned source](https://github.com/wwongw21/edge-tts/blob/4bdb8e4c6ea62f151a45a3fceb4cf6ff696bb89f/src/edge_tts/communicate.py#L460).

#### Evidence classification

Confirmed

#### Observed mechanism

Every text chunk creates ClientSession(connector=self.connector) with aiohttp's default connector_owner=True. Exiting the first chunk session closes the caller-supplied connector. The next chunk reuses that closed connector.

#### Impact and scope

Any multi-chunk synthesis using the supported custom connector argument fails on its second chunk with RuntimeError('Session is closed'); a 403 retry can also reuse the closed connector.

#### Repair direction

Use one session for the complete stream/retry lifetime, or set connector_owner=False for supplied connectors while retaining ownership for internally created connectors.

#### Verification already obtained

Actual Communicate.stream against a loopback aiohttp WebSocket fixture delivered the first chunk, closed the custom TCPConnector, then raised 'Session is closed' before opening a second request. Saved in /workspace/review/edge_repros.py.

#### Confidence

Confirmed

#### Detailed handoff evidence

Every __stream invocation constructs aiohttp.ClientSession(connector=self.connector) with default connector_owner=True. The local fixture completes the first chunk and closes the supplied TCPConnector; the second chunk fails with RuntimeError('Session is closed') before a second WebSocket request opens.

#### User impact

Multi-chunk synthesis with the supported connector parameter fails after the first chunk. A retry can encounter the same closed-connector problem.

#### Change surface

1. Communicate.__stream ClientSession construction

2. Communicate.stream chunk/retry lifetime if choosing one shared session

3. Caller-supplied connector ownership contract

#### Concrete repair steps

1. Choose one minimal ownership model: retain per-chunk sessions and set connector_owner=False only for a caller-supplied connector, or move a single session around the whole stream/retry lifetime.

2. Keep internally created connectors owned and closed by their session.

3. Document that the caller closes a supplied connector when all dependent work is finished.

4. Preserve timeout, trust_env, proxy, TLS context and HTTP 403 retry behavior.

5. Exercise both the multi-chunk path and retry behavior with a local fixture.

#### Preferred approach

Set connector_owner=False when self.connector is supplied; this is the smaller repair surface. A whole-stream session is a viable alternative if its cleanup, retry and timing effects are tested.

#### Invariants

1. Caller-supplied connectors remain open after the stream unless the caller closes them.

2. Internally allocated connectors are released on clean completion and exceptions.

3. Every required chunk can establish its WebSocket exchange.

4. A supported 403 retry can reuse the intended connector.

5. Session ownership changes do not disable TLS or bypass proxy policy.

#### Acceptance criteria

1. The existing 5,000-character whitespace-rich input against the local fixture opens at least two requests and completes without 'Session is closed'.

2. A supplied connector remains open until the test caller closes it.

3. The default connector path succeeds across multiple chunks without resource warnings.

4. A forced supported 403 retry does not fail due to premature connector closure.

5. Exception and cancellation paths leave no internally owned connector/session leak.

#### Regression scenarios

1. Single versus multiple chunks, with and without a supplied connector.

2. Two independent Communicate objects sharing one caller-owned connector.

3. First-attempt 403 followed by success; unretryable HTTP failure.

4. Failure after first audio bytes and early iteration termination.

5. Custom connector plus explicit proxy/timeout settings.

#### Risks

1. Setting connector_owner=False unconditionally would leak internally created connectors.

2. Changing the whole-stream session scope can alter connection pooling and error cleanup.

3. Applications that accidentally relied on the library closing a supplied connector need the ownership behavior documented.

#### Rollback

Revert the isolated ownership repair if necessary and omit custom connectors where their policy is unnecessary. Reverting preserves the known failure for long input with supplied connectors.

#### Detailed handoff validation

##### Recorded status

CONFIRMED

##### Probe

/workspace/review/edge_repros.py:check_connector_lifetime

##### Recorded output

custom connector: second chunk failed with 'Session is closed' ; connector closed after 1 request

##### Post repair status

NOT RUN

## Source directory

/workspace/repos/edge-tts

## Snapshot

### Branch

master

### Commit

4bdb8e4c6ea62f151a45a3fceb4cf6ff696bb89f

### Review date

2026-10-22

## Checks and reproductions

### Item 1

#### Name

Offline defect probe

#### Command

```
PYTHONDONTWRITEBYTECODE=1 python /workspace/review/edge_repros.py
```

#### Working directory

Any; script uses the absolute checkout path

#### Prerequisites

1. Python 3.12.14 was used

2. aiohttp, certifi and typing_extensions available

3. Reviewed source at /workspace/repos/edge-tts

4. Loopback sockets allowed

#### Side effects

Starts a temporary local WebSocket server and a child process killed after a controlled timeout; no external service traffic or project source edits.

#### Recorded result

PASS as a defect-confirmation script: all three currently broken behaviors were observed.

#### Recorded output

1. ASCII chunk byte lengths: [10000]

2. CJK chunk byte lengths: [9000]

3. stream_sync transport failure: hung; child killed after 2 seconds

4. custom connector: second chunk failed with 'Session is closed' ; connector closed after 1 request

#### Interpretation

The script asserts current defects. After repair, replace or invert these assertions in new regression tests; an unchanged script failing after repair is expected and is not proof of regression.

### Item 2

#### Name

Python syntax

#### Command

```
python - <<'PY'
from pathlib import Path
ps = list(Path('/workspace/repos/edge-tts').rglob('*.py'))
for p in ps:
    compile(p.read_bytes(), str(p), 'exec')
print(len(ps), 'Python files compile')
PY
```

#### Prerequisites

1. Python 3.12.14 was used

#### Recorded result

PASS: all 24 Python source files compiled.

#### Interpretation

Syntax only; does not exercise Microsoft service or OS playback.

### Item 3

#### Name

Existing live long-text shell smoke test

#### Command

```
bash tests/001-long-text.sh
```

#### Working directory

/workspace/repos/edge-tts

#### Prerequisites

1. Installed edge-tts CLI matching the candidate source

2. Microsoft online service reachable

3. Disk space for 26 MP3 and SRT outputs

4. Bash and cmp

#### Recorded result

NOT RUN

#### Interpretation

Existing test launches 26 concurrent syntheses and compares subtitle files. It is not a replacement for the new offline edge-case regressions.

### Item 4

#### Name

Build-only package validation

#### Command

```
python setup.py sdist bdist_wheel
python -m twine check dist/*
```

#### Working directory

A disposable candidate checkout

#### Prerequisites

1. setuptools, wheel and twine installed

2. Candidate dependencies and supported interpreter available

#### Recorded result

NOT RUN

#### Interpretation

Proposed commands extracted from the repository build/publish script without its upload step. Do not execute build_and_publish.sh for validation: it removes build artifacts and uploads through twine.

## Remaining acceptance

### Item 1

#### Status

NOT RUN

#### Area

Microsoft live service

#### Criteria

Synthesize representative long CJK, long unbroken ASCII, entity-heavy and ordinary prose after repairs; verify audio completeness and subtitle ordering across chunk boundaries.

### Item 2

#### Status

NOT RUN

#### Area

HTTP 403 service compatibility

#### Criteria

Check real service clock-skew recovery if reproducibly available; local retry fixtures should still be mandatory.

### Item 3

#### Status

NOT RUN

#### Area

Windows playback

#### Criteria

Verify edge-playback on a supported Windows installation, including cancellation/error behavior and no orphan playback resources.

### Item 4

#### Status

NOT RUN

#### Area

Other platform playback

#### Criteria

Verify the documented mpv integration on a supported non-Windows platform if included in the release claim.

### Item 5

#### Status

NOT RUN

#### Area

Interpreter compatibility

#### Criteria

Run candidate regression coverage on the declared minimum supported Python and a current supported interpreter; the recorded syntax run used Python 3.12 only.

## Evidence files

1. /workspace/review/edge_nvda.json

2. /workspace/review/edge_repros.py

## Handoff completion

All findings remain OPEN. A future implementer must make code changes and record post-repair results; no fix has been represented as complete.

## Delivery and diagnostic files

### Destination repository

wwongw21/edge-tts

### Status

All findings remain OPEN; documentation transfer only.

### Portable evidence

This JSON retains the complete repository-specific handoff. Historical /workspace paths are evidence references and prerequisites, not portable hyperlinks. Applicable repository-specific diagnostics are included alongside the report.

### Diagnostics included

1. edge_repros.py

### Not included

Original environments, model weights, user data and complete source snapshots are not included. Clone the pinned source and follow the recorded prerequisites. Bug-demonstration scripts intentionally assert pre-fix behavior; they are not a post-fix passing suite.

- [edge_repros.py](diagnostics/edge_repros.py)

## Remaining review limits

1. No live Microsoft service synthesis or audio playback exercised.

2. No Windows playback test.

3. Only existing test artifact is a live long-text shell smoke test; no unit test suite found.

