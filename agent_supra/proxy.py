"""Loopback-only Chat Completions proxy. Prompts and completions are never journaled."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import json
import math
import os
import re
import sqlite3
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, asdict
from pathlib import Path

from . import pricing
from .claude_hook import http_client, validate_url
from .client import SupervisorAPIError, SupervisorUnavailable
from .sdk import fingerprint
from .watch import Journal, positive

BASES = {'openai': 'https://api.openai.com/v1', 'gemini': 'https://generativelanguage.googleapis.com/v1beta/openai'}
MAX_BODY, MAX_RESPONSE, MAX_EVENT = 2_000_000, 16_000_000, 1_000_000
FIELDS = {'model', 'messages', 'stream', 'stream_options', 'max_tokens', 'max_completion_tokens',
          'temperature', 'top_p', 'frequency_penalty', 'presence_penalty', 'seed', 'stop', 'n',
          'tools', 'tool_choice', 'parallel_tool_calls', 'response_format', 'user', 'metadata',
          'logit_bias', 'logprobs', 'top_logprobs', 'reasoning_effort', 'verbosity', 'store', 'service_tier'}


@dataclass
class Config:
    model: str
    price: pricing.Price
    provider: str = 'openai'
    budget_usd: float = 1.0
    max_calls: int = 100
    max_output_tokens: int = 1024
    timeout: float = 90
    upstream_base: str | None = None

    def __post_init__(self):
        if self.provider not in BASES:
            raise ValueError('unsupported provider profile')
        self.upstream_base = validate_url(self.upstream_base or BASES[self.provider])
        if not re.fullmatch(r'[A-Za-z0-9._:/@-]{1,120}', self.model):
            raise ValueError('invalid model')
        rates = [self.price.input_usd, self.price.output_usd, self.price.long_input_usd, self.price.long_output_usd]
        if any(rate is not None and (not math.isfinite(rate) or not 0 <= rate <= 1_000_000) for rate in rates):
            raise ValueError('invalid token prices')
        if self.price.long_threshold is not None and (type(self.price.long_threshold) is not int or self.price.long_threshold <= 0):
            raise ValueError('invalid long-context threshold')
        if not math.isfinite(self.budget_usd) or pricing.to_micros(self.budget_usd) < 1:
            raise ValueError('budget must be positive')
        if not 1 <= self.max_calls <= 100_000 or not 1 <= self.max_output_tokens <= 32_000 or not 1 <= self.timeout <= 600:
            raise ValueError('invalid proxy limits')


def usage_from(data):
    usage = data.get('usage') if isinstance(data, dict) else None
    if not isinstance(usage, dict):
        return None
    values = [usage.get('prompt_tokens'), usage.get('completion_tokens')]
    if not all(type(value) is int and 0 <= value <= 1_000_000_000 for value in values):
        return None
    return tuple(values)


class SSEUsage:
    """Bounded, incremental SSE decoder; it never keeps completion text."""
    def __init__(self):
        self.buffer = bytearray()
        self.data = []
        self.event_size = 0
        self.usage = None
        self.done = False
        self.total = 0
        self.wire = bytearray()

    def feed(self, chunk):
        completed = []
        self.total += len(chunk)
        if self.total > MAX_RESPONSE:
            raise ValueError('stream limit')
        self.buffer.extend(chunk)
        while b'\n' in self.buffer:
            line, _, tail = self.buffer.partition(b'\n')
            self.buffer = bytearray(tail)
            self.wire.extend(line + b'\n')
            self.event_size += len(line) + 1
            line = bytes(line).rstrip(b'\r')
            if self.event_size > MAX_EVENT:
                raise ValueError('event limit')
            if not line:
                payload = b'\n'.join(self.data)
                self.data, self.event_size = [], 0
                usage_only = False
                if payload == b'[DONE]':
                    self.done = True
                elif payload:
                    item = json.loads(payload)
                    value = usage_from(item)
                    if value is not None:
                        self.usage = value  # Usage totals, never summed across chunks.
                        usage_only = item.get('choices') == []
                completed.append((bytes(self.wire), usage_only))
                self.wire.clear()
            elif line.startswith(b'data:'):
                self.data.append(line[5:].lstrip(b' '))
        if len(self.buffer) > MAX_EVENT:
            raise ValueError('line limit')
        return completed


def prepare(body, config):
    if not isinstance(body, dict) or set(body) - FIELDS or body.get('model') != config.model:
        raise ValueError('unsupported fields or model')
    if type(body.get('stream', False)) is not bool or type(body.get('n', 1)) is not int or body.get('n', 1) != 1:
        raise ValueError('unsupported stream or n')
    if body.get('service_tier', 'default') != 'default' or body.get('store', False) is not False:
        raise ValueError('unsupported service tier or storage')
    messages = body.get('messages')
    if not isinstance(messages, list) or not messages:
        raise ValueError('messages required')
    for message in messages:
        if not isinstance(message, dict) or set(message) - {'role', 'content', 'name', 'tool_calls', 'tool_call_id', 'function_call', 'refusal'}:
            raise ValueError('unsupported message')
        content = message.get('content')
        if content is not None and not isinstance(content, str):
            if not isinstance(content, list) or any(not isinstance(p, dict) or p.get('type') != 'text' or not isinstance(p.get('text'), str) for p in content):
                raise ValueError('text only')
    if any(not isinstance(tool, dict) or tool.get('type') != 'function' for tool in body.get('tools', [])):
        raise ValueError('function tools only')
    if 'max_tokens' in body and 'max_completion_tokens' in body:
        raise ValueError('one output limit only')
    limit = body.get('max_completion_tokens', body.get('max_tokens', config.max_output_tokens))
    if type(limit) is not int or not 1 <= limit <= config.max_output_tokens:
        raise ValueError('output limit exceeds proxy policy')
    result = dict(body)
    result.pop('max_tokens', None)
    result.pop('max_completion_tokens', None)
    result['max_tokens' if config.provider == 'gemini' else 'max_completion_tokens'] = limit
    if config.provider == 'openai':
        result['store'] = False
    if result.get('stream'):
        options = result.get('stream_options') or {}
        if not isinstance(options, dict) or set(options) - {'include_usage', 'include_obfuscation'}:
            raise ValueError('unsupported stream options')
        if any(type(value) is not bool for value in options.values()):
            raise ValueError('stream options must be booleans')
        result['stream_options'] = dict(options, include_usage=True)
    return result


class Ledger(Journal):
    def __init__(self, path, scope, config, *, replay=False):
        super().__init__(path, scope, name='Provider proxy / ' + config.model, source='provider-proxy', replay=replay, check_same_thread=False)
        self.config = config
        if replay and self.get('proxy_config') != asdict(config):
            self.close()
            raise ValueError('Replay configuration mismatch')
        if not replay:
            with self.conn:
                self.set('proxy_config', asdict(config))
        self.conn.execute('CREATE TABLE IF NOT EXISTS requests (id TEXT PRIMARY KEY, digest TEXT NOT NULL, state TEXT NOT NULL)')
        self.conn.commit()
        if replay:
            for row in self.conn.execute('SELECT id FROM requests WHERE state="dispatched"').fetchall():
                self.record(row['id'], None)  # Crash recovery counts uncertainty, never re-executes.

    def sync(self, client, *, control=True):
        if not self.get('task'):
            task = client._call('POST', '/v1/tasks', {'objective': self.get('name'), 'budget_usd': self.config.budget_usd,
                'max_calls': self.config.max_calls, 'policy': {'observation_mode': True}})['task_id']
            with self.conn:
                self.set('task', task)
        decision = super().sync(client, control=control)
        if self.get('uncertain') and decision['state'] == 'running':
            decision = client.pause(self.get('run'), 'Proxy usage is unknown; reconcile provider usage before starting another proxy run.')
        if control and decision['allowed']:
            decision = client._call('GET', f'/v1/runs/{self.get("run")}/decision', params={'before_model_call': 'true'})
        return decision

    def dispatch(self, identity, digest):
        with self.conn:
            self.conn.execute('INSERT INTO requests VALUES (?,?,?)', (identity, digest, 'dispatched'))

    def record(self, identity, usage):
        row = self.conn.execute('SELECT state FROM requests WHERE id=?', (identity,)).fetchone()
        if not row or row['state'] != 'dispatched':
            return
        inp, out = usage or (0, 0)
        cost = pricing.estimate_cost_micros(self.config.model, inp, out, {pricing.normalize(self.config.model): self.config.price}) if usage is not None else None
        data = {'model': self.config.model, 'input_tokens': inp, 'output_tokens': out,
                'cost_micros': cost, 'usage_missing': usage is None, 'source': 'provider-proxy',
                'cost_estimate_source': 'proxy_config', 'proxy_price': asdict(self.config.price)}
        with self.conn:
            self._append('model.call', data)
            self.set('calls', self.get('calls', 0) + 1)
            self.set('spend', self.get('spend', 0) + (cost or 0))
            if usage is None:
                self.set('uncertain', True)
            self.conn.execute('UPDATE requests SET state=? WHERE id=?', ('recorded', identity))


def create_app(config, ledger, supervisor, provider_key, local_token, *, transport=None):
    import anyio
    import httpx
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse, Response, StreamingResponse

    # FastAPI resolves annotated Request in module globals with postponed annotations.
    globals()['Request'] = Request
    lock = asyncio.Lock()
    upstream = httpx.AsyncClient(transport=transport, follow_redirects=False, trust_env=False,
        timeout=httpx.Timeout(config.timeout), headers={'Authorization': 'Bearer ' + provider_key})
    supervisor.cooldown = 0
    salt = bytes.fromhex(ledger.get('salt'))

    async def sync(control=True):
        return await anyio.to_thread.run_sync(lambda: ledger.sync(supervisor, control=control))

    async def cleanup(identity, usage):
        # Persist synchronously before an await: client cancellation must not lose billing metadata.
        ledger.record(identity, usage)
        with anyio.CancelScope(shield=True):
            try:
                await sync(False)
            except (SupervisorUnavailable, SupervisorAPIError):
                pass  # Durable sanitized metadata is retried before another dispatch.

    @asynccontextmanager
    async def lifespan(app):
        try:
            yield
        finally:
            await upstream.aclose()
            # A dispatched record without a terminal usage event is conservatively unknown.
            for row in ledger.conn.execute('SELECT id FROM requests WHERE state="dispatched"').fetchall():
                ledger.record(row['id'], None)
            ledger.finish(0)
            try:
                for _ in range(3):
                    await sync(False)
                    if not ledger.pending():
                        break
            except (SupervisorUnavailable, SupervisorAPIError):
                pass

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.router.redirect_slashes = False

    def error(status, code):
        return JSONResponse({'error': {'message': 'Agent Supra: ' + code.replace('_', ' '), 'type': code, 'code': code}}, status_code=status)

    @app.get('/healthz')
    async def health():
        return {'ok': True}

    @app.post('/v1/chat/completions')
    async def completion(request: Request):
        if request.headers.get('origin') or request.url.hostname not in {'127.0.0.1', 'localhost', '::1'}:
            return error(403, 'local_clients_only')
        if not hmac.compare_digest(request.headers.get('authorization', '').encode(), ('Bearer ' + local_token).encode()):
            return error(401, 'proxy_authentication_required')
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > MAX_BODY:
                return error(413, 'request_too_large')
        try:
            original = json.loads(raw)
            payload = prepare(original, config)
            wants_usage = (original.get('stream_options') or {}).get('include_usage') is True
        except (ValueError, TypeError, RecursionError):
            return error(400, 'unsupported_request')
        client_id = request.headers.get('idempotency-key')
        if client_id and not re.fullmatch(r'[A-Za-z0-9._:-]{1,128}', client_id):
            return error(400, 'invalid_idempotency_key')
        identity = fingerprint(client_id or uuid.uuid4().hex, salt)
        digest = fingerprint(json.dumps(payload, sort_keys=True, separators=(',', ':')), salt)
        await lock.acquire()
        response, dispatched, transferred = None, False, False
        observed = None
        try:
            if ledger.conn.execute('SELECT 1 FROM requests WHERE id=?', (identity,)).fetchone():
                return error(409, 'request_already_dispatched')
            if ledger.get('uncertain') or ledger.conn.execute('SELECT 1 FROM requests WHERE state="dispatched"').fetchone():
                try:
                    await sync(False)
                except (SupervisorUnavailable, SupervisorAPIError):
                    pass
                return error(503, 'usage_unknown_review_required')
            try:
                decision = await sync()
            except (SupervisorUnavailable, SupervisorAPIError):
                return error(503, 'supervision_unavailable')
            if ledger.pending():
                return error(503, 'telemetry_pending')
            if not decision['allowed']:
                return error(403, 'supervisor_blocked')
            if ledger.get('calls', 0) >= config.max_calls or ledger.get('spend', 0) >= pricing.to_micros(config.budget_usd):
                return error(429, 'proxy_limit_reached')
            ledger.dispatch(identity, digest)
            dispatched = True
            try:
                response = await upstream.send(upstream.build_request('POST', config.upstream_base + '/chat/completions', json=payload), stream=True)
            except httpx.HTTPError:
                return error(502, 'upstream_unavailable')
            if response.status_code != 200:
                return error(429 if response.status_code == 429 else 502, 'upstream_rejected')
            if payload.get('stream'):
                if 'text/event-stream' not in response.headers.get('content-type', ''):
                    return error(502, 'invalid_upstream_stream')
                parser = SSEUsage()

                async def chunks():
                    try:
                        async for chunk in response.aiter_bytes():
                            for event, usage_only in parser.feed(chunk):
                                if wants_usage or not usage_only:
                                    yield event
                        if not parser.done:
                            yield b'data: {"error":{"message":"Upstream stream ended without completion","type":"supra_upstream_error"}}\n\n'
                    except (httpx.HTTPError, ValueError):
                        yield b'data: {"error":{"message":"Upstream stream interrupted","type":"supra_upstream_error"}}\n\n'

                class ManagedStream(StreamingResponse):
                    async def __call__(self, scope, receive, send):
                        try:
                            await super().__call__(scope, receive, send)
                        finally:
                            try:
                                await cleanup(identity, parser.usage if parser.done else None)
                            finally:
                                with anyio.CancelScope(shield=True):
                                    try:
                                        await response.aclose()
                                    finally:
                                        lock.release()

                transferred = True
                return ManagedStream(chunks(), media_type='text/event-stream', headers={'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no'})
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > MAX_RESPONSE:
                    return error(502, 'upstream_response_too_large')
            try:
                result = json.loads(body)
                observed = usage_from(result)
            except ValueError:
                return error(502, 'invalid_upstream_response')
            if not isinstance(result, dict) or not isinstance(result.get('choices'), list):
                return error(502, 'invalid_upstream_response')
            return Response(bytes(body), media_type='application/json', headers={'Cache-Control': 'no-store'})
        except httpx.HTTPError:
            return error(502, 'upstream_unavailable')
        finally:
            if not transferred:
                try:
                    if dispatched:
                        await cleanup(identity, observed)
                finally:
                    try:
                        if response:
                            with anyio.CancelScope(shield=True):
                                await response.aclose()
                    finally:
                        lock.release()

    return app


def main(argv=None):
    parser = argparse.ArgumentParser(prog='agent-supra proxy', description=__doc__)
    parser.add_argument('--provider', choices=list(BASES), default='openai')
    parser.add_argument('--model')
    parser.add_argument('--budget-usd', type=positive, default=1)
    parser.add_argument('--max-calls', type=int, default=100)
    parser.add_argument('--max-output-tokens', type=int, default=1024)
    parser.add_argument('--input-rate', type=float, help='USD per million input tokens; provide both rates for unlisted models')
    parser.add_argument('--output-rate', type=float)
    parser.add_argument('--port', type=int, default=8790)
    parser.add_argument('--state-dir', type=Path, default=Path.home() / '.agent-supra' / 'proxy')
    parser.add_argument('--replay', type=Path, help='replay only; never contacts the provider')
    args = parser.parse_args(argv)
    ledger = None
    try:
        if not 1 <= args.port <= 65535 or (args.input_rate is None) != (args.output_rate is None):
            raise ValueError('invalid limits')
        if args.replay:
            with sqlite3.connect(f'file:{(args.replay.resolve() / "journal.sqlite3").as_posix()}?mode=ro', uri=True) as connection:
                saved = json.loads(connection.execute('SELECT value FROM meta WHERE key="proxy_config"').fetchone()[0])
            saved['price'] = pricing.Price(**saved['price'])
            config = Config(**saved)
        else:
            if not args.model:
                parser.error('--model is required unless replaying')
            price = pricing.Price(args.input_rate, args.output_rate) if args.input_rate is not None else pricing.lookup(args.model)
            if price is None:
                raise ValueError('explicit prices required for this model')
            config = Config(args.model, price, provider=args.provider, budget_usd=args.budget_usd, max_calls=args.max_calls, max_output_tokens=args.max_output_tokens)
        key = os.environ.get('AGENT_SUPRA_API_KEY')
        if not key:
            raise ValueError('supervisor key missing')
        url = validate_url(os.environ.get('AGENT_SUPRA_URL', 'https://agentsupra.in'))
        scope = hashlib.sha256((url + '\0' + key + '\0' + config.model + '\0' + config.upstream_base).encode()).hexdigest()
        if args.replay:
            from .watch import replay
            ledger = Ledger(args.replay, scope, config, replay=True)
            return replay(ledger, http_client(key, url))
        provider_key = os.environ.get('GEMINI_API_KEY' if args.provider == 'gemini' else 'OPENAI_API_KEY')
        token = os.environ.get('AGENT_SUPRA_PROXY_TOKEN')
        if not provider_key or not token or len(token) < 24 or token in {provider_key, key}:
            raise ValueError('provider key and a 24+ character proxy token are required')
        ledger = Ledger(args.state_dir / uuid.uuid4().hex, scope, config)
        app = create_app(config, ledger, http_client(key, url), provider_key, token)
        import uvicorn
        print(f'Proxy: http://127.0.0.1:{args.port}/v1 | private journal: {ledger.path}', flush=True)
        print('Estimated-token budget; one in-flight request can overshoot. No automatic provider retries.', flush=True)
        uvicorn.run(app, host='127.0.0.1', port=args.port, workers=1, access_log=False, log_level='warning')
        if ledger.pending():
            print('Metadata pending. Replay with: agent-supra proxy --replay', ledger.path, flush=True)
        return 0
    except ImportError:
        print('Install the proxy extra: pip install -e ".[proxy]"', file=__import__('sys').stderr)
        return 78
    except (ValueError, OSError, sqlite3.Error, TypeError):
        print('Proxy setup failed. Check environment keys, explicit model prices, limits and private directory.', file=__import__('sys').stderr)
        return 78
    finally:
        if ledger:
            ledger.close()


if __name__ == '__main__':
    raise SystemExit(main())
