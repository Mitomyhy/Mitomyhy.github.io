import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp, writeFile, mkdir} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {validateJob} from '../render.mjs';

// Real tiny PNG bytes, not an executable or network asset.
const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=', 'base64');

test('job assets are converted to data images inside the owned directory', async () => {
  const root = await mkdtemp(join(tmpdir(), 'mitom-scene-'));
  await writeFile(join(root, 'product.png'), png);
  const request = join(root, 'scene.json');
  await writeFile(request, JSON.stringify({schemaVersion: 1, product: {src: 'product.png'}}));
  const result = await validateJob(request, join(root, 'video.mp4'));
  assert.match(result.scene.product.src, /^data:image\/png;base64,/);
  assert.equal(result.scene.product.sourceAspectRatio, 1);
  assert.equal(result.scene.product.anchor, 'hanging');
  await writeFile(join(root, 'video.mp4'), 'existing');
  await assert.rejects(validateJob(request, join(root, 'video.mp4')), /already exists/);
});
test('an adjacent image and an output outside the job cannot be accessed', async () => {
  const root = await mkdtemp(join(tmpdir(), 'mitom-scene-'));
  const job = join(root, 'job'); await mkdir(job);
  await writeFile(join(root, 'private.png'), png);
  const request = join(job, 'scene.json');
  await writeFile(request, JSON.stringify({schemaVersion: 1, product: {src: '../private.png'}}));
  await assert.rejects(validateJob(request, join(job, 'video.mp4')), /inside the job/);
  await writeFile(join(job, 'product.png'), png);
  await writeFile(request, JSON.stringify({schemaVersion: 1, product: {src: 'product.png'}}));
  await assert.rejects(validateJob(request, join(root, 'escape.mp4')), /new local job/);
  await writeFile(request, JSON.stringify({schemaVersion: 1, product: {src: 'product.png'}, background: {videoSrc: '../private.png'}}));
  await assert.rejects(validateJob(request, join(job, 'video.mp4')), /inside the job/);
});
