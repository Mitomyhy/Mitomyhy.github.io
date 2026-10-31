import React from 'react';
import {AbsoluteFill, Img, useCurrentFrame, useVideoConfig} from 'remotion';
import {motionAtFrame} from './scene.mjs';
import {SceneBackground} from './SceneBackground';
import type {ProductScene as SceneProps} from './types';

export const ProductScene: React.FC<SceneProps> = (scene) => {
  const frame = useCurrentFrame();
  const {width, height} = useVideoConfig();
  const m = motionAtFrame(frame, scene);
  const portrait = height > width;
  const requestedHeight = height * scene.product.scale * (portrait ? .82 : .94);
  const sourceAspect = scene.product.sourceAspectRatio ?? 1;
  const productWidth = Math.min(width * (portrait ? .9 : .67), requestedHeight * sourceAspect);
  const productHeight = productWidth / sourceAspect;
  const centerY = portrait ? height * .5 : height * .48;
  const brightness = 1 + Math.cos(m.yaw * Math.PI / 180) * .018;
  const shadowWidth = productWidth * (.42 + Math.abs(m.yaw) * .0015);
  const edgeColor = {acrylic: '#d7e2dd', ceramic: '#e4dfd5', wood: '#a87b4c', paper: '#e5ddcf'}[scene.product.material];
  const thickness = productHeight * (scene.product.material === 'paper' ? .001 : scene.product.material === 'wood' ? .008 : .005);
  const mask = {WebkitMaskImage: `url("${scene.product.src}")`, WebkitMaskSize: 'contain', WebkitMaskRepeat: 'no-repeat', WebkitMaskPosition: 'center', maskImage: `url("${scene.product.src}")`, maskSize: 'contain', maskRepeat: 'no-repeat', maskPosition: 'center'} as const;
  return <AbsoluteFill style={{overflow: 'hidden', backgroundColor: '#e9e3d8'}}>
    <SceneBackground scene={scene} phase={m.phase} parallaxX={m.backgroundX}/>
    {scene.product.anchor === 'hanging' && <svg width={width} height={height} style={{position: 'absolute', inset: 0, pointerEvents: 'none'}}><path d={`M ${width/2} -12 Q ${width/2 + m.x*.2} ${centerY*.25} ${width/2 + m.x + Math.sin(m.roll*Math.PI/180)*productHeight*.08} ${centerY-productHeight*m.scale*.5+m.y+12}`} fill="none" stroke="#efede5" strokeWidth={Math.max(3, productHeight*.009)} opacity=".94"/><path d={`M ${width/2+2} -12 Q ${width/2 + m.x*.2+2} ${centerY*.25} ${width/2 + m.x+2 + Math.sin(m.roll*Math.PI/180)*productHeight*.08} ${centerY-productHeight*m.scale*.5+m.y+12}`} fill="none" stroke="#ffffff" strokeWidth={Math.max(1, productHeight*.002)} opacity=".55"/></svg>}
    <div style={{position: 'absolute', left: '50%', top: centerY + productHeight * .52 + 8, width: shadowWidth, height: productHeight * .045, background: '#1c211d', borderRadius: '50%', opacity: scene.lighting.shadow * .23, filter: `blur(${productHeight * .024}px)`, transform: `translateX(calc(-50% + ${-m.x * .7}px)) scaleX(${1 - Math.abs(m.yaw) * .005})`}}/>
    <div style={{position: 'absolute', left: '50%', top: centerY, width: productWidth, height: productHeight, transform: `translate(-50%, -50%) translate(${m.x}px, ${m.y}px) scale(${m.scale})`, perspective: productHeight * 3.2}}>
      <div style={{position: 'absolute', inset: 0, transformOrigin: scene.product.anchor === 'hanging' ? '50% 8%' : '50% 92%', transform: `rotateY(${m.yaw}deg) rotateX(${m.pitch}deg) rotateZ(${m.roll}deg)`, transformStyle: 'preserve-3d'}}>
        {[3,2,1].map(layer => <div key={layer} style={{position: 'absolute', inset: 0, ...mask, backgroundColor: edgeColor, opacity: scene.product.material === 'acrylic' ? .68 : 1, transform: `translateZ(${-thickness*layer/3}px)`, filter: layer === 1 ? 'brightness(1.12)' : 'brightness(.9)'}}/>)}
        <Img src={scene.product.src} style={{width: '100%', height: '100%', objectFit: 'contain', filter: `drop-shadow(${-m.yaw*.1}px 3px 2px rgba(20,20,18,.14)) brightness(${brightness})`, backfaceVisibility: 'hidden'}}/>
        <div style={{position: 'absolute', inset: 0, pointerEvents: 'none', opacity: m.highlight * .13, background: `linear-gradient(${110 + m.yaw}deg, transparent 20%, rgba(255,255,255,.9) 48%, transparent 66%)`, WebkitMaskImage: `url("${scene.product.src}")`, WebkitMaskSize: 'contain', WebkitMaskRepeat: 'no-repeat', WebkitMaskPosition: 'center', maskImage: `url("${scene.product.src}")`, maskSize: 'contain', maskRepeat: 'no-repeat', maskPosition: 'center'}}/>
        {scene.lighting.warmth !== 0 && <div style={{position: 'absolute', inset: 0, pointerEvents: 'none', opacity: Math.abs(scene.lighting.warmth) * .035, backgroundColor: scene.lighting.warmth > 0 ? '#ffd6a5' : '#badfff', WebkitMaskImage: `url("${scene.product.src}")`, WebkitMaskSize: 'contain', WebkitMaskRepeat: 'no-repeat', WebkitMaskPosition: 'center'}}/>}
      </div>
    </div>
  </AbsoluteFill>;
};
