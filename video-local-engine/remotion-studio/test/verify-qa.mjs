/** Real MP4 codec, full decode and temporal-variation proof; no model claims. */
import {spawnSync} from 'node:child_process';
import assert from 'node:assert/strict';
import {dirname, join, resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {writeFile} from 'node:fs/promises';
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const native = join(root, 'node_modules/@remotion/compositor-win32-x64-msvc');
const ffmpeg = process.env.MITOM_QA_FFMPEG ?? resolve(root, '../../build/video-ai-distribution/MITOM-Video-CPU-2026.10.23.1/ffmpeg/ffmpeg.exe');
const run = (exe, args) => {
  const result = spawnSync(exe === 'ffmpeg.exe' ? ffmpeg : join(native, exe), args, {encoding: 'utf8', windowsHide: true, maxBuffer: 4 * 1024 * 1024});
  if (result.error || result.status !== 0) throw result.error ?? new Error(result.stderr);
  return result.stdout;
};
const results = [];
for (const name of ['landscape', 'portrait', 'video-background']) {
  const file = join(root, `qa/${name === 'video-background' ? name : name + '-bt709'}.mp4`);
  const metadata = JSON.parse(run('ffprobe.exe', ['-v', 'error', '-count_frames', '-show_entries', 'stream=codec_type,codec_name,pix_fmt,width,height,r_frame_rate,nb_read_frames,duration', '-show_entries', 'format=duration', '-of', 'json', file]));
  assert.equal(metadata.streams.length, 1);
  const video = metadata.streams[0];
  assert.equal(video.codec_name, 'h264'); assert.equal(video.pix_fmt, 'yuv420p');
  assert.deepEqual([video.width, video.height], name === 'portrait' ? [1080, 1920] : [1280, 720]);
  assert.equal(video.r_frame_rate, '24/1'); assert.equal(Number(video.nb_read_frames), 120);
  assert.equal(Number(metadata.format.duration), 5);
  const decoded = run('ffmpeg.exe', ['-v', 'error', '-i', file, '-map', '0:v:0', '-f', 'framemd5', '-']);
  const hashes = decoded.split('\n').filter(line => line && !line.startsWith('#')).map(line => line.split(',').at(-1).trim());
  assert.equal(hashes.length, 120); assert.ok(new Set(hashes).size > 110);
  results.push({name, ...video, duration: Number(metadata.format.duration), decodedFrames: hashes.length, uniqueFrames: new Set(hashes).size, audioTracks: 0});
}
run('ffmpeg.exe', ['-v', 'error', '-y', '-loop', '1', '-framerate', '24', '-i', join(root, 'qa/background.png'), '-t', '2', '-vf', "zoompan=z='1.03+0.01*sin(on/16)':x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':d=1:s=1280x720:fps=24", '-c:v', 'libx264', '-pix_fmt', 'yuv420p', join(root, 'qa/background.mp4')]);
const backgroundScene = {schemaVersion: 1, durationSeconds: 5, fps: 24, resolution: 720, product: {src: 'product.png'}, background: {theme: 'christmas', videoSrc: 'background.mp4'}};
await writeFile(join(root, 'qa/video-background.json'), JSON.stringify(backgroundScene, null, 2));
await writeFile(join(root, 'qa/verification.json'), JSON.stringify({ok: true, remotionVersion: '4.0.539', actualModelInferenceClaimed: false, results}, null, 2) + '\n');
console.log(JSON.stringify({ok: true, results}, null, 2));
