import test from 'node:test';
import assert from 'node:assert/strict';
import {normalizeScene, sceneDimensions, motionAtFrame, seeded} from '../src/scene.mjs';

const scene = (change = {}) => normalizeScene({schemaVersion: 1, product: {src: 'product.png'}, ...change});

test('both listing aspect ratios have exact even HD dimensions', () => {
  assert.deepEqual(sceneDimensions(scene()), {width: 1920, height: 1080});
  assert.deepEqual(sceneDimensions(scene({aspectRatio: '9:16', resolution: 720})), {width: 720, height: 1280});
});
test('motion actually turns both ways while respecting rotation bounds', () => {
  const s = scene({durationSeconds: 8, motion: {orbitDegrees: 52}});
  const samples = Array.from({length: 240}, (_, f) => motionAtFrame(f, s));
  assert.ok(Math.max(...samples.map(m => m.yaw)) > 51);
  assert.ok(Math.min(...samples.map(m => m.yaw)) < -51);
  assert.ok(samples.every(m => Math.abs(m.yaw) <= 52 && Math.abs(m.roll) <= 3 && Math.abs(m.pitch) <= 2.5));
  assert.ok(new Set(samples.map(m => m.x)).size > 100);
});
test('left/right directions mirror yaw; fixed seed and frames are repeatable', () => {
  assert.equal(motionAtFrame(40, scene({motion: {direction: 'left'}})).yaw, -motionAtFrame(40, scene({motion: {direction: 'right'}})).yaw);
  assert.deepEqual(motionAtFrame(40, scene()), motionAtFrame(40, scene()));
  const a = seeded(42), b = seeded(42);
  assert.deepEqual(Array.from({length: 20}, a), Array.from({length: 20}, b));
});
test('arbitrary remote media, code and invalid numerical controls are rejected', () => {
  for (const src of ['https://example.com/a.png', 'file:///a.png', 'data:image/svg+xml,x', '\\\\server\\share\\x.png']) assert.throws(() => scene({product: {src}}));
  for (const motion of [{orbitDegrees: 56}, {swayDegrees: -1}, {zoom: NaN}, {cycles: Infinity}, {code: 'alert(1)'}]) assert.throws(() => scene({motion}));
  assert.throws(() => scene({extraCode: 'process.exit()'}));
  assert.throws(() => scene({background: {palette: ['red', '#000000', '#ffffff']}}));
});
