import React from 'react';
import {Composition, registerRoot} from 'remotion';
import {ProductScene} from './ProductScene';
import {sceneDimensions} from './scene.mjs';
import type {ProductScene as SceneProps} from './types';

const defaultProps: SceneProps = {
  schemaVersion: 1, aspectRatio: '16:9', durationSeconds: 8, fps: 30, resolution: 1080, seed: 42,
  product: {src: '', scale: .76, anchor: 'hanging', material: 'acrylic'}, background: {theme: 'studio'},
  motion: {orbitDegrees: 38, swayDegrees: 3, floatPixels: 9, zoom: .045, cycles: 1, direction: 'left'},
  lighting: {warmth: .18, shadow: .55, glint: .2},
};

const Root: React.FC = () => <Composition id="ProductListing" component={ProductScene} width={1920} height={1080} fps={30} durationInFrames={240} defaultProps={defaultProps} calculateMetadata={({props}) => ({...sceneDimensions(props), fps: props.fps, durationInFrames: Math.round(props.durationSeconds * props.fps)})}/>;
registerRoot(Root);
