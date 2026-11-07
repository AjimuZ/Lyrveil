// A reply end to end in the Compose test project (P02.6c): a message sent through the backend API gets a lease from the
// orchestrator, which drives its fakes, and the reply streams from the fake KoboldCpp over /ws and is stored.
//
// The REST calls and the WebSocket run inside the page, so the browser sends the app's Origin, which the backend
// requires for writes and handshakes. The lease is read from the orchestrator's event log with "docker compose exec",
// because the control API is on an internal network that the host cannot reach.
import { execFileSync } from 'node:child_process';
import { expect, test } from '@playwright/test';

// The project name that scripts/lib/EndToEnd.psm1 passes, and the token compose.test.yaml gives both services.
const project = 'lyrveil-test';
const controlToken = '0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef';
const run = Date.now().toString(36);

/** Returns the orchestrator's buffered lifecycle events, read from inside its container. */
function orchestratorEvents() {
  const script =
    "import sys, urllib.request as u; r = u.Request('http://127.0.0.1:8100/v1/events', " +
    "headers={'Authorization': 'Bearer ' + sys.argv[1]}); print(u.urlopen(r, timeout=5).read().decode())";
  const output = execFileSync(
    'docker',
    ['compose', '-p', project, 'exec', '-T', 'lyrveil-orchestrator', 'python', '-c', script, controlToken],
    { encoding: 'utf8' },
  );
  return JSON.parse(output).events;
}

test('a sent message gets a lease, a reply streamed over /ws from the fake KoboldCpp, and the stored reply', async ({ page }) => {
  // Any page of the app gives the evaluated code the app's origin.
  await page.goto('/chats');
  await expect(page.getByRole('heading', { level: 1, name: 'Conversazioni' })).toBeVisible();

  const result = await page.evaluate(async (firstName) => {
    async function call(method, path, body) {
      const response = await fetch(`/api/v1${path}`, {
        method,
        headers: body === undefined ? {} : { 'Content-Type': 'application/json' },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
      if (!response.ok) {
        throw new Error(`${method} ${path} answered ${response.status}`);
      }
      return response.json();
    }

    const character = await call('POST', '/characters', { first_name: firstName, age: 30 });
    const conversation = await call('POST', '/conversations', { character_id: character.id });

    // The events published after the connection opened arrive once the first hello is answered, so the message is
    // sent only after the hello.
    const socket = new WebSocket(`ws://${location.host}/ws`);
    const frames = [];
    const ready = new Promise((resolve, reject) => {
      socket.addEventListener('error', () => reject(new Error('the WebSocket failed')));
      socket.addEventListener('message', (event) => {
        const frame = JSON.parse(event.data);
        frames.push(frame);
        if (frame.type === 'connected') {
          socket.send(JSON.stringify({ type: 'hello', last_event_id: null, boot_id: frame.boot_id }));
          resolve();
        }
      });
    });
    await ready;

    const sent = await call('POST', `/conversations/${conversation.id}/messages`, {
      client_message_id: `reply-stream-${firstName}`,
      content: 'Ciao! Come stai?',
    });
    const replyId = sent.reply.id;
    const completed = await new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        reject(new Error(`no message.completed within 60 s; frames: ${frames.map((frame) => frame.type).join(', ')}`));
      }, 60_000);
      const check = () => {
        const failed = frames.find((frame) => frame.type === 'message.failed' && frame.data.message_id === replyId);
        if (failed) {
          clearTimeout(timer);
          reject(new Error(`the reply failed with ${failed.data.error_code}`));
        }
        const done = frames.find((frame) => frame.type === 'message.completed' && frame.data.message.id === replyId);
        if (done) {
          clearTimeout(timer);
          resolve(done.data.message);
        }
      };
      check();
      socket.addEventListener('message', check);
    });
    socket.close();

    const deltas = frames
      .filter((frame) => frame.type === 'message.delta' && frame.data.message_id === replyId)
      .map((frame) => frame.data);
    const history = await call('GET', `/conversations/${conversation.id}/messages`);
    return {
      jobId: sent.job.id,
      deltas,
      completed,
      stored: history.items.find((message) => message.id === replyId),
      typing: frames
        .filter((frame) => frame.type.startsWith('typing.') && frame.data.conversation_id === conversation.id)
        .map((frame) => frame.type),
    };
  }, `Eco${run}`);

  // The reply streamed in pieces, each at the offset where the text before it ends.
  expect(result.deltas.length).toBeGreaterThan(1);
  let offset = 0;
  for (const delta of result.deltas) {
    expect(delta.offset).toBe(offset);
    offset += delta.text.length;
  }
  const streamed = result.deltas.map((delta) => delta.text).join('');
  expect(streamed).toBe('Ciao, sono qui.');
  expect(result.typing).toEqual(['typing.started', 'typing.stopped']);

  // The completed event and the stored history carry the streamed text.
  expect(result.completed.content).toBe(streamed);
  expect(result.stored).toMatchObject({ author: 'character', status: 'complete', content: streamed });

  // The orchestrator granted the reply job a lease and ended it with the outcome ok.
  const events = orchestratorEvents().filter((event) => event.job_id === result.jobId);
  expect(events.map((event) => [event.from, event.to, event.reason])).toEqual([
    ['LLM_READY', 'GENERATING_TEXT', 'lease granted'],
    ['GENERATING_TEXT', 'LLM_READY', 'lease released'],
  ]);
});
