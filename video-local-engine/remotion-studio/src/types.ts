export type Theme = 'studio' | 'christmas' | 'floral' | 'memorial' | 'wedding' | 'nursery' | 'coastal' | 'rustic' | 'sports';
export type ProductScene = {
  schemaVersion: 1;
  aspectRatio: '16:9' | '9:16';
  durationSeconds: number;
  fps: 24 | 30;
  resolution: 720 | 1080;
  seed: number;
  product: {src: string; sourceAspectRatio?: number; scale: number; anchor: 'hanging' | 'standing'; material: 'acrylic' | 'ceramic' | 'wood' | 'paper'};
  background: {src?: string; videoSrc?: string; videoDurationSeconds?: number; theme: Theme; palette?: [string, string, string]};
  motion: {orbitDegrees: number; swayDegrees: number; floatPixels: number; zoom: number; cycles: number; direction: 'left' | 'right'};
  lighting: {warmth: number; shadow: number; glint: number};
};
